"""Real bundled-Tauri opt-in routes for public Learn and ArtBazi.

No mock CDP, T2 fixtures or secrets. The test must observe a fully booted
native GUI before interacting. URL/session readback does not prove that a
remote site rendered or created a T2 Result: screenshots remain review evidence.
"""
import contextlib
import hashlib
import json
import os
import platform
import subprocess
import sys
import tempfile
import time
from pathlib import Path

import packaged_idb_gui as gui

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts" / "browser-lab" / "live-sites"
OUT.mkdir(parents=True, exist_ok=True)
SITES = (
    ("learn", "https://learn.iwxa.ru/", "https://learn.iwxa.ru/workspace/"),
    ("artbazi", "https://artbazi.ru/", "https://artbazi.ru/workspace"),
)
# The flag is enabled by the existing Linux live-site CI step ONLY. It does
# not change the normal browser, IndexedDB tests or release builds.
THREE_MINUTE_SPA_EXPERIMENT = os.environ.get("IWXA_SPA_DIAGNOSTIC_180S") == "1"



def screenshot(name):
    image = gui.screenshot("site-" + name)
    image.save(OUT / (name + ".png"))
    return image


def wait_session(path, condition, what, seconds=55):
    if THREE_MINUTE_SPA_EXPERIMENT:
        seconds=max(seconds, 215)
    until = time.monotonic() + seconds
    last = None
    while time.monotonic() < until:
        try:
            last = json.loads(path.read_text(encoding="utf-8"))
            if condition(last):
                return last
        except (OSError, ValueError):
            pass
        time.sleep(.4)
    raise AssertionError(f"{what}: no native session evidence, last={last!r}")


def wait_picker(label, seconds=35):
    # GUI has already produced its first blank chrome. The native engine boot
    # can still be in progress: site cards are absent until Boot finishes.
    # Both white cards on the new-tab surface provide independent evidence
    # that React received the ready view, not just that an HWND exists.
    deadline = time.monotonic() + seconds
    stable = 0
    while time.monotonic() < deadline:
        image = screenshot("poll-picker")
        a = image.getpixel((345, 585))
        b = image.getpixel((655, 585))
        ready = all(c >= 248 for c in a[:3]) and all(c >= 248 for c in b[:3])
        stable = stable + 1 if ready else 0
        if stable >= 3:
            image.save(OUT / (label + ".png"))
            return
        time.sleep(.45)
    screenshot(label + "-missing-picker")
    raise AssertionError(label + ": site picker not visible after native boot")


def wait_consent_dialog(seconds=18):
    # The modal paints a dark backdrop across main. The undecorated start
    # page is near-white at this location; no OCR or DOM evaluation is used.
    deadline = time.monotonic() + seconds
    stable = 0
    while time.monotonic() < deadline:
        image = screenshot("poll-consent")
        pixel = image.getpixel((50, 200))
        stable = stable + 1 if max(pixel[:3]) < 220 else 0
        if stable >= 2:
            image.save(OUT / "artbazi-consent.png")
            return
        time.sleep(.35)
    screenshot("consent-missing")
    raise AssertionError("ArtBazi approval modal did not appear after address input")


def observe_workspace_document(name, seconds=8):
    # A Workspace can load HTML successfully without actually mounting
    # canonical T2. Capture its visible state, but do not mark it ready.
    time.sleep(seconds)
    screenshot(name)
    return "HTML_OBSERVED_T2_NOT_YET_QUALIFIED"


def wait_remote_document(name, seconds=70):
    if THREE_MINUTE_SPA_EXPERIMENT:
        seconds=max(seconds, 215)
    # The browser's status marker becomes green only after the native
    # Obscura document reaches readyState=complete. This proves a real
    # remote-document load, not hydration or a T2 Result transaction.
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        image = screenshot("poll-" + name)
        pixel = image.getpixel((16, 857))
        if pixel[1] > pixel[0] + 60 and pixel[1] > pixel[2] + 35:
            image.save(OUT / (name + ".png"))
            return
        time.sleep(.6)
    screenshot(name + "-not-ready")
    raise AssertionError(name + ": Obscura remote document never reached ready state")


