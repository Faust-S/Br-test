"""Wayland-only U04 presentation port: launch/attach, never generic window focus.

Wayland doesn't expose unrelated wl_surface/window IDs to arbitrary clients.
Compositor-controlled xdg-activation-v1 requires a real toolkit/surface/token.
This adapter deliberately advertises FOCUS, LINKED_WINDOW and EMBEDDED_VIEW
as unsupported until a host-specific official API can prove them.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import uuid

SCHEMA = "IWXA_PRESENTATION_BRIDGE/1.0.0"
FOCUS_REASON = "COMPOSITOR_ACTIVATION_TOKEN_REQUIRED"
LINK_REASON = "WAYLAND_FOREIGN_TOPLEVEL_NOT_AVAILABLE"
EMBED_REASON = "NO_OFFICIAL_TOOLKIT_EMBED_API"


def process_start(pid):
    """Linux /proc stat field 22, checked to reject stale/reused process IDs."""
    if type(pid) is not int or pid <= 0:
        return None
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        return stat.rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError, ValueError):
        return None


class WaylandPort:
    def __init__(self, resolver, *, environ=None, launcher=subprocess.Popen):
        if not callable(resolver):
            raise ValueError("HOST_APPROVAL_REQUIRED")
        self.resolver = resolver
        self.env = dict(environ) if environ is not None else dict(os.environ)
        self.launcher = launcher
        self.bindings = {}

    def _session(self):
        if self.env.get("XDG_SESSION_TYPE", "").lower() != "wayland":
            return False, "WAYLAND_SESSION_REQUIRED"
        name = self.env.get("WAYLAND_DISPLAY", "")
        runtime = self.env.get("XDG_RUNTIME_DIR", "")
        if not name or not runtime:
            return False, "WAYLAND_DISPLAY_UNAVAILABLE"
        path = Path(name) if Path(name).is_absolute() else Path(runtime) / name
        if not path.is_socket():
            return False, "WAYLAND_SOCKET_NOT_FOUND"
        return True, None

    def capabilities(self):
        launch, why = self._session()
        return {"schema": SCHEMA, "capabilities": [
            {"mode": "LAUNCH", "supported": launch,
             **({} if launch else {"reason": why})},
            {"mode": "FOCUS", "supported": False, "reason": FOCUS_REASON},
            {"mode": "LINKED_WINDOW", "supported": False, "reason": LINK_REASON},
            {"mode": "EMBEDDED_VIEW", "supported": False, "reason": EMBED_REASON},
        ]}

    def _resolve(self, app_ref, host_ref, request):
        try:
            approval = self.resolver(app_ref, host_ref, request)
        except Exception:
            return None
        return approval if isinstance(approval, dict) and approval.get("approved") is True else None

    def open(self, app_ref, host_ref, launch_spec):
        available, why = self._session()
        if not available:
            return {"schema": SCHEMA, "status": "PLATFORM_UNSUPPORTED", "reason": why}
        approval = self._resolve(app_ref, host_ref, launch_spec)
        if approval is None:
            return {"schema": SCHEMA, "status": "HOST_APPROVAL_REQUIRED"}
        exe, args = approval.get("executable"), approval.get("args", [])
        if (not isinstance(exe, str) or not Path(exe).is_absolute() or
            not Path(exe).is_file() or not os.access(exe, os.X_OK) or
            not isinstance(args, list) or len(args) > 64 or
            any(not isinstance(a, str) or "\0" in a or len(a) > 16384 for a in args)):
            return {"schema": SCHEMA, "status": "EXECUTABLE_INVALID"}
        try:
            child = self.launcher([exe, *args], env=self.env,
                                  stdin=subprocess.DEVNULL,
                                  stdout=subprocess.DEVNULL,
                                  stderr=subprocess.DEVNULL,
                                  start_new_session=True)
        except OSError:
            return {"schema": SCHEMA, "status": "LAUNCH_FAILED"}
        pid = child.pid
        stamp = process_start(pid)
        if stamp is None:
            return {"schema": SCHEMA, "status": "PROCESS_NOT_OBSERVABLE"}
        binding = uuid.uuid4().hex
        self.bindings[binding] = {
            "pid": pid, "stamp": stamp, "appRef": app_ref, "hostRef": host_ref
        }
        return {"schema": SCHEMA, "status": "LAUNCHED_NO_WINDOW",
                "reason": LINK_REASON, "bindingRef": binding,
                "processId": pid, "processStamp": stamp,
                "windowRef": None, "spawned": True}

    def reattach(self, app_ref, host_ref, pid, stamp):
        available, why = self._session()
        if not available:
            return {"schema": SCHEMA, "status": "PLATFORM_UNSUPPORTED", "reason": why}
        if (type(pid) is not int or pid <= 0 or pid > 2147483647 or
            not isinstance(stamp, str) or not stamp.isdecimal() or len(stamp) > 32):
            return {"schema": SCHEMA, "status": "PROCESS_IDENTITY_INVALID"}
        approval = self._resolve(app_ref, host_ref, {
            "operation": "reattach", "pid": pid, "stamp": stamp
        })
        if approval is None or approval.get("pid") != pid or approval.get("stamp") != stamp:
            return {"schema": SCHEMA, "status": "HOST_APPROVAL_REQUIRED"}
        if process_start(pid) != stamp:
            return {"schema": SCHEMA, "status": "PROCESS_IDENTITY_CHANGED"}
        binding = uuid.uuid4().hex
        self.bindings[binding] = {
            "pid": pid, "stamp": stamp, "appRef": app_ref, "hostRef": host_ref
        }
        return {"schema": SCHEMA, "status": "BOUND_NO_WINDOW", "reason": LINK_REASON,
                "bindingRef": binding, "processId": pid, "processStamp": stamp,
                "windowRef": None, "spawned": False}

    def activate(self, binding_ref, host_ref):
        binding = self.bindings.get(binding_ref)
        if binding is None or binding["hostRef"] != host_ref:
            return {"schema": SCHEMA, "status": "BINDING_NOT_FOUND", "focused": False}
        if process_start(binding["pid"]) != binding["stamp"]:
            return {"schema": SCHEMA, "status": "PROCESS_IDENTITY_CHANGED", "focused": False}
        return {"schema": SCHEMA, "status": "FOCUS_UNSUPPORTED",
                "reason": FOCUS_REASON, "focused": False}

    def close_binding(self, binding_ref, host_ref):
        binding = self.bindings.get(binding_ref)
        if binding is None or binding["hostRef"] != host_ref:
            return {"schema": SCHEMA, "status": "BINDING_NOT_FOUND"}
        self.bindings.pop(binding_ref)
        # Never signal/kill an external program, even when the parent exits.
        return {"schema": SCHEMA, "status": "BINDING_CLOSED", "processTerminated": False}
