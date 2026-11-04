"""X11 negative and independent GUI qualification tests."""
import importlib.util
from pathlib import Path
import unittest
from unittest.mock import Mock

PATH=Path(__file__).resolve().parents[1]/"native"/"linux_x11.py"
SPEC=importlib.util.spec_from_file_location("u04_linux_x11",PATH)
mod=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


class X11ContractTest(unittest.TestCase):
    def test_no_display_or_wayland_never_pretends_linking(self):
        for environ,reason in [
            ({}, "X11_DISPLAY_UNAVAILABLE"),
            ({"DISPLAY":":1","XDG_SESSION_TYPE":"wayland"},"WAYLAND_NATIVE_ADAPTER_REQUIRED")
        ]:
            port=mod.X11Port(lambda *_:None,environ=environ)
            self.assertEqual(port.capabilities()["capabilities"][2]["reason"],reason)
            self.assertEqual(port.open("a","h",{})["status"],"PLATFORM_UNSUPPORTED")

    def test_approved_host_is_required_before_spawning(self):
        port=mod.X11Port(lambda *_:{"approved":False},environ={"DISPLAY":":1"})
        self.assertEqual(port.open("a","h",{})["status"],"HOST_APPROVAL_REQUIRED")

    def test_wrong_host_cannot_focus_or_close(self):
        port=mod.X11Port(lambda *_:None,environ={"DISPLAY":":1"})
        port.bound["ref"]={"pid":111,"stamp":"22","hostRef":"allowed"}
        self.assertEqual(port.activate("ref","other")["status"],"BINDING_NOT_FOUND")
        self.assertEqual(port.close_binding("ref","other")["status"],"BINDING_NOT_FOUND")

    def test_pid_reuse_fails_closed(self):
        port=mod.X11Port(lambda *_:None,environ={"DISPLAY":":1"})
        port.bound["ref"]={"pid":99999999,"stamp":"22","hostRef":"host"}
        self.assertEqual(port.activate("ref","host")["status"],"PROCESS_IDENTITY_CHANGED")

    def test_close_detaches_without_killing(self):
        port=mod.X11Port(lambda *_:None,environ={"DISPLAY":":1"})
        port.bound["ref"]={"pid":42,"stamp":"1","hostRef":"host"}
        self.assertEqual(port.close_binding("ref","host")["processTerminated"],False)
        self.assertEqual(port.close_binding("ref","host")["status"],"BINDING_NOT_FOUND")

if __name__=="__main__":
    unittest.main()
