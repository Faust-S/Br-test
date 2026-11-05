"""Wayland U04 tests: no false focus, real Linux process, exact reattach."""
import importlib.util
import os
from pathlib import Path
import signal
import socket
import sys
import tempfile
import unittest

SOURCE = Path(__file__).resolve().parents[1] / "native" / "linux_wayland.py"
SPEC = importlib.util.spec_from_file_location("u04_wayland_port", SOURCE)
mod = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)


class WaylandPortTest(unittest.TestCase):
    def session(self, directory):
        server = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        server.bind(str(Path(directory) / "wayland-u04"))
        server.listen(1)
        env = {"XDG_RUNTIME_DIR": directory, "WAYLAND_DISPLAY": "wayland-u04",
               "XDG_SESSION_TYPE": "wayland"}
        self.addCleanup(server.close)
        return env

    def test_wayland_requires_native_socket_not_an_x11_display(self):
        with tempfile.TemporaryDirectory() as d:
            env = {"XDG_RUNTIME_DIR": d, "WAYLAND_DISPLAY": "wayland-u04",
                   "XDG_SESSION_TYPE": "wayland"}
            port = mod.WaylandPort(lambda *_: None, environ=env)
            self.assertEqual(port.capabilities()["capabilities"][0]["reason"],
                             "WAYLAND_SOCKET_NOT_FOUND")
            self.assertEqual(port.open("editor", "host", {})["status"],
                             "PLATFORM_UNSUPPORTED")
            sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
            sock.bind(str(Path(d) / "wayland-u04"))
            try:
                x11 = mod.WaylandPort(lambda *_: None,
                                     environ={**env, "XDG_SESSION_TYPE": "x11"})
                self.assertEqual(x11.capabilities()["capabilities"][0]["reason"],
                                 "WAYLAND_SESSION_REQUIRED")
            finally:
                sock.close()

    def test_no_unsupported_window_or_compositor_focus_claim(self):
        with tempfile.TemporaryDirectory() as d:
            port = mod.WaylandPort(lambda *_: None, environ=self.session(d))
            caps = port.capabilities()["capabilities"]
            self.assertEqual([c["supported"] for c in caps],
                             [True, False, False, False])
            self.assertEqual(caps[1]["reason"], mod.FOCUS_REASON)
            self.assertEqual(caps[2]["reason"], mod.LINK_REASON)
            self.assertEqual(caps[3]["reason"], mod.EMBED_REASON)

    def test_host_approval_and_foreign_host_rejected(self):
        with tempfile.TemporaryDirectory() as d:
            port = mod.WaylandPort(lambda *_: {"approved": False},
                                   environ=self.session(d))
            self.assertEqual(port.open("editor", "host", {})["status"],
                             "HOST_APPROVAL_REQUIRED")
            port.bindings["key"] = {"pid": os.getpid(),
                                    "stamp": mod.process_start(os.getpid()),
                                    "hostRef": "one"}
            self.assertEqual(port.activate("key", "other")["status"],
                             "BINDING_NOT_FOUND")
            self.assertEqual(port.close_binding("key", "other")["status"],
                             "BINDING_NOT_FOUND")

    def test_real_process_launch_detach_and_independent_reattach(self):
        with tempfile.TemporaryDirectory() as d:
            env = self.session(d)
            pid = None
            args = ["-c", "import time; time.sleep(20)"]

            def resolver(a, h, request):
                if (a, h) != ("editor", "host"):
                    return {"approved": False}
                if request.get("operation") == "reattach":
                    return {"approved": True, "pid": request["pid"],
                            "stamp": request["stamp"]}
                return {"approved": True, "executable": sys.executable, "args": args}

            first = mod.WaylandPort(resolver, environ=env)
            result = first.open("editor", "host", {})
            try:
                self.assertEqual(result["status"], "LAUNCHED_NO_WINDOW")
                self.assertEqual(result["windowRef"], None)
                self.assertIs(result["spawned"], True)
                pid = result["processId"]
                stamp = result["processStamp"]
                self.assertEqual(mod.process_start(pid), stamp)
                self.assertEqual(first.activate(result["bindingRef"], "host")["status"],
                                 "FOCUS_UNSUPPORTED")
                self.assertEqual(first.close_binding(result["bindingRef"], "host")
                                 ["processTerminated"], False)
                self.assertEqual(mod.process_start(pid), stamp)

                second = mod.WaylandPort(resolver, environ=env)
                bound = second.reattach("editor", "host", pid, stamp)
                self.assertEqual(bound["status"], "BOUND_NO_WINDOW")
                self.assertIs(bound["spawned"], False)
                self.assertEqual(second.activate(bound["bindingRef"], "host")["focused"],
                                 False)
                self.assertEqual(second.close_binding(bound["bindingRef"], "host")["status"],
                                 "BINDING_CLOSED")
                self.assertEqual(second.reattach("editor", "host", pid, "0")["status"],
                                 "HOST_APPROVAL_REQUIRED")
                self.assertEqual(second.reattach("editor", "host", True, stamp)["status"],
                                 "PROCESS_IDENTITY_INVALID")
            finally:
                if pid:
                    os.kill(pid, signal.SIGTERM)
                    os.waitpid(pid, 0)


if __name__ == "__main__":
    unittest.main()
