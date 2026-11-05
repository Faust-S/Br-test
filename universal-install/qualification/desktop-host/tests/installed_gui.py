"""Actual Windows NSIS -> installed Tauri/WebView2 -> OS click -> Rust -> jq.

A visible native HWND is not a loaded web page. Wait for a JS-armed, visually
blue button before clicking. Never dispatch browser JS or mock native IPC.
"""
import hashlib
import json
import os
from pathlib import Path
import subprocess
import time
from datetime import datetime, timezone

import psutil
from pywinauto import Application, mouse

ROOT = Path(__file__).resolve().parents[4]
OUT = ROOT / "artifacts" / "universal-program" / "windows-gui"
OUT.mkdir(parents=True, exist_ok=True)
REPORT = OUT / "report.json"
RUNS = []
BLUE_READY = (35, 78, 138)       # button.ready:not(:disabled)
GREEN_VERIFIED = (225, 245, 232) # #result.verified
FRONTEND_TIMEOUT_S = 90
RECEIPT_TIMEOUT_S = 20
VISUAL_RESULT_TIMEOUT_S = 12


def check(condition, code):
    if not condition:
        raise RuntimeError(code)


def sha256_file(path):
    with path.open('rb') as payload:
        return hashlib.file_digest(payload, 'sha256').hexdigest()


def color_region(image, target):
    """Find a sizable solid-color UI region in real window pixels, not DOM."""
    pixels = image.convert("RGB").load()
    width, height = image.size
    xs, ys = [], []
    for y in range(int(height * 0.22), int(height * 0.78), 4):
        for x in range(16, min(int(width * 0.62), 430), 4):
            actual = pixels[x, y]
            if all(abs(actual[i] - target[i]) <= 11 for i in range(3)):
                xs.append(x)
                ys.append(y)
    if len(xs) < 110:
        return None
    left, right, top, bottom = min(xs), max(xs), min(ys), max(ys)
    if right - left < 110 or bottom - top < 22:
        return None
    return {"left": left, "right": right, "top": top, "bottom": bottom,
            "samples": len(xs), "center_x": (left + right) // 2,
            "center_y": (top + bottom) // 2}


def wait_for_visual(window, process, target, timeout_s, stage, attempt):
    started = time.perf_counter()
    stable = 0
    last = None
    while time.perf_counter() - started < timeout_s:
        check(process.poll() is None, "GUI_PROCESS_EXITED_DURING_" + stage)
        try:
            frame = window.capture_as_image()
            last = frame
            bounds = color_region(frame, target)
        except Exception:
            bounds = None
        if bounds:
            stable += 1
            if stable >= 2:
                frame.save(str(OUT / f"{stage}-{attempt}.png"))
                return bounds
        else:
            stable = 0
        time.sleep(0.5)
    if last is not None:
        last.save(str(OUT / f"{stage}-timeout-{attempt}.png"))
    raise RuntimeError(f"GUI_{stage.upper()}_NOT_VISIBLE_WITHIN_{timeout_s}S")


def tree_resources(process):
    processes = [process]
    try:
        processes += process.children(recursive=True)
    except psutil.Error:
        pass
    rss, cpu_ms, counted = 0, 0.0, 0
    for item in processes:
        try:
            rss += item.memory_info().rss
            cpu = item.cpu_times()
            cpu_ms += (cpu.user + cpu.system) * 1000
            counted += 1
        except psutil.Error:
            pass
    return {"tree_rss_bytes": rss, "tree_cpu_ms": round(cpu_ms, 3),
            "tree_processes": counted}


def launch_and_click(executable, receipt, attempt):
    if receipt.exists():
        receipt.unlink()  # Previous process cannot prove a new session.
    started = time.perf_counter()
    process = subprocess.Popen([str(executable)], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, env=os.environ.copy())
    entry = {"attempt": attempt, "pid": process.pid, "receipt": str(receipt)}
    RUNS.append(entry)
    try:
        window = None
        for _ in range(100):
            check(process.poll() is None, "GUI_PROCESS_EXITED_BEFORE_WINDOW")
            try:
                app = Application(backend="uia").connect(process=process.pid, timeout=1)
                candidate = app.window(title_re=".*IWXA Universal Program CI.*")
                if candidate.exists(timeout=0.25) and candidate.is_visible():
                    window = candidate
                    break
            except Exception:
                pass
            time.sleep(0.2)
        check(window is not None, "INSTALLED_GUI_WINDOW_NOT_FOUND")
        entry["native_window_ms"] = round((time.perf_counter() - started) * 1000, 3)
        measured = psutil.Process(process.pid)

        # The old test clicked a blank white WebView before its JS had loaded.
        # In the fixture the button is disabled and gray until JS registers
        # its click listener; only then does it become the target blue.
        ready_button = wait_for_visual(
            window, process, BLUE_READY, FRONTEND_TIMEOUT_S, "frontend-ready", attempt)
        entry["frontend_ready_ms"] = round((time.perf_counter() - started) * 1000, 3)
        entry["ready_button_pixels"] = ready_button
        idle_before = tree_resources(measured)
        time.sleep(0.3)
        idle_after = tree_resources(measured)
        entry["idle_tree_rss_bytes"] = idle_after["tree_rss_bytes"]
        entry["idle_tree_processes"] = idle_after["tree_processes"]
        entry["idle_tree_cpu_ms_over_300ms"] = round(
            idle_after["tree_cpu_ms"] - idle_before["tree_cpu_ms"], 3)
        entry["native_process_rss_bytes"] = measured.memory_info().rss

        window.set_focus()
        rect = window.rectangle()
        x = rect.left + ready_button["center_x"]
        y = rect.top + ready_button["center_y"]
        entry["physical_click"] = {"x": x, "y": y, "method": "pywinauto.mouse.click"}
        click_started = time.perf_counter()
        mouse.click(button="left", coords=(x, y))
        entry["clicked"] = True

        body = None
        deadline = time.perf_counter() + RECEIPT_TIMEOUT_S
        while time.perf_counter() < deadline:
            check(process.poll() is None, "GUI_PROCESS_EXITED_BEFORE_PROOF")
            if receipt.exists():
                try:
                    body = json.loads(receipt.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    body = None
                if body and body.get("status") == "PASS":
                    break
            time.sleep(0.2)
        if not body or body.get("status") != "PASS":
            window.capture_as_image().save(str(OUT / f"failed-receipt-{attempt}.png"))
            raise RuntimeError("REAL_OS_CLICK_NO_NATIVE_RECEIPT")
        entry["receipt_data"] = body
        entry["click_to_native_receipt_ms"] = round(
            (time.perf_counter() - click_started) * 1000, 3)
        check(body.get("pid") == process.pid, "RECEIPT_PID_MISMATCH")
        check(body.get("real_cli_output") == "42", "REAL_JQ_RESULT_INVALID")
        check(body.get("native_contract") == "IWXA_PROGRAM_CONNECTION/1.0.0",
              "CONTRACT_MISMATCH")

        # A native receipt alone is insufficient: the actual window must
        # also display success. This rules out backend-only pseudo-GUI PASS.
        entry["visual_verified_pixels"] = wait_for_visual(
            window, process, GREEN_VERIFIED, VISUAL_RESULT_TIMEOUT_S,
            "verified-visible", attempt)
        entry["click_to_visible_result_ms"] = round(
            (time.perf_counter() - click_started) * 1000, 3)
        window.capture_as_image().save(str(OUT / f"gui-{attempt}.png"))
        return entry
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=12)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=6)


