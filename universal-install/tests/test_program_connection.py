"""Cross-platform native CLI invocation and real Windows no-console verification."""
import hashlib
import os
from unittest import mock
import sys
import time
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "universal-install"))
from iwxa_universal_install.program_connection import run_command, CliConnection, ProgramError


class CliProcessHostTest(unittest.TestCase):
    def test_native_python_process_result(self):
        code, output = run_command(sys.executable, ["-c", "print(6 * 7)"])
        self.assertEqual(code, 0)
        self.assertEqual(output.strip(), "42")

    def test_connection_uses_real_cli_and_recovery(self):
        version = f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        spec = {
            "schema": "IWXA_CLI_PROGRAM/1",
            "program_id": "PYTHON_CLI_PROBE",
            "executable_name": Path(sys.executable).name,
            "version_args": ["--version"],
            "version_contains": version,
            "operations": {"compute": ["-c", "print(6 * 7)"]}
        }
        # Python supplied by setup-python may expose sys.executable as a
        # symlink on Unix. CLI discovery intentionally rejects symlinks.
        connection = CliConnection(spec, str(Path(sys.executable).resolve()))
        self.assertEqual(connection.inspect()["status"], "DISCOVERED")
        self.assertEqual(connection.connect()["status"], "CONNECTED")
        self.assertEqual(connection.capabilities()["operations"][0]["verified"], False)
        self.assertEqual(connection.invoke("compute")["output"].strip(), "42")
        self.assertEqual(connection.capabilities()["operations"][0]["verified"], True)
        self.assertEqual(connection.disconnect()["status"], "DISCONNECTED")
        self.assertEqual(connection.recover()["status"], "CONNECTED")
        self.assertEqual(connection.capabilities()["operations"][0]["verified"], False)

    def test_pinned_installed_identity_and_two_stable_sessions(self):
        executable = str(Path(sys.executable).resolve())
        digest = hashlib.sha256(Path(executable).read_bytes()).hexdigest()
        version = f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        spec = {
            "schema": "IWXA_CLI_PROGRAM/1", "program_id": "PYTHON_EXISTING_APP",
            "executable_name": Path(executable).name,
            "executable_sha256": digest, "requires_running": True,
            "version_args": ["--version"], "version_contains": version,
            "operations": {"compute": ["-c", "print(6*7)"],
                           "with_value": ["-c", "import sys;print(sys.argv[1])",
                                          "file={note}"]}
        }
        evidence = {"program_id": "PYTHON_EXISTING_APP", "verified": True,
                    "status": "RUNNING", "spawned": False,
                    "pid": os.getpid(), "processStamp": "os-process-stamp-1"}
        observe = lambda _program_id: dict(evidence)
        first = CliConnection(spec, executable, process_probe=observe)
        second = CliConnection(spec, executable, process_probe=observe)
        inspection = first.inspect()
        self.assertEqual(inspection["status"], "DISCOVERED")
        self.assertTrue(inspection["installation_verified"])
        self.assertEqual(inspection["installation_identity"]["sha256"], digest)
        self.assertTrue(inspection["running"])
        connected = first.connect()
        self.assertEqual(connected["status"], "CONNECTED")
        self.assertEqual(connected["process_id"], os.getpid())
        self.assertEqual(second.connect()["status"], "CONNECTED")
        self.assertEqual(second.capabilities()["status"], "DECLARED")
        self.assertNotEqual(first.capabilities()["connection_epoch"],
                            second.capabilities()["connection_epoch"])
        result = first.invoke("compute")
        self.assertEqual(result["output"].strip(), "42")
        self.assertEqual(result["connection_epoch"], connected["connection_epoch"])
        self.assertEqual(second.invoke("with_value",
                                       {"note": "a b.md"})["output"].strip(),
                         "file=a b.md")
        self.assertEqual(first.capabilities()["status"], "DECLARED")
        self.assertEqual(first.capabilities()["operations"][0]["verified"], True)
        self.assertEqual(first.verify()["connection_epoch"], connected["connection_epoch"])
        self.assertEqual(first.disconnect()["status"], "DISCONNECTED")
        self.assertEqual(second.invoke("compute")["status"], "EXECUTED")
        self.assertEqual(second.recover()["status"], "CONNECTED")
        self.assertEqual(second.capabilities()["status"], "DECLARED")
        evidence["status"] = "NOT_RUNNING"
        self.assertEqual(second.invoke("compute")["status"], "PROCESS_NOT_RUNNING")
        self.assertEqual(second.capabilities()["status"], "NOT_CONNECTED")
        self.assertEqual(second.connect()["status"], "PROCESS_NOT_RUNNING")
        evidence["status"] = "RUNNING"
        self.assertEqual(second.recover()["status"], "CONNECTED")
        self.assertNotEqual(second.capabilities()["connection_epoch"],
                            connected["connection_epoch"])

    def test_pinned_installation_digest_mismatch_is_not_a_connected_program(self):
        executable = str(Path(sys.executable).resolve())
        spec = {"schema": "IWXA_CLI_PROGRAM/1", "program_id": "PYTHON_PIN_MISMATCH",
                "executable_name": Path(executable).name,
                "executable_sha256": "0"*64,
                "version_args": ["--version"], "version_contains": "Python",
                "operations": {"compute": ["-c", "print(42)"]}}
        conn = CliConnection(spec, executable)
        self.assertEqual(conn.inspect()["status"], "INSTALLATION_MISMATCH")
        self.assertFalse(conn.inspect()["installation_verified"])
        self.assertEqual(conn.connect()["status"], "INSTALLATION_MISMATCH")
        self.assertEqual(conn.capabilities()["status"], "NOT_CONNECTED")
        self.assertEqual(conn.invoke("compute")["status"], "NOT_CONNECTED")

    def test_running_process_proof_must_be_supplied_by_host(self):
        executable = str(Path(sys.executable).resolve())
        spec = {"schema": "IWXA_CLI_PROGRAM/1", "program_id": "PYTHON_NEEDS_RUNNING",
                "executable_name": Path(executable).name,
                "requires_running": True, "version_args": ["--version"],
                "version_contains": "Python",
                "operations": {"compute": ["-c", "print(42)"]}}
        missing = CliConnection(spec, executable)
        self.assertEqual(missing.connect()["status"], "RUNNING_PROCESS_UNVERIFIED")
        self.assertEqual(missing.capabilities()["unavailable_reason"],
                         "RUNNING_PROCESS_UNVERIFIED")
        spoofed = CliConnection(spec, executable, process_probe=lambda _: {
            "program_id": "PYTHON_NEEDS_RUNNING", "status": "RUNNING",
            "verified": False, "spawned": False, "pid": os.getpid(),
            "processStamp": "os-stamp-123456"})
        self.assertEqual(spoofed.connect()["status"], "RUNNING_PROCESS_UNVERIFIED")

    def test_agent03_preflight_and_post_execution_outcomes(self):
        executable = str(Path(sys.executable).resolve())
        version = f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        spec = {"schema": "IWXA_CLI_PROGRAM/1", "program_id": "PYTHON_EFFECT_RESULT",
                "executable_name": Path(executable).name,
                "version_args": ["--version"], "version_contains": version,
                "operations": {"echo": ["-c", "print('42')"],
                               "failed": ["-c", "raise SystemExit(3)"]}}
        conn = CliConnection(spec, executable)
        self.assertEqual(conn.invoke("echo")["executionOutcome"], "NONE")
        connected = conn.connect()
        self.assertEqual(connected["status"], "CONNECTED")
        self.assertEqual(conn.invoke("missing")["executionOutcome"], "NONE")
        self.assertEqual(conn.invoke("echo", {"unexpected": "value"})["executionOutcome"], "NONE")
        failed = conn.invoke("failed")
        self.assertEqual((failed["status"], failed["executionOutcome"], failed["exit_code"]),
                         ("EXECUTION_FAILED", "UNKNOWN", 3))
        result = conn.invoke("echo")
        self.assertEqual((result["status"], result["output"].strip(),
                          result["executionOutcome"]), ("EXECUTED", "42", "ACKNOWLEDGED"))
        self.assertEqual(result["connection_epoch"], connected["connection_epoch"])

    def test_hash_cached_for_polling_but_explicit_verify_rehashes(self):
        executable = str(Path(sys.executable).resolve())
        digest = hashlib.sha256(Path(executable).read_bytes()).hexdigest()
        version = f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        spec = {"schema": "IWXA_CLI_PROGRAM/1", "program_id": "PYTHON_PIN_CACHE",
                "executable_name": Path(executable).name, "executable_sha256": digest,
                "version_args": ["--version"], "version_contains": version,
                "operations": {"echo": ["-c", "print('42')"]}}
        conn = CliConnection(spec, executable)
        original = hashlib.sha256
        calls = []
        def counted(*args, **kwargs):
            calls.append(1)
            return original(*args, **kwargs)
        with mock.patch("iwxa_universal_install.program_connection.hashlib.sha256",
                        side_effect=counted):
            connected = conn.connect()
            self.assertEqual(connected["status"], "CONNECTED")
            self.assertTrue(conn.capabilities()["installation_verified"])
            self.assertTrue(conn.capabilities()["installation_verified"])
            self.assertEqual(conn.invoke("echo")["output"].strip(), "42")
            self.assertEqual(len(calls), 1, "polling rehashed an unchanged executable")
            self.assertEqual(conn.verify()["connection_epoch"], connected["connection_epoch"])
            self.assertEqual(len(calls), 2, "explicit verify did not rehash")
            self.assertTrue(conn.capabilities()["installation_verified"])
            self.assertEqual(len(calls), 2)

    def test_existing_process_restart_invalidates_epoch_and_capabilities(self):
        executable = str(Path(sys.executable).resolve())
        version = f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        spec = {"schema": "IWXA_CLI_PROGRAM/1", "program_id": "PYTHON_APP_REUSE",
                "executable_name": Path(executable).name, "requires_running": True,
                "version_args": ["--version"], "version_contains": version,
                "operations": {"echo": ["-c", "print('42')"]}}
        identity = {"program_id": "PYTHON_APP_REUSE", "verified": True,
                    "status": "RUNNING", "spawned": False,
                    "pid": os.getpid(), "processStamp": "stamp-before-restart"}
        conn = CliConnection(spec, executable, process_probe=lambda _: dict(identity))
        first = conn.connect()
        self.assertEqual(first["status"], "CONNECTED")
        self.assertEqual(conn.invoke("echo")["status"], "EXECUTED")
        identity["processStamp"] = "stamp-after-restart"
        changed = conn.capabilities()
        self.assertEqual(changed["status"], "PROCESS_CHANGED")
        self.assertFalse(changed["connected"])
        self.assertFalse(changed["operations"][0]["verified"])
        self.assertEqual(conn.invoke("echo")["executionOutcome"], "NONE")
        after = conn.recover()
        self.assertEqual(after["status"], "CONNECTED")
        self.assertNotEqual(after["connection_epoch"], first["connection_epoch"])
        identity["status"] = "NOT_RUNNING"
        self.assertEqual(conn.invoke("echo")["status"], "PROCESS_NOT_RUNNING")
        self.assertEqual(conn.connect()["status"], "PROCESS_NOT_RUNNING")

    def test_process_changing_during_version_probe_cannot_connect(self):
        executable = str(Path(sys.executable).resolve())
        version = f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        spec = {"schema": "IWXA_CLI_PROGRAM/1", "program_id": "PYTHON_PROBE_SWAP",
                "executable_name": Path(executable).name, "requires_running": True,
                "version_args": ["--version"], "version_contains": version,
                "operations": {"echo": ["-c", "print('42')"]}}
        n = [0]
        def probe(_):
            n[0] += 1
            return {"program_id": "PYTHON_PROBE_SWAP", "verified": True,
                    "status": "RUNNING", "spawned": False, "pid": os.getpid(),
                    "processStamp": "initial-process" if n[0] == 1 else "other-process"}
        conn = CliConnection(spec, executable, process_probe=probe)
        self.assertEqual(conn.connect()["status"], "PROCESS_CHANGED")
        self.assertEqual(conn.capabilities()["status"], "NOT_CONNECTED")

    def test_excessive_output_is_rejected_before_timeout(self):
        for stream in ("stdout", "stderr"):
            with self.subTest(stream=stream):
                script = (
                    "import sys,time; "
                    f"sys.{stream}.buffer.write(b'x'*262144); "
                    f"sys.{stream}.flush(); time.sleep(30)"
                )
                started = time.monotonic()
                with self.assertRaisesRegex(ProgramError, "CLI_OUTPUT_TOO_LARGE"):
                    run_command(sys.executable, ["-c", script])
                self.assertLess(time.monotonic() - started, 8.0)

    @unittest.skipUnless(os.name == "nt", "Win32 console window belongs to Windows only")
    def test_child_console_is_absent_for_gui_host(self):
        code, output = run_command(sys.executable, [
            "-c", "import ctypes; print(ctypes.windll.kernel32.GetConsoleWindow())"
        ])
        self.assertEqual(code, 0)
        self.assertEqual(output.strip(), "0", "Windows child unexpectedly owns a console")


if __name__ == "__main__":
    unittest.main()
