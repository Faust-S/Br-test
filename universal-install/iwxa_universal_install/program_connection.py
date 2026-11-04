"""Ephemeral, read-only discovery and CLI capability connection for multiple hosts."""
import os
from pathlib import Path
import re
import shutil
import subprocess
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
    declared = {arg[1:-1] for arg in args if re.fullmatch(r"\{[a-z][a-z0-9_]*\}", arg)}
    if set(params) != declared or any(
        not isinstance(value, str) or len(value) > 16384 or "\0" in value
        for value in params.values()
    ):
        raise ProgramError("CLI_INPUT_INVALID")
    return [params[arg[1:-1]] if arg[1:-1] in declared and
            re.fullmatch(r"\{[a-z][a-z0-9_]*\}", arg) else arg for arg in args]


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
    try:
        # A user-facing Windows host must not flash a console when executing
        # background CLI commands such as jq or yq.
        flags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        result = subprocess.run([path, *args], shell=False, capture_output=True,
                                timeout=20, check=False, creationflags=flags)
    except subprocess.TimeoutExpired:
        raise ProgramError("CLI_TIMED_OUT") from None
    except OSError:
        raise ProgramError("CLI_EXECUTION_FAILED") from None
    if len(result.stdout) > 131072 or len(result.stderr) > 131072:
        raise ProgramError("CLI_OUTPUT_TOO_LARGE")
    return result.returncode, result.stdout.decode("utf-8", errors="replace")

class CliConnection:
    """Host owns executable selection, consent, receipts, storage and install."""
    def __init__(self, spec, executable=None):
        self.spec = validate_spec(spec)
        self.requested = executable
        self.path = None
        self._identity = None
        self._verified = set()

    def observation(self, status, **extra):
        return dict(schema=SCHEMA, program_id=self.spec["program_id"],
                    transport="cli", status=status, **extra)

    def inspect(self):
        """Never runs the program or modifies its installation."""
        path = self.requested or shutil.which(self.spec["executable_name"])
        if path:
            file = Path(path)
            if file.is_file() and not file.is_symlink() and (
                os.name == "nt" or os.access(file, os.X_OK)
            ):
                return self.observation("DISCOVERED", installed=True,
                                        path=str(file.resolve()), connected=False,
                                        architecture="UNKNOWN", ownership="UNKNOWN",
                                        interfaces=["cli"], process="UNKNOWN")
        return self.observation("NOT_FOUND", installed=False, connected=False,
                                architecture="UNKNOWN", ownership="UNKNOWN",
                                interfaces=[], process="UNKNOWN")

    def connect(self):
        found = self.inspect()
        if not found["installed"]:
            self.disconnect()
            return found
        identity = executable_identity(found["path"])
        if identity is None:
            self.disconnect()
            return self.observation("NOT_FOUND", installed=False, connected=False)
        if self.path != found["path"] or self._identity != identity:
            self._verified.clear()
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
        return self.observation("CONNECTED", installed=True, connected=True,
                                path=self.path, version=version.strip()[:256])

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
        return self.observation(
            "CONNECTION_LOST" if lost else ("DECLARED" if self.path else "NOT_CONNECTED"),
            connected=bool(self.path),
            operations=[dict(name=name, declared=True, verified=name in self._verified)
                        for name in sorted(self.spec["operations"])])

    def invoke(self, operation, params=None):
        if not self.path:
            return self.observation("NOT_CONNECTED", executed=False)
        if executable_identity(self.path) != self._identity:
            self.disconnect()
            return self.observation("CONNECTION_LOST", executed=False)
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
                                output=output,
                                elapsed_ms=round((time.perf_counter()-start)*1000, 3))

    def disconnect(self):
        self.path = None
        self._identity = None
        self._verified.clear()
        return self.observation("DISCONNECTED", connected=False)

    def recover(self):
        return self.connect()
