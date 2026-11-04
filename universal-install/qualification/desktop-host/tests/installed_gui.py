"""Installed real Windows Tauri/WebView GUI: actual click -> native Rust -> jq.

Run on a fresh hosted profile. Do not treat a backend fixture or DOM simulation
as an installed GUI PASS. Preserve screenshots and measurements on failure.
"""
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone

import psutil
from pywinauto import Application

ROOT=Path(__file__).resolve().parents[4]
OUT=ROOT/"artifacts"/"universal-program"/"windows-gui"
OUT.mkdir(parents=True,exist_ok=True)
REPORT=OUT/"report.json"
RUNS=[]

def check(condition,code):
    if not condition:
        raise RuntimeError(code)

def launch_and_click(executable,receipt,attempt):
    if receipt.exists():
        receipt.unlink()
    process=subprocess.Popen([str(executable)],stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL,env=os.environ.copy())
    started=time.perf_counter()
    entry={"attempt":attempt,"pid":process.pid,"receipt":str(receipt)}
    try:
        window=None
        for _ in range(100):
            if process.poll() is not None:
                raise RuntimeError("GUI_PROCESS_EXITED_BEFORE_WINDOW")
            try:
                app=Application(backend="uia").connect(process=process.pid,timeout=1)
                win=app.window(title_re=".*IWXA Universal Program CI.*")
                if win.exists(timeout=0.25) and win.is_visible():
                    window=win
                    break
            except Exception:
                pass
            time.sleep(0.2)
        check(window is not None,"INSTALLED_GUI_WINDOW_NOT_FOUND")
        entry["cold_window_ms"]=round((time.perf_counter()-started)*1000,3)
        measured=psutil.Process(process.pid)
        entry["idle_rss_bytes"]=measured.memory_info().rss
        before=measured.cpu_times()
        time.sleep(0.3)
        after=measured.cpu_times()
        entry["idle_cpu_ms_over_300ms"]=round(
            ((after.user+after.system)-(before.user+before.system))*1000,3)
        window.set_focus()
        window.capture_as_image().save(str(OUT/f"before-click-{attempt}.png"))
        controls=[]
        try:
            controls=[{"type":c.element_info.control_type,"name":c.window_text()[:160]}
                      for c in window.descendants()[:80]]
        except Exception as error:
            controls=[{"error":str(error)}]
        entry["controls_before_click"]=controls
        print("GUI_CONTROLS_BEFORE_CLICK: "+json.dumps(controls,ensure_ascii=False),flush=True)
        # Prefer the actual HTML button exposed through WebView2 UI Automation.
        button=None
        try:
            button=window.child_window(title_re=".*Execute jq.*",control_type="Button")
            if not button.exists(timeout=1):
                button=None
        except Exception:
            button=None
        if button is not None:
            button.click_input()
            entry["click_method"]="uia_button"
        else:
            # Fallback: native Windows click at documented test-fixture position.
            window.click_input(coords=(145,184))
            entry["click_method"]="native_coordinate"
        entry["clicked"]=True
        time.sleep(1.0)
        window.capture_as_image().save(str(OUT/f"after-click-{attempt}.png"))
        try:
            entry["controls_after_click"]=[
                {"type":c.element_info.control_type,"name":c.window_text()[:160]}
                for c in window.descendants()[:80]]
            print("GUI_CONTROLS_AFTER_CLICK: "+
                  json.dumps(entry["controls_after_click"],ensure_ascii=False),flush=True)
        except Exception as error:
            entry["ui_inspection_error"]=str(error)
        for _ in range(120):
            if receipt.exists():
                try:
                    body=json.loads(receipt.read_text(encoding="utf-8"))
                except Exception:
                    body=None
                if body and body.get("status")=="PASS":
                    entry["receipt_data"]=body
                    entry["click_to_verified_ms"]=round((time.perf_counter()-started)*1000,3)
                    break
            if process.poll() is not None:
                raise RuntimeError("GUI_PROCESS_EXITED_BEFORE_PROOF")
            time.sleep(0.2)
        if "receipt_data" not in entry:
            try:
                window.capture_as_image().save(str(OUT/f"failed-receipt-{attempt}.png"))
            except Exception:
                pass
            details=json.dumps(entry.get("controls_after_click",[]),ensure_ascii=False)
            raise RuntimeError("REAL_GUI_CLICK_NO_NATIVE_RECEIPT: "+details[:1200])
        check(entry["receipt_data"]["pid"]==process.pid,"RECEIPT_PID_MISMATCH")
        check(entry["receipt_data"]["real_cli_output"]=="42","REAL_JQ_RESULT_INVALID")
        check(entry["receipt_data"]["native_contract"]=="IWXA_PROGRAM_CONNECTION/1.0.0",
              "CONTRACT_MISMATCH")
        try:
            window.capture_as_image().save(str(OUT/f"gui-{attempt}.png"))
        except Exception as error:
            entry["screenshot_error"]=str(error)
        return entry
    finally:
        if process.poll() is None:
            process.terminate()
            try:process.wait(timeout=12)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=6)

def main():
    exe=Path(os.environ["IWXA_INSTALLED_GUI_EXE"]).resolve()
    check(exe.is_file(),"INSTALLED_EXE_NOT_FOUND")
    check(str(exe).lower().startswith(str(Path(os.environ["LOCALAPPDATA"])).lower()),
          "NOT_INSTALLED_IN_ISOLATED_PROFILE")
    check(Path(os.environ["IWXA_CLI_EXECUTABLE"]).is_file(),"REAL_JQ_MISSING")
    receipt=Path(os.environ["IWXA_GUI_RECEIPT"]).resolve()
    check(receipt.parent.is_dir(),"RECEIPT_DIR_NOT_FOUND")
    user_file=receipt.parent/"persistent-user-file.txt"
    user_file.write_text("KEEP",encoding="utf-8")
    result={"schema":"IWXA_INSTALLED_WINDOWS_GUI_QUALIFICATION/1",
            "status":"FAIL","actual_gui":True,"real_install":True,
            "timestamp":datetime.now(timezone.utc).isoformat(),
            "executable":str(exe),"runs":RUNS}
    try:
        for attempt in (1,2):
            RUNS.append(launch_and_click(exe,receipt,attempt))
            check(user_file.read_text(encoding="utf-8")=="KEEP","USER_FILE_CHANGED")
        result["status"]="PASS"
        result["cold_window_ms"]=[r["cold_window_ms"] for r in RUNS]
        result["idle_rss_bytes"]=[r["idle_rss_bytes"] for r in RUNS]
        result["measurement_note"]="Actual installed host only; no baseline GUI without integration"
        print(json.dumps(result,ensure_ascii=False,sort_keys=True))
    except Exception as error:
        result["error"]=str(error)
        raise
    finally:
        REPORT.write_text(json.dumps(result,indent=2,ensure_ascii=False),encoding="utf-8")

if __name__=="__main__":
    main()