def main():
    exe = Path(os.environ["IWXA_INSTALLED_GUI_EXE"]).resolve()
    check(exe.is_file(), "INSTALLED_EXE_NOT_FOUND")
    check(str(exe).lower().startswith(
          str(Path(os.environ["LOCALAPPDATA"]).resolve()).lower() + os.sep),
          "NOT_INSTALLED_IN_ISOLATED_PROFILE")
    check(Path(os.environ["IWXA_CLI_EXECUTABLE"]).is_file(), "REAL_JQ_MISSING")
    source_sha = os.environ.get("GITHUB_SHA", "")
    check(len(source_sha) == 40 and all(ch in "0123456789abcdef" for ch in source_sha.lower()),
          "GUI_SOURCE_SHA_NOT_PINNED")
    installer_dir = ROOT / "universal-install/qualification/desktop-host/src-tauri/target/release/bundle/nsis"
    installers = list(installer_dir.glob("*.exe"))
    check(len(installers) == 1, "GUI_INSTALLER_PROVENANCE_AMBIGUOUS")
    receipt = Path(os.environ["IWXA_GUI_RECEIPT"]).resolve()
    check(receipt.parent.is_dir(), "RECEIPT_DIR_NOT_FOUND")
    user_file = receipt.parent / "persistent-user-file.txt"
    user_file.write_text("KEEP", encoding="utf-8")
    result = {"schema": "IWXA_INSTALLED_WINDOWS_GUI_QUALIFICATION/1",
              "status": "FAIL", "actual_gui": True, "real_install": True,
              "timestamp": datetime.now(timezone.utc).isoformat(),
              "executable": str(exe), "runs": RUNS}
    try:
        for attempt in (1, 2):
            launch_and_click(exe, receipt, attempt)
            check(user_file.read_text(encoding="utf-8") == "KEEP",
                  "USER_FILE_CHANGED")
        result["status"] = "PASS"
        result["native_window_ms"] = [r["native_window_ms"] for r in RUNS]
        result["frontend_ready_ms"] = [r["frontend_ready_ms"] for r in RUNS]
        result["click_to_visible_result_ms"] = [
            r["click_to_visible_result_ms"] for r in RUNS]
        result["idle_tree_rss_bytes"] = [r["idle_tree_rss_bytes"] for r in RUNS]
        result["measurement_note"] = (
            "Installed host incl. child processes; no baseline without SDK")
        print("INSTALLED_WINDOWS_GUI_TWO_RUNS_PASS", flush=True)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    except Exception as error:
        result["error"] = str(error)
        raise
    finally:
        REPORT.write_text(json.dumps(result, indent=2, ensure_ascii=False),
                          encoding="utf-8")


if __name__ == "__main__":
    main()
