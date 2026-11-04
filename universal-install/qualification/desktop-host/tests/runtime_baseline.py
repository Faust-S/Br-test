"""Paired Tauri/WebView2 resource observations on the same Windows CI runner.

Compare the EXACT installed IPC-enabled host with a separately compiled Tauri
binary displaying identical web assets, without its CliPort native commands.
No JS injection, fixture results, or synthetic browser.
"""
import json
import os
from pathlib import Path
import statistics
import subprocess
import time
from datetime import datetime, timezone

import psutil
from pywinauto import Application
from installed_gui import BLUE_READY, OUT, wait_for_visual, tree_resources

BASELINE_TIMEOUT_S = 90
REPORT = OUT / "overhead.json"


def require(condition, label):
    if not condition:
        raise RuntimeError(label)


def launch(executable, name, index):
    started = time.perf_counter()
    process = subprocess.Popen([str(executable)], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, env=os.environ.copy())
    record = {"candidate": name, "iteration": index, "pid": process.pid}
    try:
        window = None
        for _ in range(100):
            require(process.poll() is None, "BASELINE_PROCESS_EXITED_BEFORE_WINDOW")
            try:
                app = Application(backend="uia").connect(process=process.pid, timeout=1)
                candidate = app.window(title_re=".*IWXA Universal Program CI.*")
                if candidate.exists(timeout=0.25) and candidate.is_visible():
                    window = candidate
                    break
            except Exception:
                pass
            time.sleep(0.2)
        require(window is not None, "BASELINE_WINDOW_NOT_FOUND")
        record["native_window_ms"] = round((time.perf_counter()-started)*1000, 3)
        wait_for_visual(window, process, BLUE_READY, BASELINE_TIMEOUT_S,
                        "perf-ready", f"{name}-{index}")
        record["frontend_ready_ms"] = round((time.perf_counter()-started)*1000, 3)
        measured = psutil.Process(process.pid)
        before = tree_resources(measured)
        time.sleep(0.3)
        after = tree_resources(measured)
        record["process_tree_rss_bytes"] = after["tree_rss_bytes"]
        record["process_tree_count"] = after["tree_processes"]
        record["process_tree_idle_cpu_ms_over_300ms"] = round(
            after["tree_cpu_ms"] - before["tree_cpu_ms"], 3)
        record["native_process_rss_bytes"] = measured.memory_info().rss
        return record
    finally:
        if process.poll() is None:
            process.terminate()
            try:
                process.wait(timeout=12)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=6)


def summary(measured):
    out = {}
    for field in ("native_window_ms", "frontend_ready_ms",
                  "process_tree_rss_bytes", "native_process_rss_bytes",
                  "process_tree_idle_cpu_ms_over_300ms", "process_tree_count"):
        sdk = statistics.median(item[field] for item in measured if item["candidate"]=="sdk")
        base = statistics.median(item[field] for item in measured if item["candidate"]=="baseline")
        out[field] = {"sdk_median": sdk, "baseline_median": base,
                      "delta": round(sdk-base, 3),
                      "delta_percent": round((sdk-base)/base*100, 3) if base else None}
    return out


def main():
    sdk = Path(os.environ["IWXA_INSTALLED_GUI_EXE"]).resolve()
    baseline = Path(os.environ["IWXA_BASELINE_GUI_EXE"]).resolve()
    require(sdk.is_file() and baseline.is_file(), "BASELINE_BINARY_MISSING")
    require(sdk != baseline, "BASELINE_MUST_BE_DISTINCT")
    require(sdk.parent == baseline.parent, "BASELINE_MUST_SHARE_GUI_INSTALL_DIR")
    keep = sdk.parent/"qualification-user-data-keep.txt"
    keep.write_text("USER_DATA_PRESERVED", encoding="utf8")
    result = {
        "schema":"IWXA_TAURI_CLI_PORT_RELATIVE_OVERHEAD/1",
        "status":"FAIL", "timestamp":datetime.now(timezone.utc).isoformat(),
        "sdk_exe":str(sdk), "baseline_exe":str(baseline),
        "sdk_exe_bytes":sdk.stat().st_size,
        "baseline_exe_bytes":baseline.stat().st_size,
        "size_delta_bytes":sdk.stat().st_size-baseline.stat().st_size,
        "samples":[], "warmups":[],
        "note":("Both binaries use the same Tauri configuration, WebView2 and web assets; "
                "baseline lacks Rust port state and IPC. Isolated hosted-runner samples; "
                "not a statistical proof of a <5% product-performance budget.")
    }
    try:
        candidates = {"sdk":sdk, "baseline":baseline}
        for name in ("sdk","baseline"):
            result["warmups"].append(launch(candidates[name],name,0))
        # Alternating order balances runner caches and first-run penalties.
        for name in ("sdk","baseline","baseline","sdk","sdk","baseline"):
            idx = sum(r["candidate"] == name for r in result["samples"]) + 1
            result["samples"].append(launch(candidates[name],name,idx))
        require(len(result["samples"])==6, "BASELINE_SAMPLE_COUNT_INVALID")
        require(keep.read_text(encoding="utf8")=="USER_DATA_PRESERVED",
                "BASELINE_CHANGED_USER_FILE")
        result["paired_medians"]=summary(result["samples"])
        result["status"]="BENCHMARK_COMPLETE"
        print("IWXA_GUI_BASELINE_MEASUREMENT_COMPLETE",flush=True)
        print(json.dumps(result["paired_medians"],sort_keys=True),flush=True)
    except Exception as error:
        result["error"]=str(error)
        raise
    finally:
        REPORT.write_text(json.dumps(result,indent=2,ensure_ascii=False),
                          encoding="utf8")


if __name__ == "__main__":
    main()
