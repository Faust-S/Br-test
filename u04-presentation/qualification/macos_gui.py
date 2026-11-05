"""macOS native AppKit focus qualification: actual independent window + OS readback.

If a hosted runner has no interactive WindowServer, report ENVIRONMENT_UNAVAILABLE.
That is not a native GUI PASS. Never convert a false focus into success.
"""
import json
import importlib.util
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/"artifacts"/"u04"/"macos-gui"
OUT.mkdir(parents=True,exist_ok=True)
PORT=Path(os.environ["IWXA_U04_APPKIT_PORT"])
APP=Path(os.environ["IWXA_U04_APPKIT_FIXTURE"])
BUNDLE_ID="ru.iwxa.u04.ci.external-editor"

def invoke(operation,pid):
    r=subprocess.run([str(PORT),operation,str(pid)],capture_output=True,text=True,timeout=12)
    try:return json.loads(r.stdout)
    except ValueError:return {"status":"NATIVE_RESULT_INVALID","stderr":r.stderr[:180]}

SPEC=importlib.util.spec_from_file_location(
    "u04_macos",ROOT/"u04-presentation"/"native"/"macos_appkit.py")
mod=importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(mod)

def approval(app_ref,host_ref,request):
    allowed=app_ref=="editor" and host_ref=="mac-host"
    if isinstance(request,dict) and request.get("operation")=="reattach":
        return {"approved":allowed,"pid":request.get("pid"),"stamp":request.get("stamp")}
    return {"approved":allowed,"bundle_path":str(APP),"bundle_id":BUNDLE_ID,
            "args":[request["document"]]}

def main():
    report={"schema":"IWXA_U04_MACOS_GUI/1","status":"NOT_TESTED","native_process_observed":False,
            "real_focus_verified":False,"external_survived_host_exit":False,
            "windowRef":None,"embeddedSupported":False}
    child_pid=None
    try:
        with tempfile.TemporaryDirectory() as home:
            document=Path(home)/"user-document.txt"
            document.write_text("PRESERVED MACOS USER CONTENT",encoding="utf-8")
            port=mod.MacOSPort(PORT,approval,platform="darwin")
            opened=port.open("editor","mac-host",{"document":str(document)})
            child_pid=opened.get("processId")
            report["open_status"]=opened.get("status")
            report["external_pid"]=child_pid
            if opened.get("status")!="PROCESS_OBSERVED" or not isinstance(child_pid,int):
                report["status"]="NOT_QUALIFIED"
                report["reason"]="APPKIT_PORT_LAUNCH_NOT_OBSERVED"
                return
            observation=invoke("inspect",child_pid)
            if observation.get("status")!="PROCESS_OBSERVED" or not observation.get("isFinishedLaunching"):
                report["status"]="NOT_QUALIFIED"
                report["reason"]="APPKIT_NATIVE_INSPECT_MISMATCH"
                return
            report["native_process_observed"]=True
            report["launchTimestamp"]=observation.get("launchTimestamp")
            report["bundle_id"]=BUNDLE_ID
            if report["launchTimestamp"]=="NOT_OBSERVED":
                report["status"]="NOT_QUALIFIED"
                report["reason"]="PROCESS_START_IDENTITY_UNVERIFIED"
                return
            # True GUI verification demands native OS foreground readback.
            attempts=[]
            for _ in range(3):
                response=port.activate(opened["bindingRef"],"mac-host")
                foreground=invoke("foreground",child_pid)
                attempts.append({"activation":response.get("status"),
                                 "foreground":foreground.get("status"),
                                 "frontmost_pid":foreground.get("frontmostProcessId")})
                if response.get("status")=="PROCESS_FOCUSED" and foreground.get("frontmostProcessId")==child_pid:
                    report["real_focus_verified"]=True
                    break
                time.sleep(.3)
            report["focus_attempts"]=attempts
            if not report["real_focus_verified"]:
                report["status"]="NOT_QUALIFIED"
                report["reason"]="OS_FRONTMOST_NOT_CONFIRMED"
                return
            # Detach the original bridge. A fresh bridge may reconnect only
            # with host approval and an exact PID + start identity.
            detached=port.close_binding(opened["bindingRef"],"mac-host")
            report["binding_closed_without_termination"]=(
                detached.get("status")=="BINDING_CLOSED" and
                detached.get("processTerminated") is False)
            restored=mod.MacOSPort(PORT,approval,platform="darwin")
            reattached=restored.reattach("editor","mac-host",child_pid,report["launchTimestamp"])
            report["reattach_same_process"]=(
                reattached.get("status")=="PROCESS_OBSERVED" and
                reattached.get("spawned") is False and
                reattached.get("processId")==child_pid)
            if report["reattach_same_process"]:
                again=restored.activate(reattached["bindingRef"],"mac-host")
                foreground=invoke("foreground",child_pid)
                report["reattach_focus_verified"]=(
                    again.get("focused") is True and
                    foreground.get("frontmostProcessId")==child_pid)
                restored.close_binding(reattached["bindingRef"],"mac-host")
            else:report["reattach_focus_verified"]=False
            # The CLI host process has exited after each operation; the app survives.
            alive=invoke("inspect",child_pid)
            report["external_survived_host_exit"]=(alive.get("status")=="PROCESS_OBSERVED" and
                                                   alive.get("launchTimestamp")==report["launchTimestamp"])
            report["user_document_preserved"]=document.read_text(encoding="utf-8")=="PRESERVED MACOS USER CONTENT"
            report["status"]="PASS" if all(report.get(k) is True for k in (
                "external_survived_host_exit","user_document_preserved",
                "binding_closed_without_termination","reattach_same_process",
                "reattach_focus_verified")) else "NOT_QUALIFIED"
    finally:
        OUT.joinpath("report.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
        if isinstance(child_pid,int) and child_pid>0:
            # CI-owned fixture cleanup is separate from U04.closeBinding.
            import signal
            try:os.kill(child_pid,signal.SIGTERM)
            except ProcessLookupError:pass
        print("U04_MACOS_REAL_GUI_"+report["status"],json.dumps(report),flush=True)

if __name__=="__main__":main()
