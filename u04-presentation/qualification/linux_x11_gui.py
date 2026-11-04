"""Real X11 host GUI -> independent editor -> OS focus -> host exit -> editor stays alive.

Uses xdotool XTEST mouse events against a visible Tk host, never direct callbacks
as a substitute for GUI interaction. Run under an X11 window manager (openbox).
"""
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import tkinter as tk

ROOT=Path(__file__).resolve().parents[2]
path=ROOT/"u04-presentation"/"native"/"linux_x11.py"
spec=importlib.util.spec_from_file_location("u04_linux_x11",path)
mod=importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

def cmd(*args):
    p=subprocess.run(["xdotool",*map(str,args)],capture_output=True,text=True,timeout=7)
    if p.returncode: return ""
    return p.stdout.strip()

def check(test,reason):
    if not test: raise RuntimeError(reason)

def wait_for(predicate,timeout=15):
    limit=time.monotonic()+timeout
    while time.monotonic()<limit:
        result=predicate()
        if result:return result
        time.sleep(.15)
    raise RuntimeError("NATIVE_WINDOW_NOT_READY")

def main():
    report={"schema":"IWXA_U04_LINUX_X11_GUI/1","status":"FAIL"}
    out=ROOT/"artifacts"/"u04"/"linux-x11"
    out.mkdir(parents=True,exist_ok=True)
    with tempfile.TemporaryDirectory() as home:
        document=Path(home)/"editable.txt"
        document.write_text("PRESERVE REAL USER DOCUMENT",encoding="utf8")
        target=(sys.executable,[str(ROOT/"u04-presentation"/"qualification"/"external_editor.py"),str(document)])
        port=mod.X11Port(lambda a,h,s:{"approved": a=="editor" and h=="gui-host",
             "executable":target[0],"args":target[1]})
        state={"binding":None,"external":None,"events":[]}
        gui=tk.Tk()
        gui.title("IWXA U04 Independent X11 Host")
        gui.geometry("450x210+40+40")
        label=tk.StringVar(value="U04 READY")
        tk.Label(gui,textvariable=label).pack()
        def act(name):
            if name=="open":
                value=port.open("editor","gui-host",{})
                state["binding"]=value.get("bindingRef")
                state["external"]=value.get("processId")
            elif name=="activate":
                value=port.activate(state["binding"],"gui-host")
            else:
                value=port.close_binding(state["binding"],"gui-host")
            state["events"].append({"op":name,**value})
            label.set(value["status"])
        for name in ("open","activate","closeBinding"):
            tk.Button(gui,text=name,command=lambda n=name:act(n),width=21).pack()
        gui.update()
        def host_observation():
            matches=cmd("search","--name","^IWXA U04 Independent X11 Host$")
            return matches.splitlines()[0] if matches else None
        host_window=wait_for(host_observation)
        try:
            def real_click(name):
                index={"open":0,"activate":1,"closeBinding":2}[name]
                cmd("windowactivate","--sync",host_window)
                cmd("mousemove","--window",host_window,180,43+index*31)
                before=len(state["events"])
                cmd("click","1")
                def result():
                    gui.update()
                    return len(state["events"])>before
                wait_for(result)
                return state["events"][-1]
            opened=real_click("open")
            check(opened["status"] in ("LAUNCHED_NO_WINDOW","WINDOW_OBSERVED"),"X11_LAUNCH_FAILED")
            pid=opened["processId"]
            window=wait_for(lambda:port._window(pid))
            check(bool(window),"REAL_EDITOR_WINDOW_NOT_FOUND")
            for _ in range(2):
                focused=real_click("activate")
                check(focused["status"]=="WINDOW_FOCUSED","X11_OS_FOCUS_FAILED")
                check(cmd("getactivewindow")==window,"X11_FOREGROUND_WINDOW_MISMATCH")
            detached=real_click("closeBinding")
            check(detached["status"]=="BINDING_CLOSED","X11_DETACH_FAILED")
            gui.destroy()
            check(mod._read_start(pid)==opened["processStamp"],"EDITOR_DIED_WITH_HOST")
            check(document.read_text(encoding="utf8")=="PRESERVE REAL USER DOCUMENT","USER_DOCUMENT_CHANGED")
            report.update(status="PASS",external_window=window,external_pid=pid,
                          actual_gui_clicks=4,focus_events=2,
                          independent_host_closed=True,external_survived=True,
                          user_document_preserved=True,embedding_supported=False)
            print("U04_REAL_X11_GUI_PASS",json.dumps(report),flush=True)
        finally:
            if state["external"]:
                # Disposable test fixture cleanup, not a U04 closeBinding operation.
                try:os.kill(state["external"],15)
                except ProcessLookupError:pass
            try:gui.destroy()
            except tk.TclError:pass
            (out/"report.json").write_text(json.dumps(report,indent=2),encoding="utf8")

if __name__=="__main__":main()
