"""Real Wayland compositor + GTK client + portable U04 launch/reattach.

The compositor is managed by CI. This test must independently verify:
- actual GTK Wayland window mapping, not merely the socket existence
- native process PID/stamp, detach, fresh adapter, no second spawn
- genuine Linux user file persistence and explicit unsupported focus/I3/I4
No compositor/window activation success may be invented.
"""
from __future__ import annotations
import importlib.util
import json
import os
from pathlib import Path
import signal
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/"artifacts"/"u04"/"wayland"
OUT.mkdir(parents=True,exist_ok=True)
source=ROOT/"u04-presentation"/"native"/"linux_wayland.py"
spec=importlib.util.spec_from_file_location("u04_wayland",source)
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
report={"schema":"IWXA_U04_WAYLAND_NATIVE/1","status":"FAIL",
        "windowRef":None,"embeddedSupported":False,"focused":False}

def check(condition,reason):
    if not condition:raise RuntimeError(reason)

def wait_receipt(path,pid,timeout=25):
    end=time.monotonic()+timeout
    while time.monotonic()<end:
        if path.is_file():
            try: record=json.loads(path.read_text(encoding="utf-8"))
            except (ValueError,OSError):record=None
            if record and record.get("pid")==pid:return record
        check(mod.process_start(pid) is not None,"GTK_PROCESS_EXITED")
        time.sleep(.12)
    raise RuntimeError("GTK_NATIVE_WAYLAND_MAP_NOT_OBSERVED")

def main():
    child=None
    try:
        with tempfile.TemporaryDirectory() as tmp:
            receipt=Path(tmp)/"gtk-map.json"
            document=Path(tmp)/"user.txt"
            document.write_text("PRESERVE THIS WAYLAND DOCUMENT",encoding="utf-8")
            fixture=ROOT/"u04-presentation"/"qualification"/"wayland_editor.py"
            approved_args=[str(fixture),str(receipt),str(document)]
            # GTK3 fixture must be a native Wayland client, never Xwayland.
            env=dict(os.environ,GDK_BACKEND="wayland",
                     XDG_SESSION_TYPE="wayland")
            def approve(a,h,request):
                accepted=(a=="editor" and h=="u04-ci-host")
                if request.get("operation")=="reattach":
                    return {"approved":accepted,"pid":request["pid"],
                            "stamp":request["stamp"]}
                return {"approved":accepted,"executable":"/usr/bin/python3",
                        "args":approved_args}
            original=mod.WaylandPort(approve,environ=env)
            check(original.capabilities()["capabilities"][0]["supported"],
                  "WAYLAND_SESSION_NOT_READY")
            opened=original.open("editor","u04-ci-host",{})
            check(opened.get("status")=="LAUNCHED_NO_WINDOW","WAYLAND_LAUNCH_FAILED")
            pid=opened["processId"]
            child=pid
            stamp=opened["processStamp"]
            gtk=wait_receipt(receipt,pid)
            check(gtk.get("native_wayland") is True and gtk.get("mapped") is True,
                  "GTK_NOT_NATIVE_WAYLAND")
            check(opened.get("windowRef") is None,"FOREIGN_WAYLAND_WINDOW_INVENTED")
            check(original.activate(opened["bindingRef"],"u04-ci-host")
                  ["status"]=="FOCUS_UNSUPPORTED","FALSE_COMPOSITOR_FOCUS")
            closed=original.close_binding(opened["bindingRef"],"u04-ci-host")
            check(closed.get("processTerminated") is False,"WAYLAND_DETACH_TERMINATED")
            check(mod.process_start(pid)==stamp,"WAYLAND_PROCESS_GONE_AFTER_DETACH")
            second=mod.WaylandPort(approve,environ=env)
            rebound=second.reattach("editor","u04-ci-host",pid,stamp)
            check(rebound.get("status")=="BOUND_NO_WINDOW" and
                  rebound.get("spawned") is False and rebound.get("processId")==pid,
                  "WAYLAND_REATTACH_FAILED")
            check(second.activate(rebound["bindingRef"],"u04-ci-host")
                  ["status"]=="FOCUS_UNSUPPORTED","FALSE_REATTACH_FOCUS")
            check(second.close_binding(rebound["bindingRef"],"u04-ci-host")
                  ["processTerminated"] is False,"WAYLAND_REATTACH_DETACH_FAILED")
            check(mod.process_start(pid)==stamp,"WAYLAND_PROCESS_KILLED")
            check(document.read_text(encoding="utf-8")=="PRESERVE THIS WAYLAND DOCUMENT",
                  "WAYLAND_USER_DOCUMENT_CHANGED")
            report.update(status="PASS",native_wayland_window_mapped=True,
                          native_backend=gtk["native_backend"],
                          same_process_reattached=True,
                          no_second_spawn=True,document_preserved=True,
                          external_survived_detach=True,
                          focus_status="FOCUS_UNSUPPORTED",
                          linked_status="WAYLAND_FOREIGN_TOPLEVEL_NOT_AVAILABLE",
                          app_pid=pid)
            print("U04_WAYLAND_NATIVE_LAUNCH_REATTACH_PASS",json.dumps(report),flush=True)
    except Exception as exc:
        report["error"]=str(exc)
        raise
    finally:
        OUT.joinpath("report.json").write_text(json.dumps(report,indent=2),
                                               encoding="utf-8")
        if child is not None:
            # CI fixture cleanup, never part of U04.close_binding.
            try:os.kill(child,signal.SIGTERM)
            except ProcessLookupError:pass
            try:os.waitpid(child,0)
            except ChildProcessError:pass

if __name__=="__main__":main()
