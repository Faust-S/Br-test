"""Ephemeral, read-only discovery and CLI capability connection for multiple hosts."""
import hashlib
import os
import secrets
import signal
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time

SCHEMA = "IWXA_PROGRAM_CONNECTION/1.0.0"
DEFAULT_OUTPUT_BYTES = 131072
MAX_APPROVED_OUTPUT_BYTES = 4 * 1024 * 1024

class ProgramError(RuntimeError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)

def validate_spec(data):
    if not isinstance(data, dict) or data.get("schema") != "IWXA_CLI_PROGRAM/1":
        raise ValueError("CLI_SPEC_INVALID")
    if not re.fullmatch(r"[A-Z][A-Z0-9_]{2,79}", str(data.get("program_id", ""))):
        raise ValueError("CLI_PROGRAM_INVALID")
    name = data.get("executable_name")
    if not isinstance(name, str) or not name or "/" in name or "\\" in name:
        raise ValueError("CLI_EXECUTABLE_INVALID")
    if not isinstance(data.get("version_contains"), str) or not data["version_contains"]:
        raise ValueError("CLI_VERSION_INVALID")
    ops = data.get("operations")
    if not isinstance(ops, dict) or not ops:
        raise ValueError("CLI_OPERATIONS_INVALID")
    expected = data.get("executable_sha256")
    if expected is not None and (
        not isinstance(expected, str) or re.fullmatch(r"[a-f0-9]{64}", expected) is None
    ):
        raise ValueError("CLI_DIGEST_INVALID")
    if "requires_running" in data and not isinstance(data["requires_running"], bool):
        raise ValueError("CLI_RUNNING_POLICY_INVALID")
    limit = data.get("max_output_bytes", DEFAULT_OUTPUT_BYTES)
    if type(limit) is not int or not 1024 <= limit <= MAX_APPROVED_OUTPUT_BYTES:
        raise ValueError("CLI_OUTPUT_LIMIT_INVALID")
    for args in [data.get("version_args"), *ops.values()]:
        if not isinstance(args, list) or len(args) > 24 or any(
            not isinstance(x, str) or len(x) > 2048 or "\0" in x for x in args
        ):
            raise ValueError("CLI_ARGUMENTS_INVALID")
    return data

def argument_parameter_names(args):
    """Names exposed by a reviewed CLI argv template, for product-host forms."""
    if not isinstance(args, list) or any(not isinstance(arg, str) for arg in args):
        raise ValueError("CLI_ARGUMENTS_INVALID")
    names = set()
    for arg in args:
        match = re.fullmatch(r"(?:[a-z][a-z0-9_-]*=)?\{([a-z][a-z0-9_]*)\}", arg)
        if match:
            names.add(match.group(1))
    return sorted(names)


def bind_arguments(args, params):
    """Replace only declared whole-argument placeholders; never use a shell."""
    if params is None:
        params = {}
    if not isinstance(params, dict) or len(params) > 8:
        raise ProgramError("CLI_INPUT_INVALID")
    def key(arg):
        match = re.fullmatch(r"(?:[a-z][a-z0-9_-]*=)?\{([a-z][a-z0-9_]*)\}", arg)
        return match.group(1) if match else None
    declared = set(argument_parameter_names(args))
    if set(params) != declared or any(
        not isinstance(value, str) or len(value) > 16384 or "\0" in value
        for value in params.values()
    ):
        raise ProgramError("CLI_INPUT_INVALID")
    return [((arg.partition("=")[0] + "=") if not arg.startswith("{") else "") +
            params[key(arg)] if key(arg) is not None else arg for arg in args]


def version_matches(output, target):
    """Exact version token, not a prefix of a newer package version."""
    return re.search(r"(?<![A-Za-z0-9._+-])" + re.escape(target)
                     + r"(?![A-Za-z0-9._+-])", output) is not None

def executable_identity(path):
    """Local observation only, never a host-owned install receipt."""
    try:
        st = os.stat(path, follow_symlinks=False)
        return (st.st_dev, st.st_ino, st.st_size, st.st_mtime_ns)
    except OSError:
        return None