def reliable_click(x, y):
    # Xvfb/Openbox sometimes stalls on xdotool mousemove --sync when the
    # restored Obscura page is busy, although the window remains responsive.
    # Send an ordinary pointer movement and click, without waiting for an
    # exact X11 pointer position acknowledgement. This still uses native UI.
    if gui.WINDOWS:
        gui.click(x, y)
    else:
        gui.command("xdotool", "mousemove", str(x), str(y))
        gui.command("xdotool", "click", "1")


def type_address(url):
    # Actual pointer path works on both WebKitGTK and Windows WebView2.
    reliable_click(430, 86)
    time.sleep(.3)
    gui.key("ctrl+a")
    gui.text(url)
    gui.key("Return")


def open_tab(session, number):
    # A real user click on the small '+' control, not a keyboard accelerator
    # potentially consumed by GTK/WebView2 while a remote page is loading.
    # In the 1250px native window the new-tab button is directly after
    # a single 220px tab; x=285 is outside its 34px hit target.
    reliable_click(253, 42)
    wait_session(session, lambda state:
        len(state.get("tabs", [])) == number and
        state["tabs"][state["active"]] == "about:blank",
        f"new tab {number}")
    wait_picker("new-tab-picker")


def wait_workspace_probe(log_path, site, seconds=27, since=0):
    if THREE_MINUTE_SPA_EXPERIMENT:
        seconds=max(seconds, 215)
    marker = "IWXA_BROWSER_WORKSPACE_PROBE "
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        try:
            with log_path.open("rb") as handle:
                handle.seek(since)
                lines = handle.read().decode("utf-8", errors="replace").splitlines()
            for line in reversed(lines):
                if marker not in line: continue
                data = json.loads(line.split(marker,1)[1])
                if data.get("site")==site: return data
        except (OSError,ValueError):
            pass
        time.sleep(.4)
    raise AssertionError(f"Native debug-only Workspace probe {site} did not respond")


def request_probe(site, log_path=OUT/"native.log"):
    # Distinguish a new user-triggered observation from the previous one.
    since=log_path.stat().st_size if log_path.exists() else 0
    gui.key("ctrl+alt+shift+d")
    return wait_workspace_probe(log_path,site,since=since)


def native_workspace_click(point):
    # Debug DOM gives only public action geometry. The action itself remains
    # a real native pointer click through the Tauri/Obscura input path.
    if not isinstance(point,dict) or not all(isinstance(point.get(k),int) for k in ("x","y")):
        raise AssertionError("Visible T2 user action has no usable native geometry")
    x,y=point["x"],point["y"]
    if not (0<x<1200 and 0<y<730):
        raise AssertionError("T2 action would be outside expected viewport")
    # Linux native title/tab/address chrome consumes 111 vertical pixels;
    # read back actual click outcome rather than asserting coordinates alone.
    reliable_click(x,y+111)


def wait_artbazi_overview(initial, report, seconds=70):
    if THREE_MINUTE_SPA_EXPERIMENT:
        seconds=195
    # Remote React hydration was non-deterministic in the earlier run.
    # Never click a blank document, and never treat HTML ready as UI ready.
    state=initial
    deadline=time.monotonic()+seconds
    attempts=0
    while time.monotonic()<deadline:
        view=state.get("result") or {}
        if view.get("overviewRendered") and (view.get("buttons") or {}).get("new-workspace-toggle"):
            report["checks"]["artbazi_overview_rendered"]="PASS"
            report["checks"]["artbazi_overview_probe_count"]=attempts+1
            return state
        attempts+=1
        time.sleep(5)
        state=request_probe("artbazi")
    report["checks"]["artbazi_overview_rendered"]="HOLD"
    report["checks"]["artbazi_overview_probe_count"]=attempts
    screenshot("artbazi-overview-unavailable")
    raise AssertionError("ArtBazi T2 overview not hydrated within bounded user wait")


