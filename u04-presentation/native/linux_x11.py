"""Linux X11 presentation port using existing xdotool/X11 WM, not a compositor.

Host approves executable and arguments. No arbitrary command line from an appRef.
X11 windows are discovered by real OS PID, independently checked before focus.
Unsupported Wayland is reported explicitly.
"""
from __future__ import annotations

import os
from pathlib import Path
import subprocess
import uuid

SCHEMA = "IWXA_PRESENTATION_BRIDGE/1.0.0"


def _read_start(pid):
    try:
        # /proc/PID/stat starttime is field 22, after the parenthesized comm.
        stat = Path(f"/proc/{pid}/stat").read_text()
        return stat.rsplit(")", 1)[1].split()[19]
    except (OSError, IndexError, ValueError):
        return None


class X11Port:
    def __init__(self, resolver, *, run=subprocess.run, environ=None):
        if not callable(resolver):
            raise ValueError("HOST_APPROVAL_REQUIRED")
        self.resolver, self.run = resolver, run
        self.env = environ if environ is not None else os.environ
        self.bound = {}

    def _supported(self):
        if not self.env.get("DISPLAY"):
            return False, "X11_DISPLAY_UNAVAILABLE"
        if self.env.get("XDG_SESSION_TYPE", "").lower() == "wayland":
            return False, "WAYLAND_NATIVE_ADAPTER_REQUIRED"
        return True, None

    def capabilities(self):
        supported, reason = self._supported()
        return {"schema": SCHEMA, "capabilities": [
            {"mode":mode, "supported":supported, **({} if supported else {"reason":reason})}
            for mode in ("LAUNCH", "FOCUS", "LINKED_WINDOW")
        ] + [{"mode":"EMBEDDED_VIEW","supported":False,
              "reason":"NO_OFFICIAL_TOOLKIT_EMBED_API"}]}

    def _xdotool(self, *args):
        try:
            result = self.run(["xdotool", *map(str, args)], capture_output=True,
                              text=True, timeout=6, env=dict(self.env), check=False)
            return result.stdout.strip() if result.returncode == 0 else None
        except (OSError, subprocess.TimeoutExpired):
            return None

    def _window(self, pid):
        handles = self._xdotool("search", "--onlyvisible", "--pid", pid)
        if not handles:
            return None
        for item in handles.splitlines():
            if item.isdecimal() and int(item)>0:
                # X11 PID hints can be forged: qualify as OS observation, not an authority grant.
                observed = self._xdotool("getwindowpid", item)
                if observed and observed.isdecimal() and int(observed)==pid:
                    return item
        return None

    def open(self, app_ref, host_ref, launch_spec):
        supported, reason = self._supported()
        if not supported:
            return {"schema":SCHEMA,"status":"PLATFORM_UNSUPPORTED","reason":reason}
        approval = self.resolver(app_ref, host_ref, launch_spec)
        if not isinstance(approval, dict) or approval.get("approved") is not True:
            return {"schema":SCHEMA,"status":"HOST_APPROVAL_REQUIRED"}
        exe = approval.get("executable")
        args = approval.get("args", [])
        if not isinstance(exe,str) or not Path(exe).is_absolute() or not Path(exe).is_file() or (
            not isinstance(args,list) or len(args)>64 or any(
                not isinstance(a,str) or "\0" in a or len(a)>16384 for a in args
            )
        ):
            return {"schema":SCHEMA,"status":"EXECUTABLE_INVALID"}
        try:
            proc=subprocess.Popen([exe,*args], stdin=subprocess.DEVNULL,
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                                  start_new_session=True, env=dict(self.env))
        except OSError:
            return {"schema":SCHEMA,"status":"LAUNCH_FAILED"}
        stamp = _read_start(proc.pid)
        if stamp is None:
            return {"schema":SCHEMA,"status":"PROCESS_NOT_OBSERVABLE"}
        ref=uuid.uuid4().hex
        self.bound[ref]={"pid":proc.pid,"stamp":stamp,"hostRef":host_ref,"appRef":app_ref}
        window=self._window(proc.pid)
        return {"schema":SCHEMA,"status":"WINDOW_OBSERVED" if window else "LAUNCHED_NO_WINDOW",
                "bindingRef":ref,"processId":proc.pid,"processStamp":stamp,
                "windowRef":window, "spawned":True}

    def activate(self, binding_ref, host_ref):
        bound=self.bound.get(binding_ref)
        if not bound or bound["hostRef"]!=host_ref:
            return {"schema":SCHEMA,"status":"BINDING_NOT_FOUND","focused":False}
        pid=bound["pid"]
        if _read_start(pid)!=bound["stamp"]:
            return {"schema":SCHEMA,"status":"PROCESS_IDENTITY_CHANGED","focused":False}
        window=self._window(pid)
        if not window:
            return {"schema":SCHEMA,"status":"WINDOW_NOT_FOUND","focused":False}
        self._xdotool("windowactivate","--sync",window)
        foreground=self._xdotool("getactivewindow")
        focused=foreground==window
        return {"schema":SCHEMA,"status":"WINDOW_FOCUSED" if focused else "WINDOW_FOCUS_DENIED",
                "focused":focused,"windowRef":window,"processId":pid}

    def close_binding(self,binding_ref,host_ref):
        bound=self.bound.get(binding_ref)
        if not bound or bound["hostRef"]!=host_ref:
            return {"schema":SCHEMA,"status":"BINDING_NOT_FOUND"}
        self.bound.pop(binding_ref)
        return {"schema":SCHEMA,"status":"BINDING_CLOSED","processTerminated":False}
