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

def invoke(operation,pid):
    r=subprocess.run([str(PORT),operation,str(pid)],capture_output=True,text=True,timeout=12)
    try:return json.loads(r.stdout)
    except ValueError:return {"status":"NATIVE_RESULT_INVALID","stderr":r.stderr[:180]}

def main():
    report={"schema":"IWXA_U04_MACOS_GUI/1","status":"NOT_TESTED","native_process_observed":False,
            "real_focus_verified":False,"external_survived_host_exit":False,
            "windowRef":None,"embeddedSupported":False}
    child=None
    try:
        with tempfile.TemporaryDirectory() as home:
            document=Path(home)/"user-document.txt"
            document.write_text("PRESERVED MACOS USER CONTENT",encoding="utf-8")
            child=subprocess.Popen([str(APP),str(document)],stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                                   start_new_session=True)
            report["external_pid"]=child.pid
            observation=None
            for _ in range(80):
                observation=invoke("inspect",child.pid)
                if observation.get("status")=="PROCESS_OBSERVED" and observation.get("isFinishedLaunching"):
                    break
                if child.poll() is not None:
                    report["status"]="ENVIRONMENT_UNAVAILABLE"
                    report["reason"]="GUI_PROCESS_TERMINATED"
                    return
                time.sleep(.2)
            if observation.get("status")!="PROCESS_OBSERVED" or not observation.get("isFinishedLaunching"):
                report["status"]="ENVIRONMENT_UNAVAILABLE"
                report["reason"]="APPKIT_RUNNING_APPLICATION_NOT_OBSERVED"
                return
            report["native_process_observed"]=True
            report["launchTimestamp"]=observation.get("launchTimestamp")
            # True GUI verification demands native OS foreground readback.
            attempts=[]
            for _ in range(3):
                response=invoke("activate",child.pid)
                foreground=invoke("foreground",child.pid)
                attempts.append({"activation":response.get("status"),
                                 "foreground":foreground.get("status"),
                                 "frontmost_pid":foreground.get("frontmostProcessId")})
                if response.get("status")=="PROCESS_FOCUSED" and foreground.get("frontmostProcessId")==child.pid:
                    report["real_focus_verified"]=True
                    break
                time.sleep(.3)
            report["focus_attempts"]=attempts
            if not report["real_focus_verified"]:
                report["status"]="NOT_QUALIFIED"
                report["reason"]="OS_FRONTMOST_NOT_CONFIRMED"
                return
            # The CLI host process has exited after each operation; the app survives.
            report["external_survived_host_exit"]=child.poll() is None
            report["user_document_preserved"]=document.read_text(encoding="utf-8")=="PRESERVED MACOS USER CONTENT"
            report["status"]="PASS" if report["external_survived_host_exit"] and report["user_document_preserved"] else "FAIL"
    finally:
        OUT.joinpath("report.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
        if child and child.poll() is None:
            child.terminate()
            try:child.wait(timeout=6)
            except subprocess.TimeoutExpired:child.kill()
        print("U04_MACOS_REAL_GUI_"+report["status"],json.dumps(report),flush=True)

if __name__=="__main__":main()
