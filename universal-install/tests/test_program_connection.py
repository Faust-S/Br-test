"""Cross-platform native CLI invocation and real Windows no-console verification."""
import os
import sys
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "universal-install"))
from iwxa_universal_install.program_connection import run_command, CliConnection


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
        connection = CliConnection(spec, sys.executable)
        self.assertEqual(connection.inspect()["status"], "DISCOVERED")
        self.assertEqual(connection.connect()["status"], "CONNECTED")
        self.assertEqual(connection.capabilities()["operations"][0]["verified"], False)
        self.assertEqual(connection.invoke("compute")["output"].strip(), "42")
        self.assertEqual(connection.capabilities()["operations"][0]["verified"], True)
        self.assertEqual(connection.disconnect()["status"], "DISCONNECTED")
        self.assertEqual(connection.recover()["status"], "CONNECTED")
        self.assertEqual(connection.capabilities()["operations"][0]["verified"], False)

    @unittest.skipUnless(os.name == "nt", "Win32 console window belongs to Windows only")
    def test_child_console_is_absent_for_gui_host(self):
        code, output = run_command(sys.executable, [
            "-c", "import ctypes; print(ctypes.windll.kernel32.GetConsoleWindow())"
        ])
        self.assertEqual(code, 0)
        self.assertEqual(output.strip(), "0", "Windows child unexpectedly owns a console")


if __name__ == "__main__":
    unittest.main()