def attempt_artbazi_create(initial, report, session):
    view=wait_artbazi_overview(initial, report)["result"]
    toggle=(view.get("buttons") or {}).get("new-workspace-toggle")
    native_workspace_click(toggle)
    time.sleep(1.1)
    screenshot("artbazi-create-dialog")
    dialog=request_probe("artbazi")
    report["checks"]["artbazi_create_dialog_present"] = (
        "PASS" if dialog["result"].get("createDialog") else "HOLD")
    if not dialog["result"].get("createDialog"):
        raise AssertionError("T2 create dialog not mounted after real user click")
    confirm=(dialog["result"].get("buttons") or {}).get("create-workspace-confirm")
    native_workspace_click(confirm)
    time.sleep(3)
    screenshot("artbazi-after-create")
    report["checks"]["artbazi_create_submitted"] = "USER_ACTION_PERFORMED"
    # The normal T2 route is fragment-based. Only a successful Kernel
    # createWorkspace may drive its workspace-id route from the UI.
    created=wait_session(session,lambda state:
        len(state.get("tabs",[])) == 2 and state.get("active") == 1 and
        state["tabs"][1].startswith("https://artbazi.ru/workspace/#/workspace/"),
        "ArtBazi T2 created workspace route",seconds=80)
    created_url=created["tabs"][1]
    report["checks"]["created_workspace_route_sha256"]=hashlib.sha256(
        created_url.encode("utf-8")).hexdigest()
    report["checks"]["artbazi_created_workspace_route"]="PASS"
    last={}
    for _ in range(4):
        time.sleep(3)
        last=request_probe("artbazi")["result"]
        if last.get("workspaceViewMounted") and not last.get("workspaceViewError"):
            break
    report["checks"]["artbazi_created_workspace_visible"]=(
        "PASS" if last.get("workspaceViewMounted") and not last.get("workspaceViewError")
        else "HOLD")
    screenshot("artbazi-created-workspace")
    if report["checks"]["artbazi_created_workspace_visible"] != "PASS":
        raise AssertionError("New T2 workspace did not render its genuine record view")
    return created_url



