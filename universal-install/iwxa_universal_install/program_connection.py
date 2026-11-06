"""Ephemeral, read-only discovery and CLI capability connection for multiple hosts."""
import hashlib
import os
import secrets
from pathlib import Path
import re
import shutil
import subprocess
import threading
import time

SCHEMA = "IWXA_PROGRAM_CONNECTION/1.0.0"

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
    for args in [data.get("version_args"), *ops.values()]:
        if not isinstance(args, list) or len(args) > 24 or any(
            not isinstance(x, str) or len(x) > 2048 or "\0" in x for x in args
        ):
            raise ValueError("CLI_ARGUMENTS_INVALID")
    return data

def bind_arguments(args, params):
    """Replace only declared whole-argument placeholders; never use a shell."""
    if params is None:
        params = {}
    if not isinstance(params, dict) or len(params) > 8:
        raise ProgramError("CLI_INPUT_INVALID")
    def key(arg):
        match = re.fullmatch(r"(?:[a-z][a-z0-9_-]*=)?\{([a-z][a-z0-9_]*)\}", arg)
        return match.group(1) if match else None
    declared = {key(arg) for arg in args if key(arg) is not None}
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


def run_command(path, args):
    """Run a native CLI with strict per-stream output limits on all hosts."""
    flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
    try:
        child = subprocess.Popen(
            [path, *args], shell=False, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, creationflags=flags,
        )
    except OSError:
        raise ProgramError("CLI_EXECUTION_FAILED") from None

    too_large = threading.Event()
    stream_failed = threading.Event()
    buffers = [bytearray(), bytearray()]

    def collect(stream, buffer):
        try:
            while chunk := stream.read(8192):
                buffer.extend(chunk)
                if len(buffer) > 131072:
                    too_large.set()
                    break
        except OSError:
            stream_failed.set()

    streams = (child.stdout, child.stderr)
    readers = [threading.Thread(target=collect, args=(stream, buffer), daemon=True)
               for stream, buffer in zip(streams, buffers)]
    for reader in readers:
        reader.start()
    deadline = time.monotonic() + 20
    try:
        while child.poll() is None:
            if too_large.is_set():
                raise ProgramError("CLI_OUTPUT_TOO_LARGE")
            if stream_failed.is_set():
                raise ProgramError("CLI_EXECUTION_FAILED")
            if time.monotonic() >= deadline:
                raise ProgramError("CLI_TIMED_OUT")
            time.sleep(0.01)
        for reader in readers:
            reader.join(timeout=max(0, deadline - time.monotonic()))
        if too_large.is_set():
            raise ProgramError("CLI_OUTPUT_TOO_LARGE")
        if any(reader.is_alive() for reader in readers):
            raise ProgramError("CLI_TIMED_OUT")
        if stream_failed.is_set():
            raise ProgramError("CLI_EXECUTION_FAILED")
        return child.returncode, buffers[0].decode("utf-8", errors="replace")
    finally:
        if child.poll() is None:
            child.kill()
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
        expected = self.spec.get("executable_sha256")
        if expected is None:
            return {"installation_verified": False, "installation_reason": "PIN_NOT_PROVIDED"}
        try:
            before = os.stat(path, follow_symlinks=False)
            if before.st_size > 1_073_741_824:
                return {"installation_verified": False, "installation_reason": "BINARY_TOO_LARGE"}
            digest = hashlib.sha256()
            with open(path, "rb") as stream:
                for part in iter(lambda: stream.read(65536), b""):
                    digest.update(part)
            after = os.stat(path, follow_symlinks=False)
            if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (
                after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns
            ):
                return {"installation_verified": False,
                        "installation_reason": "INSTALLATION_CHANGED_DURING_READBACK"}
            value = digest.hexdigest()
        except OSError:
            return {"installation_verified": False,
                    "installation_reason": "INSTALLATION_READBACK_FAILED"}
        if value != expected:
            return {"installation_verified": False,
                    "installation_reason": "EXECUTABLE_DIGEST_MISMATCH"}
        return {"installation_verified": True, "installation_identity": {
            "program_id": self.spec["program_id"], "executable": str(path),
            "sha256": value, "identity_source": "PINNED_BINARY_SHA256_READBACK",
            "verified": True}}


    def inspect(self):
        """Never runs the program or modifies its installation."""
        path = self.requested or shutil.which(self.spec["executable_name"])
        if path:
            file = Path(path)
            if file.is_file() and not file.is_symlink() and (
                os.name == "nt" or os.access(file, os.X_OK)
            ):
                resolved = str(file.resolve())
                identity = self.installation_identity(resolved)
                return self.observation(
                    "INSTALLATION_MISMATCH" if self.spec.get("executable_sha256") and
                    not identity["installation_verified"] else "DISCOVERED",
                    installed=True, path=resolved, connected=False,
                    architecture="UNKNOWN", ownership="UNKNOWN",
                    interfaces=["cli"], **identity, **self.process_observation())
        return self.observation("NOT_FOUND", installed=False, connected=False,
                                architecture="UNKNOWN", ownership="UNKNOWN",
                                interfaces=[], installation_verified=False,
                                **self.process_observation())

    def connect(self):
        found = self.inspect()
        if found["status"] != "DISCOVERED":
            self.disconnect()
            self._unavailable = found["status"]
            return found
        if self.spec.get("requires_running") and found.get("running") is not True:
            self.disconnect()
            status = ("PROCESS_NOT_RUNNING" if found.get("running") is False
                      else "RUNNING_PROCESS_UNVERIFIED")
            self._unavailable = status
            return self.observation(status, installed=True, connected=False,
                                    **self.process_observation())
        identity = executable_identity(found["path"])
        if identity is None:
            self.disconnect()
            return self.observation("NOT_FOUND", installed=False, connected=False)
        if self.path != found["path"] or self._identity != identity:
            self._verified.clear()
            self._epoch = None
        try:
            code, version = run_command(found["path"], self.spec["version_args"])
        except ProgramError as error:
            self.disconnect()
            return self.observation(error.code, installed=True, connected=False)
        if code or not version_matches(version, self.spec["version_contains"]):
            self.disconnect()
            return self.observation("VERSION_UNVERIFIED", installed=True,
                                    connected=False)
        self.path = found["path"]
        self._identity = identity
        if self._epoch is None:
            self._epoch = secrets.token_hex(16)
        self._unavailable = None
        return self.observation("CONNECTED", installed=True, connected=True,
                                path=self.path, version=version.strip()[:256],
                                **self.installation_identity(self.path),
                                **self.process_observation())

    def verify(self):
        if not self.path:
            return self.observation("NOT_CONNECTED", connected=False)
        if executable_identity(self.path) != self._identity:
            self.disconnect()
            return self.observation("CONNECTION_LOST", connected=False)
        return self.connect()

    def capabilities(self):
        lost = bool(self.path) and executable_identity(self.path) != self._identity
        if lost:
            self.disconnect()
        identity = self.installation_identity(self.path) if self.path else {}
        mismatch = bool(self.path) and bool(self.spec.get("executable_sha256")) and not (
            identity["installation_verified"])
        running = self.process_observation()
        process_lost = bool(self.path) and bool(self.spec.get("requires_running")) and (
            running["running"] is not True)
        if mismatch or process_lost:
            self.disconnect()
        if process_lost:
            self._unavailable = ("PROCESS_NOT_RUNNING" if running["running"] is False
                                 else "RUNNING_PROCESS_UNVERIFIED")
        return self.observation(
            "CONNECTION_LOST" if lost else "INSTALLATION_MISMATCH" if mismatch else
            self._unavailable if process_lost else
            "DECLARED" if self.path else "NOT_CONNECTED",
            connected=bool(self.path), **identity, **running,
            **({"unavailable_reason": self._unavailable} if self._unavailable else {}),
            operations=[dict(name=name, declared=True, verified=name in self._verified)
                        for name in sorted(self.spec["operations"])])

    def invoke(self, operation, params=None):
        if not self.path:
            return self.observation("NOT_CONNECTED", executed=False)
        if executable_identity(self.path) != self._identity:
            self.disconnect()
            return self.observation("CONNECTION_LOST", executed=False)
        if self.spec.get("executable_sha256") and not (
            self.installation_identity(self.path)["installation_verified"]
        ):
            self.disconnect()
            self._unavailable = "INSTALLATION_MISMATCH"
            return self.observation("INSTALLATION_MISMATCH", executed=False)
        if self.spec.get("requires_running"):
            observation = self.process_observation()
            if observation["running"] is not True:
                self.disconnect()
                status = ("PROCESS_NOT_RUNNING" if observation["running"] is False
                          else "RUNNING_PROCESS_UNVERIFIED")
                self._unavailable = status
                return self.observation(status, executed=False, **observation)
        args = self.spec["operations"].get(operation)
        if args is None:
            return self.observation("OPERATION_UNSUPPORTED", executed=False)
        start = time.perf_counter()
        try:
            bound = bind_arguments(args, params)
            code, output = run_command(self.path, bound)
        except ProgramError as e:
            return self.observation(e.code, executed=False)
        if code:
            return self.observation("EXECUTION_FAILED", executed=False,
                                    exit_code=code)
        self._verified.add(operation)
        return self.observation("EXECUTED", executed=True, operation=operation,
                                **self.installation_identity(self.path),
                                **self.process_observation(), output=output,
                                elapsed_ms=round((time.perf_counter()-start)*1000, 3))

    def disconnect(self):
        self.path = None
        self._identity = None
        self._epoch = None
        self._verified.clear()
        return self.observation("DISCONNECTED", connected=False)

    def recover(self):
        return self.connect()
