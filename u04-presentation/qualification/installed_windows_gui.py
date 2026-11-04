"""Installed Windows Tauri GUI -> real independent Python/Tk editor -> reattach.

Native HWND and foreground ownership are independently read with user32.
Never call a native command directly to simulate the GUI button click.
"""
from __future__ import annotations
import ctypes
import json
import os
from pathlib import Path
import subprocess
import time

import psutil
from pywinauto import Application, mouse

ROOT = Path(__file__).resolve().parents[2]
OUTPUT = ROOT / "artifacts" / "u04" / "windows-gui"
OUTPUT.mkdir(parents=True, exist_ok=True)
USER32 = ctypes.windll.user32
USER32.GetWindowThreadProcessId.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_ulong)]
USER32.GetWindowThreadProcessId.restype = ctypes.c_ulong
USER32.GetForegroundWindow.restype = ctypes.c_void_p

def check(test, code):
    if not test:
        raise RuntimeError(code)

def foreground_pid():
    handle = USER32.GetForegroundWindow()
    pid = ctypes.c_ulong()
    USER32.GetWindowThreadProcessId(handle, ctypes.byref(pid))
    return pid.value

def wait_receipt(path, operation, previous, host_pid, timeout=25):
    limit = time.monotonic() + timeout
    while time.monotonic() < limit:
        if path.is_file():
            try:
                raw = path.read_bytes()
                record = json.loads(raw)
            except (ValueError, OSError):
                record = None
                raw = None
            if record and raw != previous and record.get("operation") == operation and record.get("host_pid") == host_pid:
                return raw, record["response"]
        time.sleep(.1)
    raise RuntimeError(f"U04_NO_RECEIPT_{operation}")

def wait_gui(process):
    limit = time.monotonic() + 60
    while time.monotonic() < limit:
        check(process.poll() is None, "HOST_EXITED_BEFORE_WINDOW")
        try:
            app = Application(backend="uia").connect(process=process.pid, timeout=1)
            candidate = app.window(title_re=".*IWXA Universal Program CI.*")
            if candidate.exists(timeout=.2) and candidate.is_visible():
                return candidate
        except Exception:
            pass
        time.sleep(.25)
    raise RuntimeError("INSTALLED_U04_GUI_NOT_FOUND")

