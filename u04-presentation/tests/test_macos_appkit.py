"""MacOS presentation port: no false I3/I4 and stale process refusal."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import Mock
import tempfile

p=Path(__file__).resolve().parents[1]/"native"/"macos_appkit.py"
spec=importlib.util.spec_from_file_location("u04_mac_native",p)
module=importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)

class MacOSPortTest(unittest.TestCase):
    def test_no_native_port_and_foreign_platform(self):
        port=module.MacOSPort("/unavailable",lambda *_:None,platform="darwin")
        self.assertFalse(port.capabilities()["capabilities"][0]["supported"])
        self.assertFalse(port.capabilities()["capabilities"][2]["supported"])
        self.assertEqual(port.open("a","h",{})["status"],"PLATFORM_UNSUPPORTED")

    def test_host_approval_required(self):
        with tempfile.TemporaryDirectory() as d:
            f=Path(d)/"native";f.touch()
            port=module.MacOSPort(f,lambda *_:{"approved":False},platform="darwin")
            self.assertEqual(port.open("a","h",{})["status"],"HOST_APPROVAL_REQUIRED")
            self.assertFalse(port.capabilities()["capabilities"][2]["supported"])
            self.assertFalse(port.capabilities()["capabilities"][3]["supported"])

    def test_reused_process_and_foreign_host_refused(self):
        with tempfile.TemporaryDirectory() as d:
            f=Path(d)/"native";f.touch()
            port=module.MacOSPort(f,lambda *_:None,platform="darwin")
            port.bindings["ref"]={"hostRef":"host","pid":41,"stamp":"old"}
            self.assertEqual(port.activate("ref","foreign")["status"],"BINDING_NOT_FOUND")
            port._call=Mock(return_value={"status":"PROCESS_OBSERVED",
                                             "launchTimestamp":"new"})
            self.assertEqual(port.activate("ref","host")["status"],"PROCESS_IDENTITY_CHANGED")
            self.assertEqual(port.close_binding("ref","host")["processTerminated"],False)

if __name__=="__main__":unittest.main()
