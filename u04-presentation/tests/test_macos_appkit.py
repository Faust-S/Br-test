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


    def test_reattach_explicit_exact_host_approval(self):
        with tempfile.TemporaryDirectory() as d:
            f=Path(d)/"native";f.touch()
            def approve(app,host,req):
                return {"approved":app=="editor" and host=="mac-host",
                        "pid":req.get("pid"),"stamp":req.get("stamp")}
            port=module.MacOSPort(f,approve,platform="darwin")
            port._call=Mock(return_value={"status":"PROCESS_OBSERVED",
                                           "launchTimestamp":"1234.000001"})
            ok=port.reattach("editor","mac-host",123,"1234.000001")
            self.assertEqual(ok["status"],"PROCESS_OBSERVED")
            self.assertIs(ok["spawned"],False)
            self.assertEqual(ok["processId"],123)
            self.assertEqual(port.activate(ok["bindingRef"],"other")["status"],
                             "BINDING_NOT_FOUND")
            self.assertEqual(port.close_binding(ok["bindingRef"],"mac-host")["status"],
                             "BINDING_CLOSED")

    def test_reused_pid_and_missing_start_time_cannot_reattach(self):
        with tempfile.TemporaryDirectory() as d:
            f=Path(d)/"native";f.touch()
            port=module.MacOSPort(f,lambda a,h,r:{"approved":True,
                                 "pid":r.get("pid"),"stamp":r.get("stamp")},platform="darwin")
            self.assertEqual(port.reattach("editor","host",123,"NOT_OBSERVED")["status"],
                             "PROCESS_IDENTITY_INVALID")
            port._call=Mock(return_value={"status":"PROCESS_OBSERVED",
                                          "launchTimestamp":"not-old"})
            self.assertEqual(port.reattach("editor","host",123,"old")["status"],
                             "PROCESS_IDENTITY_CHANGED")

if __name__=="__main__":unittest.main()