def _terminate_cli_tree(child):
    """Best-effort cleanup only for abnormal execution, never successful apps.

    POSIX commands get their own process group. On Windows, taskkill /T
    requests recursive termination while the original child is alive.
    """
    if os.name == "nt":
        if child.poll() is None:
            try:
                subprocess.run(
                    ["taskkill.exe", "/PID", str(child.pid), "/T", "/F"],
                    shell=False, stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    creationflags=subprocess.CREATE_NO_WINDOW, timeout=3,
                    check=False,
                )
            except (OSError, subprocess.TimeoutExpired):
                pass
    else:
        try:
            os.killpg(child.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except OSError:
            pass
    if child.poll() is None:
        try:
            child.kill()
        except ProcessLookupError:
            pass


def run_command(path, args, *, cancel_event=None, timeout_seconds=20,
                max_output_bytes=DEFAULT_OUTPUT_BYTES):
    """Run one native child, bounded by host cancellation, time and stream bytes."""
    if (type(timeout_seconds) not in (int, float) or not
            0.05 <= timeout_seconds <= 20):
        raise ProgramError("CLI_INPUT_INVALID")
    if type(max_output_bytes) is not int or not 1024 <= max_output_bytes <= MAX_APPROVED_OUTPUT_BYTES:
        raise ProgramError("CLI_INPUT_INVALID")
    if cancel_event is not None and cancel_event.is_set():
        raise ProgramError("CLI_CANCELLED_BEFORE_START")
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        child = subprocess.Popen(
            [path, *args], shell=False, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=flags,
            start_new_session=(os.name != "nt"),
        )
    except OSError:
        raise ProgramError("CLI_EXECUTION_FAILED") from None

    too_large = threading.Event()
    stream_failed = threading.Event()
    buffers = [bytearray(), bytearray()]

    def collect(stream, buffer, limit):
        try:
            while chunk := stream.read(8192):
                buffer.extend(chunk)
                if len(buffer) > limit:
                    too_large.set()
                    break
        except OSError:
            stream_failed.set()

    streams = (child.stdout, child.stderr)
    readers = [threading.Thread(target=collect, args=(stream, buffer, limit), daemon=True)
               for stream, buffer, limit in zip(
                   streams, buffers, (max_output_bytes, DEFAULT_OUTPUT_BYTES))]
    for reader in readers:
        reader.start()
    deadline = time.monotonic() + timeout_seconds
    completed = False
    try:
        while child.poll() is None:
            if cancel_event is not None and cancel_event.is_set():
                raise ProgramError("CLI_CANCELLED")
            if too_large.is_set():
                raise ProgramError("CLI_OUTPUT_TOO_LARGE")
            if stream_failed.is_set():
                raise ProgramError("CLI_EXECUTION_FAILED")
            if time.monotonic() >= deadline:
                raise ProgramError("CLI_TIMED_OUT")
            time.sleep(0.01)
        for reader in readers:
            reader.join(timeout=max(0, deadline - time.monotonic()))
        if cancel_event is not None and cancel_event.is_set():
            raise ProgramError("CLI_CANCELLED")
        if too_large.is_set():
            raise ProgramError("CLI_OUTPUT_TOO_LARGE")
        if any(reader.is_alive() for reader in readers):
            raise ProgramError("CLI_TIMED_OUT")
        if stream_failed.is_set():
            raise ProgramError("CLI_EXECUTION_FAILED")
        try:
            output = buffers[0].decode("utf-8", errors="strict")
        except UnicodeDecodeError:
            raise ProgramError("CLI_OUTPUT_ENCODING_UNSUPPORTED") from None
        completed = True
        return child.returncode, output
    finally:
        if not completed:
            _terminate_cli_tree(child)
        child.wait()
        for stream, reader in zip(streams, readers):
            reader.join(timeout=0.2)
            if not reader.is_alive():
                stream.close()

class CliConnection:
    """Host owns executable selection, consent, receipts, storage and install."""
    def __init__(self, spec, executable=None, *, process_probe=None):
        self.spec = validate_spec(spec)
        self.requested = executable
        self.path = None
        self._identity = None
        self._verified = set()
        self._epoch = None
        self._process_probe = process_probe
        self._unavailable = None
        self._process_ref = None
        self._pin_cache = None  # (path, filesystem-identity, approved SHA)
        self._invocation_lock = threading.Lock()
        self._state_lock = threading.RLock()

    def observation(self, status, **extra):
        epoch = ({"connection_epoch": self._epoch} if self._epoch and
                 status in ("CONNECTED", "DECLARED", "EXECUTED") else {})
        return dict(schema=SCHEMA, program_id=self.spec["program_id"],
                    transport="cli", status=status, **epoch, **extra)

    def process_observation(self):
        if not callable(self._process_probe):
            return {"process": "UNKNOWN", "running": None}
        try:
            proof = self._process_probe(self.spec["program_id"])
        except Exception:
            return {"process": "UNKNOWN", "running": None}
        if not isinstance(proof, dict) or proof.get("verified") is not True or (
            proof.get("program_id") != self.spec["program_id"]
        ):
            return {"process": "UNKNOWN", "running": None}
        if proof.get("status") == "NOT_RUNNING":
            return {"process": "NOT_RUNNING", "running": False}
        pid, stamp = proof.get("pid"), proof.get("processStamp")
        if (proof.get("status") != "RUNNING" or proof.get("spawned") is not False or
                type(pid) is not int or pid <= 0 or not isinstance(stamp, str) or
                not 8 <= len(stamp) <= 256 or any(ord(char) < 32 for char in stamp)):
            return {"process": "UNKNOWN", "running": None}
        return {"process": "RUNNING", "running": True, "process_id": pid,
                "process_stamp": stamp, "process_proof": "HOST_OS_OBSERVATION"}

    def installation_identity(self, path):
        """Read the approved binary digest once per unchanged file identity.

        Capability polling is common in Agent 0.3; re-reading an entire
        executable on every poll is unnecessary. Explicit verify() rehashes.
        """
        expected = self.spec.get("executable_sha256")
        if expected is None:
            return {"installation_verified": False, "installation_reason": "PIN_NOT_PROVIDED"}
        identity = executable_identity(path)
        if identity is None:
            self._pin_cache = None
            return {"installation_verified": False,
                    "installation_reason": "INSTALLATION_READBACK_FAILED"}
        cache_key = (str(path), identity, expected)
        if self._pin_cache == cache_key:
            value = expected
        else:
            self._pin_cache = None
            try:
                before = os.stat(path, follow_symlinks=False)
                if before.st_size > 1_073_741_824:
                    return {"installation_verified": False,
                            "installation_reason": "BINARY_TOO_LARGE"}
                digest = hashlib.sha256()
                with open(path, "rb") as stream:
                    for part in iter(lambda: stream.read(65536), b""):
                        digest.update(part)
                after = executable_identity(path)
                if after != identity:
                    return {"installation_verified": False,
                            "installation_reason": "INSTALLATION_CHANGED_DURING_READBACK"}
                value = digest.hexdigest()
            except OSError:
                return {"installation_verified": False,
                        "installation_reason": "INSTALLATION_READBACK_FAILED"}
            if value != expected:
                return {"installation_verified": False,
                        "installation_reason": "EXECUTABLE_DIGEST_MISMATCH"}
            self._pin_cache = cache_key
        return {"installation_verified": True, "installation_identity": {
            "program_id": self.spec["program_id"], "executable": str(path),
            "sha256": value, "identity_source": "PINNED_BINARY_SHA256_READBACK",
            "verified": True}}

    @staticmethod
    def _process_key(observation):
        if observation.get("running") is not True:
            return None
        return observation["process_id"], observation["process_stamp"]

    @staticmethod
    def _process_reason(observation):
        if observation.get("running") is False:
            return "PROCESS_NOT_RUNNING"
        return "RUNNING_PROCESS_UNVERIFIED"


    def inspect(self, *, verify_pin=True):
        """Observe a program without launching it.

        Bulk inventory may defer a costly executable digest. Explicit connect,
        verify and invoke always revalidate the original owner identity.
        """
        if type(verify_pin) is not bool:
            raise ValueError("CLI_VERIFY_PIN_INVALID")
        path = self.requested or shutil.which(self.spec["executable_name"])
        if path:
            file = Path(path)
            if file.is_file() and not file.is_symlink() and (
                os.name == "nt" or os.access(file, os.X_OK)
            ):
                resolved = str(file.resolve())
                identity = (self.installation_identity(resolved) if verify_pin else {
                    "installation_verified": False,
                    "installation_reason": ("PIN_CHECK_DEFERRED" if
                                            self.spec.get("executable_sha256")
                                            else "PIN_NOT_PROVIDED"),
                })
                return self.observation(
                    "INSTALLATION_MISMATCH" if verify_pin and self.spec.get("executable_sha256") and
                    not identity["installation_verified"] else "DISCOVERED",
                    installed=True, path=resolved, connected=False,
                    architecture="UNKNOWN", ownership="UNKNOWN",
                    interfaces=["cli"], **identity, **self.process_observation())
        return self.observation("NOT_FOUND", installed=False, connected=False,
                                architecture="UNKNOWN", ownership="UNKNOWN",
                                interfaces=[], installation_verified=False,
                                **self.process_observation())

    def connect(self):
        with self._state_lock:
            found = self.inspect()
            if found["status"] != "DISCOVERED":
                self.disconnect()
                self._unavailable = found["status"]
                return found
            required = self.spec.get("requires_running")
            initial_process = self._process_key(found) if required else None
            if required and initial_process is None:
                self.disconnect()
                status = self._process_reason(found)
                self._unavailable = status
                return self.observation(status, installed=True, connected=False,
                                        **self.process_observation())
            identity = executable_identity(found["path"])
            if identity is None:
                self.disconnect()
                return self.observation("NOT_FOUND", installed=False, connected=False)
            if (self.path != found["path"] or self._identity != identity or
                    (required and self._process_ref != initial_process)):
                self._verified.clear()
                self._epoch = None
            try:
                code, version = run_command(found["path"], self.spec["version_args"])
            except ProgramError as error:
                self.disconnect()
                self._unavailable = error.code
                return self.observation(error.code, installed=True, connected=False)
            if code or not version_matches(version, self.spec["version_contains"]):
                self.disconnect()
                self._unavailable = "VERSION_UNVERIFIED"
                return self.observation("VERSION_UNVERIFIED", installed=True, connected=False)
            if executable_identity(found["path"]) != identity:
                self.disconnect()
                self._unavailable = "CONNECTION_LOST"
                return self.observation("CONNECTION_LOST", installed=True, connected=False)
            if required:
                current = self.process_observation()
                if self._process_key(current) != initial_process:
                    self.disconnect()
                    status = ("PROCESS_CHANGED" if self._process_key(current) is not None
                              else self._process_reason(current))
                    self._unavailable = status
                    return self.observation(status, installed=True, connected=False, **current)
            installation = self.installation_identity(found["path"])
            if self.spec.get("executable_sha256") and not installation["installation_verified"]:
                self.disconnect()
                self._unavailable = "INSTALLATION_MISMATCH"
                return self.observation("INSTALLATION_MISMATCH", installed=True, connected=False)
            self.path = found["path"]
            self._identity = identity
            self._process_ref = initial_process
            if self._epoch is None:
                self._epoch = secrets.token_hex(16)
            self._unavailable = None
            return self.observation("CONNECTED", installed=True, connected=True,
                                    path=self.path, version=version.strip()[:256],
                                    **installation, **self.process_observation())

    def verify(self):
        with self._state_lock:
            if not self.path:
                return self.observation("NOT_CONNECTED", connected=False)
            if executable_identity(self.path) != self._identity:
                self.disconnect()
                return self.observation("CONNECTION_LOST", connected=False)
            self._pin_cache = None  # Explicit verify always rehashes the approved file.
            return self.connect()

    def capabilities(self):
        with self._state_lock:
            lost = bool(self.path) and executable_identity(self.path) != self._identity
            if lost:
                self.disconnect()
            installation = self.installation_identity(self.path) if self.path else {}
            mismatch = bool(self.path) and bool(self.spec.get("executable_sha256")) and not (
                installation["installation_verified"])
            running = self.process_observation()
            process_status = None
            if self.path and self.spec.get("requires_running"):
                observed_key = self._process_key(running)
                process_status = ("PROCESS_CHANGED" if observed_key is not None
                                  else self._process_reason(running)) if (
                                  observed_key != self._process_ref) else None
            if mismatch or process_status:
                self.disconnect()
                self._unavailable = "INSTALLATION_MISMATCH" if mismatch else process_status
            status = ("CONNECTION_LOST" if lost else
                      "INSTALLATION_MISMATCH" if mismatch else
                      process_status if process_status else
                      "DECLARED" if self.path else "NOT_CONNECTED")
            return self.observation(
                status, connected=bool(self.path), **installation, **running,
                **({"unavailable_reason": self._unavailable} if self._unavailable else {}),
                operations=[dict(name=name, declared=True, verified=name in self._verified)
                            for name in sorted(self.spec["operations"])])

    def invoke(self, operation, params=None, *, cancel_event=None, timeout_seconds=20):
        if not self._invocation_lock.acquire(blocking=False):
            return self.observation("CLI_BUSY", executed=False, executionOutcome="NONE")
        try:
            return self._invoke_owned(operation, params, cancel_event=cancel_event,
                                      timeout_seconds=timeout_seconds)
        finally:
            self._invocation_lock.release()

    def _invoke_owned(self, operation, params=None, *, cancel_event=None,
                      timeout_seconds=20):
        with self._state_lock:
            # A denied preflight must not be classified as an unknown external effect.
            if not self.path:
                return self.observation("NOT_CONNECTED", executed=False,
                                        executionOutcome="NONE",
                                        **({"unavailable_reason": self._unavailable}
                                           if self._unavailable else {}))
            if executable_identity(self.path) != self._identity:
                self.disconnect()
                return self.observation("CONNECTION_LOST", executed=False, executionOutcome="NONE")
            if self.spec.get("executable_sha256") and not (
                self.installation_identity(self.path)["installation_verified"]
            ):
                self.disconnect()
                self._unavailable = "INSTALLATION_MISMATCH"
                return self.observation("INSTALLATION_MISMATCH", executed=False,
                                        executionOutcome="NONE")
            if self.spec.get("requires_running"):
                running = self.process_observation()
                current_key = self._process_key(running)
                if current_key != self._process_ref:
                    status = ("PROCESS_CHANGED" if current_key is not None
                              else self._process_reason(running))
                    self.disconnect()
                    self._unavailable = status
                    return self.observation(status, executed=False,
                                            executionOutcome="NONE", **running)
            args = self.spec["operations"].get(operation)
            if args is None:
                return self.observation("OPERATION_UNSUPPORTED", executed=False,
                                        executionOutcome="NONE")
            try:
                bound = bind_arguments(args, params)
            except ProgramError as exc:
                return self.observation(exc.code, executed=False, executionOutcome="NONE")
            if cancel_event is not None and cancel_event.is_set():
                return self.observation("CLI_CANCELLED", executed=False, executionOutcome="NONE")
            opened_epoch = self._epoch
            opened_path = self.path
            opened_identity = self._identity
        start = time.perf_counter()
        try:
            code, output = run_command(opened_path, bound, cancel_event=cancel_event,
                                       timeout_seconds=timeout_seconds,
                                       max_output_bytes=self.spec.get("max_output_bytes", DEFAULT_OUTPUT_BYTES))
        except ProgramError as exc:
            with self._state_lock:
                if (self._epoch != opened_epoch or self.path != opened_path or
                        self._identity != opened_identity):
                    return self.observation("CONNECTION_SUPERSEDED", executed=False,
                                            executionOutcome="NONE" if exc.code in
                                            ("CLI_INPUT_INVALID", "CLI_CANCELLED_BEFORE_START")
                                            else "UNKNOWN")
                if exc.code not in ("CLI_CANCELLED_BEFORE_START", "CLI_INPUT_INVALID"):
                    self._verified.discard(operation)
                return self.observation("CLI_CANCELLED" if exc.code == "CLI_CANCELLED_BEFORE_START"
                                        else exc.code, executed=False,
                                        executionOutcome="NONE" if exc.code in
                                        ("CLI_CANCELLED_BEFORE_START", "CLI_INPUT_INVALID")
                                        else "UNKNOWN")
        with self._state_lock:
            if (self._epoch != opened_epoch or self.path != opened_path or
                    self._identity != opened_identity):
                return self.observation("CONNECTION_SUPERSEDED", executed=False,
                                        executionOutcome="UNKNOWN")
            if code:
                self._verified.discard(operation)
                return self.observation("EXECUTION_FAILED", executed=False,
                                        executionOutcome="UNKNOWN", exit_code=code)
            installation = self.installation_identity(self.path)
            if self.spec.get("executable_sha256") and not installation["installation_verified"]:
                self.disconnect()
                self._unavailable = "INSTALLATION_MISMATCH"
                return self.observation("INSTALLATION_MISMATCH", executed=False,
                                        executionOutcome="UNKNOWN")
            process = self.process_observation()
            if self.spec.get("requires_running") and self._process_key(process) != self._process_ref:
                status = ("PROCESS_CHANGED" if self._process_key(process) is not None
                          else self._process_reason(process))
                self.disconnect()
                self._unavailable = status
                return self.observation(status, executed=False,
                                        executionOutcome="UNKNOWN", **process)
            self._verified.add(operation)
            return self.observation("EXECUTED", executed=True, operation=operation,
                                    executionOutcome="ACKNOWLEDGED",
                                    **installation, **process, output=output,
                                    elapsed_ms=round((time.perf_counter()-start)*1000, 3))

    def disconnect(self):
        with self._state_lock:
            self.path = None
            self._identity = None
            self._process_ref = None
            self._pin_cache = None
            self._epoch = None
            self._verified.clear()
            return self.observation("DISCONNECTED", connected=False)

    def recover(self):
        return self.connect()