def main():
    app = Path(os.environ["OBSCURA_LAB_GUI_BINARY"]).resolve(strict=True)
    report = {
        "kind": "LIVE_SITE_NATIVE_GUI",
        "head": os.getenv("GITHUB_SHA"),
        "platform": platform.platform(),
        "experiment": {
            "three_minute_spa_budget": THREE_MINUTE_SPA_EXPERIMENT,
            "script_deadline_ms": 180000 if THREE_MINUTE_SPA_EXPERIMENT else None,
            "module_budget_ms": 180000 if THREE_MINUTE_SPA_EXPERIMENT else None,
        },
        "checks": {},
        "not_qualified": [
            "remote document hydration", "T2 receipts", "T2 Result readback",
            "Exchange", "arbitrary-Internet process isolation"
        ],
    }
    manager = None
    process = None
    created_url = None
    with tempfile.TemporaryDirectory(prefix="iwxa-public-sites-") as temp, (OUT / "native.log").open("wb") as log:
        profile = Path(temp) / "profile"
        profile.mkdir()
        session = profile / "browser-tabs-v1.json"
        env = dict(os.environ)
        for key in ("OBSCURA_LAB_FIXTURE", "OBSCURA_LAB_ALLOWED_ORIGINS",
                    "OBSCURA_LAB_BINARY", "OBSCURA_LAB_SHA256"):
            env.pop(key, None)
        # DEBUG-only, local profile under test control; no fixture network
        # privileges and no origin grants are injected through the environment.
        env["OBSCURA_LAB_PROFILE_DIR"] = str(profile)
        # Only this fresh public-page CI profile: expose upstream module
        # preparation/evaluation warnings in the existing GUI artifact.
        env["RUST_LOG"] = "obscura_browser::page=warn,obscura_js=warn"
        if not gui.WINDOWS:
            env["WEBKIT_DISABLE_COMPOSITING_MODE"] = "1"
            manager = subprocess.Popen(["openbox"], stdout=log, stderr=subprocess.STDOUT)
        try:
            for stage in ("visit", "restore", "readback"):
                previous_write = session.stat().st_mtime_ns if session.exists() else 0
                process = subprocess.Popen([str(app)], env=env, cwd=app.parent,
                    stdout=log, stderr=subprocess.STDOUT)
                gui.get_window(process)
                gui.wait_chrome(process)
                wait_session(session, lambda state:
                    session.stat().st_mtime_ns > previous_write and
                    len(state.get("tabs", [])) >= 1,
                    f"{stage}: completed native engine boot", seconds=80)
                screenshot(stage + "-after-native-boot")

                if stage == "visit":
                    wait_picker("learn-picker-ready")
                    reliable_click(475, 597)  # Learn card, actual new-tab UI
                    saved = wait_session(session, lambda state:
                        "learn" in state.get("granted_sites", []) and
                        state["tabs"][state["active"]].startswith(SITES[0][1]),
                        "Learn card approval and native navigation", seconds=65)
                    report["checks"]["learn_card_approved"] = "PASS"
                    report["checks"]["learn_saved_url"] = saved["tabs"][saved["active"]]
                    wait_remote_document("learn-public-page")
                    report["checks"]["learn_native_document_ready"] = "PASS"

                    open_tab(session, 2)
                    type_address(SITES[1][1])  # ArtBazi address + user consent
                    wait_consent_dialog()
                    # Cancel is auto-focused; Tab then Enter chooses Allow.
                    gui.key("Tab")
                    gui.key("Return")
                    saved = wait_session(session, lambda state:
                        set(state.get("granted_sites", [])) == {"learn", "artbazi"} and
                        len(state.get("tabs", [])) == 2 and
                        state["tabs"][state["active"]].startswith(SITES[1][1]),
                        "ArtBazi consent and native navigation", seconds=65)
                    report["checks"]["artbazi_address_approved"] = "PASS"
                    report["checks"]["artbazi_saved_url"] = saved["tabs"][saved["active"]]
                    wait_remote_document("artbazi-public-page")
                    report["checks"]["artbazi_native_document_ready"] = "PASS"
                    report["checks"]["browser_session_before_restart"] = saved["tabs"]
                elif stage == "restore":
                    saved = wait_session(session, lambda state:
                        set(state.get("granted_sites", [])) == {"learn", "artbazi"} and
                        len(state.get("tabs", [])) == 2 and state.get("active") == 1 and
                        state["tabs"][0].startswith(SITES[0][1]) and
                        state["tabs"][1].startswith(SITES[1][1]),
                        "two sites restored from native profile")
                    report["checks"]["both_tabs_and_approvals_restored"] = "PASS"
                    screenshot("restored-artbazi")

                    type_address(SITES[1][2])
                    wait_session(session, lambda state:
                        any(url.startswith(SITES[1][2]) for url in state.get("tabs", [])),
                        "ArtBazi Workspace address")
                    report["checks"]["artbazi_workspace_address"] = "PASS"
                    report["checks"]["artbazi_workspace_html"] = observe_workspace_document(
                        "artbazi-workspace-capture")
                    artbazi_probe=request_probe("artbazi")
                    report["checks"]["artbazi_bootstrap_probe"]=artbazi_probe
                    report["checks"]["artbazi_t2_host_mount"]=(
                        "PASS" if artbazi_probe["result"].get("hostMounted") else "HOLD")
                    screenshot("artbazi-workspace-after-probe")
                    created_url=attempt_artbazi_create(artbazi_probe,report,session)
                    report["checks"]["artbazi_t2_result_readback"]="UNQUALIFIED"

                    reliable_click(145, 43)  # original Learn tab
                    time.sleep(.6)
                    type_address(SITES[0][2])
                    wait_session(session, lambda state:
                        any(url.startswith(SITES[0][2]) for url in state.get("tabs", [])),
                        "Learn Workspace address")
                    report["checks"]["learn_workspace_address"] = "PASS"
                    report["checks"]["learn_workspace_html"] = observe_workspace_document(
                        "learn-workspace-capture")
                    learn_probe = request_probe("learn")
                    report["checks"]["learn_bootstrap_probe"] = learn_probe
                    report["checks"]["learn_t2_host_mount"] = (
                        "PASS" if learn_probe["result"].get("hostMounted") else "HOLD")
                    screenshot("learn-workspace-after-probe")
                    report["checks"]["learn_t2_result_readback"] = "UNQUALIFIED"
                else:  # readback: do not re-create the workspace or change URLs
                    if not created_url:
                        raise AssertionError("No created T2 Workspace identity from the user action")
                    restored=wait_session(session,lambda state:
                        len(state.get("tabs",[])) == 2 and
                        set(state.get("granted_sites",[])) == {"learn","artbazi"} and
                        state["tabs"][1] == created_url,
                        "same T2 Workspace URL persisted through full Tauri restart")
                    report["checks"]["artbazi_created_workspace_route_restored"]="PASS"
                    # The previous stage left Learn active. Re-select the
                    # previously created ArtBazi workspace through the GUI.
                    reliable_click(300,43)
                    wait_session(session,lambda state:
                        state.get("active") == 1 and state["tabs"][1] == created_url,
                        "native ArtBazi tab selected after cold restart")
                    screenshot("artbazi-created-workspace-restored")
                    deadline=time.monotonic()+65
                    readback={}
                    while time.monotonic()<deadline:
                        readback=request_probe("artbazi")["result"]
                        if readback.get("workspaceViewError"):
                            break
                        if readback.get("workspaceViewMounted"):
                            break
                        time.sleep(4)
                    screenshot("artbazi-created-workspace-after-restart")
                    if readback.get("workspaceViewError") or not readback.get("workspaceViewMounted"):
                        raise AssertionError("Same T2 Workspace record not readable after cold restart")
                    report["checks"]["artbazi_workspace_same_record_readback"]="PASS"
                    # A Workspace record is not a domain Result or receipt.
                    report["checks"]["artbazi_t2_result_readback"]="UNQUALIFIED"
                gui.stop_tree(process)
                process = None
            report["status"] = "PASS_PUBLIC_SITE_GUI_NAVIGATION_AND_SESSION"
        except Exception as error:
            report["status"] = "HOLD_PUBLIC_SITE_GUI_NAVIGATION_AND_SESSION"
            report["reason"] = repr(error)
            with contextlib.suppress(Exception):
                screenshot("hold")
        finally:
            if process is not None:
                with contextlib.suppress(Exception):
                    gui.stop_tree(process)
            if manager is not None:
                manager.terminate()
                with contextlib.suppress(Exception):
                    manager.wait(timeout=5)
    # The previous GUI run silently discarded Obscura stdout/stderr. Preserve
    # only aggregate engine event counts in the report; the separate log file
    # remains review evidence from this fresh test-only public profile.
    try:
        engine_log = (OUT / "native.log").read_text(encoding="utf-8", errors="replace")
        report["checks"]["engine_logging_180s_active"] = (
            "PASS" if "IWXA_SPA_DIAGNOSTIC_ACTIVE script_ms=180000" in engine_log
            else "HOLD") if THREE_MINUTE_SPA_EXPERIMENT else "NOT_REQUESTED"
        event_signatures = {
            "module_prepare_errors": "ES module error (",
            "module_evaluation_errors": "ES module evaluation error",
            "module_loaded": "ES module loaded:",
            "parser_script_deadline": "deadline reached",
            "module_budget_exhausted": "ES module exhausted",
            "classic_script_errors": "Script error (",
            "inline_script_errors": "Inline script error:",
            "network_module_fetch_errors": "Failed to fetch module",
        }
        report["checks"]["engine_script_events"] = {
            key: engine_log.count(marker) for key, marker in event_signatures.items()
        }
        report["checks"]["engine_log_line_count"] = len(engine_log.splitlines())
    except OSError:
        report["checks"]["engine_logging_180s_active"] = "HOLD"
    (OUT / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["status"] == "PASS_PUBLIC_SITE_GUI_NAVIGATION_AND_SESSION" else 1


if __name__ == "__main__":
    sys.exit(main())