def button_center(window, operation):
    targets = {"open":(35,78,138),"activate":(174,112,28),
               "closeBinding":(101,64,160)}
    goal = targets[operation]
    image = window.capture_as_image().convert("RGB")
    pixels = image.load()
    xs,ys = [],[]
    for y in range(85,min(260,image.height),3):
        for x in range(20,min(680,image.width),3):
            rgb = pixels[x,y]
            if all(abs(rgb[i]-goal[i])<=12 for i in range(3)):
                xs.append(x)
                ys.append(y)
    if len(xs)<45 or max(xs)-min(xs)<75 or max(ys)-min(ys)<20:
        return None
    return ((min(xs)+max(xs))//2,(min(ys)+max(ys))//2)

def wait_mode_ready(window):
    limit = time.monotonic()+65
    while time.monotonic()<limit:
        try:
            if all(button_center(window,op) for op in
                    ("open","activate","closeBinding")):
                return
        except Exception:
            pass
        time.sleep(.4)
    raise RuntimeError("U04_BUTTONS_NOT_VISIBLE")

def click_operation(host, window, receipt, operation, previous):
    window.set_focus()
    center = button_center(window,operation)
    check(center is not None,"U04_BUTTON_NOT_VISIBLE_"+operation)
    rect = window.rectangle()
    if receipt.exists(): receipt.unlink()
    mouse.click(button="left",coords=(rect.left+center[0],rect.top+center[1]))
    return wait_receipt(receipt,operation,None,host.pid)

def run_host(exe, env, receipt):
    if receipt.exists():
        receipt.unlink()
    host = subprocess.Popen([str(exe)],env=env,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
    window = wait_gui(host)
    wait_mode_ready(window)
    return host,window

def main():
    host_exe = Path(os.environ["IWXA_INSTALLED_GUI_EXE"]).resolve()
    check(host_exe.is_file(),"HOST_NOT_INSTALLED")
    python = Path(os.environ["IWXA_U04_PYTHONW"]).resolve()
    check(python.is_file() and python.name.lower()=="pythonw.exe","PYTHONW_NOT_AVAILABLE")
    receipt = Path(os.environ["RUNNER_TEMP"]) / "iwxa-u04-receipt.json"
    document = Path(os.environ["RUNNER_TEMP"]) / "iwxa-u04-user-document.txt"
    document.write_text("USER WORK IS PRESERVED",encoding="utf8")
    fixture = ROOT / "u04-presentation" / "qualification" / "external_editor.py"
    env = dict(os.environ,
        IWXA_U04_QUALIFICATION="1",IWXA_U04_TARGET_EXE=str(python),
        IWXA_U04_TARGET_ARGS=json.dumps([str(fixture),str(document)]),
        IWXA_U04_RECEIPT=str(receipt))
    report={"schema":"IWXA_U04_INSTALLED_WINDOWS_GUI/1","status":"FAIL","sessions":[]}
    external = None
    host = None
    try:
        host,window = run_host(host_exe,env,receipt)
        last, opened = click_operation(host,window,receipt,"open",None)
        check(opened["status"] in ("WINDOW_OBSERVED","LAUNCHED_NO_WINDOW"),"OPEN_NOT_LAUNCHED")
        pid = opened["processId"]
        stamp = opened["processStamp"]
        external = psutil.Process(pid)
        check(external.is_running(),"EXTERNAL_GUI_NOT_RUNNING")
        report["sessions"].append({"host_pid":host.pid,"external_pid":pid,"process_stamp":stamp,"open":opened["status"]})
        # Native HWND focus is an OS observation, not a fabricated UI receipt.
        for i in range(2):
            last, focused = click_operation(host,window,receipt,"activate",last)
            check(focused["status"]=="WINDOW_FOCUSED" and focused["focused"],"OS_WINDOW_FOCUS_NOT_CONFIRMED")
            check(foreground_pid()==pid,"EXTERNAL_FOREGROUND_PID_MISMATCH")
        last, detached = click_operation(host,window,receipt,"closeBinding",last)
        check(detached["status"]=="BINDING_CLOSED","BINDING_NOT_DETACHED")
        window.close()
        host.wait(timeout=20)
        host = None
        check(external.is_running(),"HOST_CLOSED_EXTERNAL_PROGRAM")
        check(document.read_text(encoding="utf8")=="USER WORK IS PRESERVED","USER_DOCUMENT_CHANGED")
        # New desktop host must rebind to the exact same process without spawning.
        env["IWXA_U04_REATTACH_PID"]=str(pid)
        env["IWXA_U04_REATTACH_STAMP"]=stamp
        host,window=run_host(host_exe,env,receipt)
        last, reopened=click_operation(host,window,receipt,"open",None)
        check(reopened["processId"]==pid and reopened["processStamp"]==stamp and not reopened["spawned"],
              "REATTACH_CREATED_NEW_PROCESS")
        last,focused=click_operation(host,window,receipt,"activate",last)
        check(focused["status"]=="WINDOW_FOCUSED" and foreground_pid()==pid,
              "REATTACHED_WINDOW_NOT_FOCUSED")
        window.close()
        host.wait(timeout=20)
        host = None
        check(external.is_running(),"RESTARTED_HOST_KILLED_EXTERNAL_PROGRAM")
        check(document.read_text(encoding="utf8")=="USER WORK IS PRESERVED","USER_DOCUMENT_LOST")
        report["sessions"].append({"reattached_host":True,"same_process":True,"focused":True})
        report["user_document_preserved"]=True
        report["external_survived_host_close"]=True
        report["embed_supported"]=False
        report["status"]="PASS"
        print("U04_REAL_INSTALLED_WINDOWS_GUI_PASS",flush=True)
    finally:
        OUTPUT.joinpath("report.json").write_text(json.dumps(report,indent=2),encoding="utf8")
        if host and host.poll() is None:
            host.terminate()
            try:host.wait(timeout=8)
            except subprocess.TimeoutExpired:host.kill()
        # This process was created solely as a disposable qualification fixture;
        # test cleanup is NOT bridge's closeBinding.
        if external and external.is_running():
            external.terminate()
            try:external.wait(timeout=8)
            except psutil.TimeoutExpired:external.kill()

if __name__=="__main__":
    main()
