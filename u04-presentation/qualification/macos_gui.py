"""macOS native AppKit focus qualification: actual independent window + OS readback.

If a hosted runner has no interactive WindowServer, report ENVIRONMENT_UNAVAILABLE.
That is not a native GUI PASS. Never convert a false focus into success.
"""
import json
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

def main():
    report={"schema":"IWXA_U04_MACOS_GUI/1","status":"NOT_TESTED","native_process_observed":False,
            "real_focus_verified":False,"external_survived_host_exit":False,
            "windowRef":None,"embeddedSupported":False}
    child_pid=None
    try:
        with tempfile.TemporaryDirectory() as home:
            document=Path(home)/"user-document.txt"
            document.write_text("PRESERVED MACOS USER CONTENT",encoding="utf-8")
            # LaunchServices is responsible for real GUI apps on macOS. A raw CLI
            # executable is not a valid substitute for a registered .app bundle.
            opened=subprocess.run(["open","-n","-a",str(APP),"--args",str(document)],
                                  capture_output=True,text=True,timeout=15)
            if opened.returncode!=0:
                report["status"]="ENVIRONMENT_UNAVAILABLE"
                report["reason"]="LAUNCH_SERVICES_REFUSED"
                report["launch_error"]=opened.stderr[:180]
                return
            observation=None
            for _ in range(80):
                lookup=subprocess.run([str(PORT),"lookup",BUNDLE_ID],
                                      capture_output=True,text=True,timeout=12)
                try:
                    found=json.loads(lookup.stdout)
                except ValueError:
                    found={"status":"NATIVE_RESULT_INVALID"}
                if found.get("status")=="PROCESS_OBSERVED":
                    child_pid=found.get("processId")
                    observation=invoke("inspect",child_pid)
                    if observation.get("status")=="PROCESS_OBSERVED" and observation.get("isFinishedLaunching"):
                        break
                time.sleep(.2)
            report["external_pid"]=child_pid
            if not observation or observation.get("status")!="PROCESS_OBSERVED" or not observation.get("isFinishedLaunching"):
                report["status"]="ENVIRONMENT_UNAVAILABLE"
                report["reason"]="APPKIT_RUNNING_APPLICATION_NOT_OBSERVED"
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
                response=invoke("activate",child_pid)
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
            # The CLI host process has exited after each operation; the app survives.
            alive=invoke("inspect",child_pid)
            report["external_survived_host_exit"]=(alive.get("status")=="PROCESS_OBSERVED" and
                                                   alive.get("launchTimestamp")==report["launchTimestamp"])
            report["user_document_preserved"]=document.read_text(encoding="utf-8")=="PRESERVED MACOS USER CONTENT"
            report["status"]="PASS" if report["external_survived_host_exit"] and report["user_document_preserved"] else "FAIL"
    finally:
        OUT.joinpath("report.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
        if isinstance(child_pid,int) and child_pid>0:
            # CI-owned fixture cleanup is separate from U04.closeBinding.
            import signal
            try:os.kill(child_pid,signal.SIGTERM)
            except ProcessLookupError:pass
        print("U04_MACOS_REAL_GUI_"+report["status"],json.dumps(report),flush=True)

if __name__=="__main__":main()
