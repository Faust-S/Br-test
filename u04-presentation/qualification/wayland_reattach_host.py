"""Independent process host for native Wayland reattachment qualification.

The first host is gone: this Python interpreter creates a fresh WaylandPort,
requires approved PID+starttime, and never launches an additional GUI.
"""
from __future__ import annotations
import importlib.util
import json
from pathlib import Path
import sys

ROOT=Path(__file__).resolve().parents[2]
spec=importlib.util.spec_from_file_location(
    "u04_wayland",ROOT/"u04-presentation"/"native"/"linux_wayland.py")
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

def main():
    if len(sys.argv)!=4:raise SystemExit("INVALID_ARGUMENT_COUNT")
    pid=int(sys.argv[1])
    stamp=sys.argv[2]
    receipt=Path(sys.argv[3])
    def approve(app,host,req):
        valid=(app=="editor" and host=="u04-ci-host" and
               req.get("operation")=="reattach" and req.get("pid")==pid and
               req.get("stamp")==stamp)
        return {"approved":valid,"pid":pid,"stamp":stamp}
    port=mod.WaylandPort(approve)
    caps=port.capabilities()["capabilities"]
    rebound=port.reattach("editor","u04-ci-host",pid,stamp)
    if rebound.get("status")!="BOUND_NO_WINDOW" or rebound.get("spawned") is not False:
        raise SystemExit("INDEPENDENT_REATTACH_FAILED:"+str(rebound.get("status")))
    activation=port.activate(rebound["bindingRef"],"u04-ci-host")
    detached=port.close_binding(rebound["bindingRef"],"u04-ci-host")
    still_running=mod.process_start(pid)==stamp
    evidence={"schema":"IWXA_U04_WAYLAND_INDEPENDENT_HOST/1",
              "status":"PASS" if (
                 caps[0]["supported"] and caps[1]["supported"] is False and
                 activation["status"]=="FOCUS_UNSUPPORTED" and
                 detached["status"]=="BINDING_CLOSED" and still_running
              ) else "FAIL",
              "hostProcessId":__import__("os").getpid(),"externalProcessId":pid,
              "processStamp":stamp,"reattachStatus":rebound["status"],
              "spawned":rebound["spawned"],"activationStatus":activation["status"],
              "focused":activation["focused"],"detachStatus":detached["status"],
              "processSurvived":still_running,"windowRef":None}
    receipt.write_text(json.dumps(evidence,sort_keys=True),encoding="utf-8")
    if evidence["status"]!="PASS":raise SystemExit("INDEPENDENT_HOST_VERIFICATION_FAILED")

if __name__=="__main__":main()
