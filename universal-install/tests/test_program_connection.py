"""Cross-platform native CLI invocation and real Windows no-console verification."""
import hashlib
import json
import os
from unittest import mock
import sys
import time
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "universal-install"))
from iwxa_universal_install.program_connection import run_command, CliConnection, ProgramError, argument_parameter_names


class CliProcessHostTest(unittest.TestCase):
    def test_agent05_file_input_parameter_templates_are_discoverable(self):
        catalog_path = ROOT / "universal-install" / "catalog" / "cli-connectors-1.json"
        catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
        by_id = {p["program_id"]: p for p in catalog["programs"]}
        jq = by_id["JQ_CLI_V1"]
        yq = by_id["YQ_CLI_V1"]
        self.assertEqual(argument_parameter_names(jq["operations"]["evaluate"]),
                         ["expression"])
        self.assertEqual(argument_parameter_names(jq["operations"]["transform_json"]),
                         ["filter", "input"])
        self.assertEqual(argument_parameter_names(yq["operations"]["read_yaml_json"]),
                         ["input"])
        self.assertEqual(jq["operations"]["query_json"],
                         ["--", "{expression}", "{input}"])
        self.assertEqual(yq["operations"]["query_yaml"],
                         ["eval", "-o=json", "--expression", "{expression}", "{input}"])
        self.assertEqual(argument_parameter_names(jq["operations"]["query_json"]),
                         ["expression", "input"])
        self.assertEqual(argument_parameter_names(yq["operations"]["query_yaml"]),
                         ["expression", "input"])
        self.assertEqual(argument_parameter_names(["--","file={path}","{expression}"]),
                         ["expression","path"])
        with self.assertRaisesRegex(ValueError, "CLI_ARGUMENTS_INVALID"):
            argument_parameter_names(["{input}", None])
        self.assertEqual(jq["version_contains"],"jq-1.8.1")
        self.assertEqual(yq["version_contains"],"v4.54.1")

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

    def test_agent05_bulk_inventory_defers_pin_but_connect_verifies(self):
        executable = str(Path(sys.executable).resolve())
        digest = hashlib.sha256(Path(executable).read_bytes()).hexdigest()
        spec = {
            "schema": "IWXA_CLI_PROGRAM/1",
            "program_id": "PYTHON_AGENT05_FAST_INVENTORY",
            "executable_name": Path(executable).name,
            "executable_sha256": digest,
            "version_args": ["--version"],
            "version_contains": "Python",
            "operations": {"compute": ["-c", "print(42)"]},
        }
        conn = CliConnection(spec, executable)
        with mock.patch.object(conn, "installation_identity",
                               side_effect=AssertionError("inventory hashed binary")):
            quick = conn.inspect(verify_pin=False)
        self.assertEqual(quick["status"], "DISCOVERED")
        self.assertEqual(quick["installation_reason"], "PIN_CHECK_DEFERRED")
        self.assertFalse(quick["installation_verified"])
        self.assertNotIn("installation_identity", quick)
        connected = conn.connect()
        self.assertEqual(connected["status"], "CONNECTED")
        self.assertTrue(connected["installation_verified"])
        with self.assertRaisesRegex(ValueError, "CLI_VERIFY_PIN_INVALID"):
            conn.inspect(verify_pin="false")

    def test_agent05_deferred_inventory_cannot_authorize_wrong_binary(self):
        executable = str(Path(sys.executable).resolve())
        spec = {
            "schema": "IWXA_CLI_PROGRAM/1",
            "program_id": "PYTHON_AGENT05_DEFERRED_MISMATCH",
            "executable_name": Path(executable).name,
            "executable_sha256": "0" * 64,
            "version_args": ["--version"], "version_contains": "Python",
            "operations": {"compute": ["-c", "print(42)"]},
        }
        conn = CliConnection(spec, executable)
        inventory = conn.inspect(verify_pin=False)
        self.assertEqual(inventory["status"], "DISCOVERED")
        self.assertEqual(inventory["installation_reason"], "PIN_CHECK_DEFERRED")
        self.assertEqual(conn.connect()["status"], "INSTALLATION_MISMATCH")
        self.assertEqual(conn.invoke("compute")["executionOutcome"], "NONE")

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

    def test_failed_attempt_revokes_previous_verified_operation(self):
        executable = str(Path(sys.executable).resolve())
        version = f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        spec = {"schema": "IWXA_CLI_PROGRAM/1",
                "program_id": "PYTHON_REVOKE_ON_FAILURE",
                "executable_name": Path(executable).name,
                "version_args": ["--version"], "version_contains": version,
                "operations": {"dynamic": ["-c", "{script}"]}}
        conn = CliConnection(spec, executable)
        self.assertEqual(conn.connect()["status"], "CONNECTED")
        self.assertEqual(conn.invoke("dynamic", {"script": "print(42)"})["status"], "EXECUTED")
        self.assertTrue(conn.capabilities()["operations"][0]["verified"])
        failed = conn.invoke("dynamic", {"script": "raise SystemExit(3)"})
        self.assertEqual((failed["status"], failed["executionOutcome"]),
                         ("EXECUTION_FAILED","UNKNOWN"))
        self.assertFalse(conn.capabilities()["operations"][0]["verified"])

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

    def test_concurrent_real_process_does_not_duplicate_or_ack_stale_session(self):
        import tempfile
        import threading
        executable = str(Path(sys.executable).resolve())
        version = f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        script = (
            "import sys,time\nfrom pathlib import Path\n"
            "effect,gate=map(Path,sys.argv[1:3])\n"
            "with effect.open('a',encoding='utf-8') as stream:\n"
            "    stream.write('external-effect\\n')\n"
            "deadline=time.monotonic()+8\n"
            "while not gate.exists() and time.monotonic()<deadline:\n"
            "    time.sleep(0.02)\n"
            "print('completed')\n"
        )
        spec = {"schema":"IWXA_CLI_PROGRAM/1", "program_id":"PYTHON_CONCURRENT_APP",
                "executable_name":Path(executable).name,
                "version_args":["--version"],"version_contains":version,
                "operations":{"slow":["-c",script,"{effect}","{gate}"],
                              "echo":["-c","print(42)"]}}
        conn = CliConnection(spec,executable)
        with tempfile.TemporaryDirectory(prefix="iwxa u01 concurrent ") as folder:
            effect = Path(folder)/"external effects.txt"
            gate = Path(folder)/"release signal.txt"
            params = {"effect":str(effect),"gate":str(gate)}
            initial = conn.connect()
            self.assertEqual(initial["status"],"CONNECTED")

            def run_one(results):
                results.append(conn.invoke("slow",params))

            def wait_started():
                deadline = time.monotonic()+6
                while not effect.exists() and time.monotonic()<deadline:
                    time.sleep(0.02)
                self.assertTrue(effect.exists(),"real subprocess never began")

            first = []
            thread = threading.Thread(target=run_one,args=(first,),daemon=True)
            thread.start()
            try:
                wait_started()
                for operation, bound in (("slow",params),("echo",None)):
                    busy=conn.invoke(operation,bound)
                    self.assertEqual((busy["status"],busy["executionOutcome"]),
                                     ("CLI_BUSY","NONE"))
                self.assertEqual(effect.read_text(encoding="utf-8"),
                                 "external-effect\n")
            finally:
                gate.write_text("release",encoding="utf-8")
                thread.join(timeout=6)
            self.assertFalse(thread.is_alive())
            self.assertEqual(first[0]["status"],"EXECUTED")
            self.assertEqual(first[0]["connection_epoch"],
                             initial["connection_epoch"])
            self.assertTrue(next(x for x in conn.capabilities()["operations"]
                                 if x["name"]=="slow")["verified"])
            effect.unlink()
            gate.unlink()

            second = []
            thread = threading.Thread(target=run_one,args=(second,),daemon=True)
            thread.start()
            try:
                wait_started()
                self.assertEqual(conn.disconnect()["status"],"DISCONNECTED")
                recovered=conn.recover()
                self.assertEqual(recovered["status"],"CONNECTED")
                self.assertNotEqual(recovered["connection_epoch"],
                                    initial["connection_epoch"])
                busy=conn.invoke("echo")
                self.assertEqual((busy["status"],busy["executionOutcome"]),
                                 ("CLI_BUSY","NONE"))
                self.assertEqual(effect.read_text(encoding="utf-8"),
                                 "external-effect\n")
            finally:
                gate.write_text("release",encoding="utf-8")
                thread.join(timeout=6)
            self.assertFalse(thread.is_alive())
            self.assertEqual((second[0]["status"], second[0]["executionOutcome"]),
                             ("CONNECTION_SUPERSEDED","UNKNOWN"))
            self.assertFalse(next(x for x in conn.capabilities()["operations"]
                                  if x["name"]=="slow")["verified"])
            echo = conn.invoke("echo")
            self.assertEqual((echo["status"],echo["output"].strip()),
                             ("EXECUTED","42"))
            self.assertEqual(echo["connection_epoch"],
                             recovered["connection_epoch"])

    def test_host_cancel_before_start_does_not_launch_program(self):
        import threading
        flag = threading.Event()
        flag.set()
        with self.assertRaisesRegex(ProgramError, "CLI_CANCELLED_BEFORE_START"):
            run_command(sys.executable, ["-c", "raise RuntimeError('ran')"],
                        cancel_event=flag)
        version = f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        spec = {"schema": "IWXA_CLI_PROGRAM/1", "program_id": "PYTHON_CANCEL_TEST",
                "executable_name": Path(sys.executable).name,
                "version_args": ["--version"], "version_contains": version,
                "operations": {"slow": ["-c", "import time;time.sleep(9)"]}}
        conn = CliConnection(spec, str(Path(sys.executable).resolve()))
        self.assertEqual(conn.connect()["status"], "CONNECTED")
        observation = conn.invoke("slow", cancel_event=flag)
        self.assertEqual((observation["status"], observation["executionOutcome"]),
                         ("CLI_CANCELLED", "NONE"))
        self.assertFalse(conn.capabilities()["operations"][0]["verified"])

    def test_host_cancel_running_cli_kills_child_and_preserves_session(self):
        import threading
        version = f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        spec = {"schema": "IWXA_CLI_PROGRAM/1", "program_id": "PYTHON_CANCEL_RUNNING",
                "executable_name": Path(sys.executable).name,
                "version_args": ["--version"], "version_contains": version,
                "operations": {"slow": ["-c", "import time;time.sleep(9)"],
                               "echo": ["-c", "print(42)"]}}
        conn = CliConnection(spec, str(Path(sys.executable).resolve()))
        connected = conn.connect()
        flag = threading.Event()
        timer = threading.Timer(0.15, flag.set)
        timer.start()
        start = time.monotonic()
        try:
            status = conn.invoke("slow", cancel_event=flag)
        finally:
            timer.join(timeout=2)
        self.assertEqual((status["status"], status["executionOutcome"]),
                         ("CLI_CANCELLED", "UNKNOWN"))
        self.assertLess(time.monotonic()-start, 3.0)
        self.assertEqual(conn.capabilities()["connection_epoch"], connected["connection_epoch"])
        self.assertFalse(next(x for x in conn.capabilities()["operations"]
                              if x["name"] == "slow")["verified"])
        self.assertEqual(conn.invoke("echo")["output"].strip(), "42")

    def test_real_timeout_stops_child_and_does_not_verify_operation(self):
        executable = str(Path(sys.executable).resolve())
        version = f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        spec = {"schema": "IWXA_CLI_PROGRAM/1", "program_id": "PYTHON_TIMEOUT_NATIVE",
                "executable_name": Path(executable).name,
                "version_args": ["--version"], "version_contains": version,
                "operations": {"slow": ["-c", "import time;time.sleep(8)"]}}
        conn = CliConnection(spec, executable)
        connected = conn.connect()
        started = time.monotonic()
        result = conn.invoke("slow", timeout_seconds=0.2)
        self.assertEqual((result["status"], result["executionOutcome"]),
                         ("CLI_TIMED_OUT", "UNKNOWN"))
        self.assertLess(time.monotonic()-started, 3.0)
        self.assertFalse(conn.capabilities()["operations"][0]["verified"])
        self.assertEqual(conn.capabilities()["connection_epoch"], connected["connection_epoch"])
        rejected = conn.invoke("slow", timeout_seconds=0)
        self.assertEqual((rejected["status"], rejected["executionOutcome"]),
                         ("CLI_INPUT_INVALID", "NONE"))

    def test_invalid_utf8_cli_output_is_not_declared_a_success(self):
        executable = str(Path(sys.executable).resolve())
        version = f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        spec = {"schema": "IWXA_CLI_PROGRAM/1", "program_id": "PYTHON_BINARY_OUTPUT",
                "executable_name": Path(executable).name,
                "version_args": ["--version"], "version_contains": version,
                "operations": {"bad_encoding": ["-c",
                    "import sys;sys.stdout.buffer.write(bytes([255]))"]}}
        conn = CliConnection(spec, executable)
        self.assertEqual(conn.connect()["status"], "CONNECTED")
        result = conn.invoke("bad_encoding")
        self.assertEqual((result["status"],result["executionOutcome"]),
                         ("CLI_OUTPUT_ENCODING_UNSUPPORTED","UNKNOWN"))
        self.assertFalse(conn.capabilities()["operations"][0]["verified"])

    def test_host_approved_large_document_output_and_default_limit(self):
        executable = str(Path(sys.executable).resolve())
        version = f"Python {sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}"
        spec = {"schema": "IWXA_CLI_PROGRAM/1", "program_id": "PYTHON_DOCUMENT_OUTPUT",
                "executable_name": Path(executable).name,
                "version_args": ["--version"], "version_contains": version,
                "operations": {
                    "large": ["-c", "import sys;sys.stdout.write('X'*262144)"],
                    "too_large": ["-c", "import sys;sys.stdout.write('Y'*786432)"]}}
        default = CliConnection(spec, executable)
        self.assertEqual(default.connect()["status"], "CONNECTED")
        refused = default.invoke("large")
        self.assertEqual((refused["status"], refused["executionOutcome"]),
                         ("CLI_OUTPUT_TOO_LARGE", "UNKNOWN"))
        self.assertFalse(default.capabilities()["operations"][0]["verified"])
        approved = CliConnection({**spec, "max_output_bytes": 524288}, executable)
        connected = approved.connect()
        self.assertEqual(connected["status"], "CONNECTED")
        large = approved.invoke("large")
        self.assertEqual((large["status"], large["executionOutcome"]),
                         ("EXECUTED", "ACKNOWLEDGED"))
        self.assertEqual(large["output"], "X"*262144)
        self.assertEqual(large["connection_epoch"], connected["connection_epoch"])
        refused = approved.invoke("too_large")
        self.assertEqual((refused["status"], refused["executionOutcome"]),
                         ("CLI_OUTPUT_TOO_LARGE", "UNKNOWN"))
        self.assertFalse(next(x for x in approved.capabilities()["operations"]
                              if x["name"] == "too_large")["verified"])
        self.assertTrue(next(x for x in approved.capabilities()["operations"]
                             if x["name"] == "large")["verified"])
        self.assertEqual(approved.invoke("large")["output"], large["output"])

        for invalid in (True, 0, 1023, 4194305, "524288", 524288.0):
            with self.subTest(invalid=invalid):
                with self.assertRaisesRegex(ValueError, "CLI_OUTPUT_LIMIT_INVALID"):
                    CliConnection({**spec, "max_output_bytes": invalid}, executable)

    def test_cancel_or_timeout_stops_real_descendant_before_later_file_write(self):
        import tempfile
        import threading
        parent_script = (
            "import subprocess,sys,time\n"
            "from pathlib import Path\n"
            "start,marker=sys.argv[1:3]\n"
            "child_script='import sys,time;from pathlib import Path;'"
            "+'time.sleep(1.5);Path(sys.argv[1]).write_text(\"ORPHAN\",encoding=\"utf-8\")'\n"
            "subprocess.Popen([sys.executable,'-c',child_script,marker],"
            "stdin=subprocess.DEVNULL,stdout=sys.stdout,stderr=sys.stderr)\n"
            "Path(start).write_text('STARTED',encoding='utf-8')\n"
            "time.sleep(4)\n"
        )
        with tempfile.TemporaryDirectory(prefix="iwxa-u01-tree-") as tmp:
            for mode in ("cancel", "timeout"):
                with self.subTest(mode=mode):
                    start = Path(tmp) / (mode+"-started.txt")
                    marker = Path(tmp) / (mode+"-descendant-effect.txt")
                    cancel = threading.Event()
                    watcher = None
                    if mode == "cancel":
                        def watch_start():
                            deadline = time.monotonic()+3
                            while not start.exists() and time.monotonic() < deadline:
                                time.sleep(0.01)
                            if start.exists():
                                cancel.set()
                        watcher = threading.Thread(target=watch_start,daemon=True)
                        watcher.start()
                    try:
                        with self.assertRaisesRegex(
                            ProgramError,
                            "CLI_CANCELLED" if mode == "cancel" else "CLI_TIMED_OUT",
                        ):
                            run_command(
                                str(Path(sys.executable).resolve()),
                                ["-c", parent_script, str(start), str(marker)],
                                cancel_event=cancel if mode == "cancel" else None,
                                timeout_seconds=3 if mode == "cancel" else 0.35,
                            )
                    finally:
                        if watcher:
                            watcher.join(timeout=4)
                    self.assertTrue(start.exists(),"native CLI did not spawn its child")
                    # The grandchild writes only if the parent termination left it running.
                    time.sleep(1.7)
                    self.assertFalse(marker.exists(),
                                     "cancelled CLI left its descendant performing work")

    @unittest.skipIf(os.name == "nt", "Windows tree termination needs a live parent PID")
    def test_parent_exits_but_descendant_holds_stdout_pipe(self):
        import tempfile
        parent_script = (
            "import subprocess,sys\n"
            "from pathlib import Path\n"
            "start,marker=sys.argv[1:3]\n"
            "sub='import sys,time;from pathlib import Path;'"
            "+'time.sleep(1.5);Path(sys.argv[1]).write_text(\"ORPHAN\",encoding=\"utf-8\")'\n"
            "subprocess.Popen([sys.executable,'-c',sub,marker],"
            "stdin=subprocess.DEVNULL,stdout=sys.stdout,stderr=sys.stderr)\n"
            "Path(start).write_text('STARTED',encoding='utf-8')\n"
        )
        with tempfile.TemporaryDirectory(prefix="iwxa-u01-exited-leader-") as tmp:
            started=Path(tmp)/"started.txt"
            marker=Path(tmp)/"late-child-effect.txt"
            with self.assertRaisesRegex(ProgramError,"CLI_TIMED_OUT"):
                run_command(
                    str(Path(sys.executable).resolve()),
                    ["-c",parent_script,str(started),str(marker)],
                    timeout_seconds=0.35,
                )
            self.assertTrue(started.exists())
            time.sleep(1.7)
            self.assertFalse(marker.exists(),"exited parent left an inherited-pipe child")

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
