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
from urllib.parse import urlsplit

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


def wait_save_first_dialog(before, label, seconds=7):
    """Verify the visible OS-close overlay rather than mere process liveness."""
    points = ((38, 180), (38, 390), (38, 690), (1190, 210), (1190, 690))
    deadline = time.monotonic() + seconds
    stable = 0
    while time.monotonic() < deadline:
        current = screenshot("poll-" + label)
        dimmed = sum(
            sum(before.getpixel(point)) - sum(current.getpixel(point)) >= 45
            for point in points
        )
        stable = stable + 1 if dimmed >= 3 else 0
        if stable >= 2:
            current.save(OUT / (label + ".png"))
            return
        time.sleep(.25)
    screenshot(label + "-missing")
    raise AssertionError("System close did not paint the save-first dialog")


def wait_save_dialog_dismissed(before, seconds=5):
    points = ((38, 180), (38, 390), (38, 690), (1190, 210), (1190, 690))
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        current = screenshot("poll-return-to-work")
        restored = sum(
            sum(abs(before.getpixel(point)[i] - current.getpixel(point)[i])
                for i in range(3)) <= 35
            for point in points
        )
        if restored >= 3:
            current.save(OUT / "save-first-cancelled.png")
            return
        time.sleep(.25)
    raise AssertionError("Cancel did not remove the save-first overlay")


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
    started=time.monotonic()
    deadline=started+seconds
    attempts=0
    reloaded=False
    while time.monotonic()<deadline:
        view=state.get("result") or {}
        if view.get("overviewRendered") and (view.get("buttons") or {}).get("new-workspace-toggle"):
            report["checks"]["artbazi_overview_rendered"]="PASS"
            report["checks"]["artbazi_overview_probe_count"]=attempts+1
            report["checks"]["artbazi_overview_reload_required"]=reloaded
            return state
        attempts+=1
        # If the actual root is still empty, exercise the browser's existing
        # user-visible Reload ONCE. No engine restart, no increased timeout,
        # no script injection or changes to T2 data. Never retry a mounted
        # screen that might contain an unsaved edit.
        if not reloaded and time.monotonic()-started>=22 and (
            view.get("scope")=="public-workspace" and
            view.get("rootChildren")==0 and
            not view.get("createDialog") and
            not view.get("workspaceViewMounted")
        ):
            screenshot("artbazi-empty-overview-before-user-reload")
            reliable_click(105,86)
            reloaded=True
            report["checks"]["artbazi_blank_root_user_reload"]="USER_ACTION_PERFORMED"
        time.sleep(5)
        state=request_probe("artbazi")
    report["checks"]["artbazi_overview_rendered"]="HOLD"
    report["checks"]["artbazi_overview_probe_count"]=attempts
    report["checks"]["artbazi_overview_reload_required"]=reloaded
    screenshot("artbazi-overview-unavailable")
    raise AssertionError("ArtBazi T2 overview not hydrated after bounded normal or single-reload route")


def inspect_closed_engine_workspace(profile, created_url):
    """Read ONLY key presence from the existing Obscura profile snapshot.

    Called after terminating the entire test-owned browser process tree. This
    never opens a database, reads Result bodies, or uses a second T2 Kernel.
    IndexedDB v0.2.6 encodes a string primary key as JSON.stringify([2, key]).
    """
    output = {"snapshot": "ABSENT", "database": "ABSENT",
              "store": "ABSENT", "record": "UNKNOWN", "records": 0}
    try:
        page = urlsplit(created_url)
        if (page.scheme, page.netloc) != ("https", "artbazi.ru"):
            return {**output, "error": "unexpected_origin"}
        parts = page.fragment.split("/")
        if len(parts) != 3 or parts[0:2] != ["", "workspace"] or not parts[2]:
            return {**output, "error": "unexpected_route"}
        workspace_id = parts[2]
        snapshot = profile / "indexedDB" / "snapshot-v1.json"
        if not snapshot.is_file():
            return output
        if snapshot.stat().st_size > 256 * 1024 * 1024:
            return {**output, "error": "oversized"}
        output["snapshot"] = "PRESENT"
        durable = json.loads(snapshot.read_text(encoding="utf-8"))
        if durable.get("format") != 1:
            return {**output, "error": "unexpected_format"}
        databases = [
            entry for entry in durable.get("databases", [])
            if entry.get("origin") == "https://artbazi.ru"
            and entry.get("name") == "iwxa-local"
        ]
        if len(databases) != 1:
            return output
        db = databases[0]
        output["database"] = "PRESENT"
        output["db_version"] = db.get("version")
        records = db.get("records", {})
        if "workspaces" not in records:
            return output
        store = records["workspaces"]
        output["store"] = "PRESENT"
        output["records"] = len(store)
        # Inspect only stored primary keys; values are V8-serialized and
        # must never be extracted into artifacts or user-facing diagnostics.
        output["record"] = "PRESENT" if any(
            json.loads(key) == [2, workspace_id]
            for key in store
        ) else "ABSENT"
        return output
    except (OSError, ValueError, TypeError, AttributeError) as error:
        return {**output, "error": type(error).__name__}


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
        len(state.get("tabs",[])) == 1 and state.get("active") == 0 and
        state["tabs"][0].startswith("https://artbazi.ru/workspace/#/workspace/"),
        "ArtBazi T2 created workspace route",seconds=80)
    created_url=created["tabs"][0]
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




def scroll_native_page(direction):
    """Wheel toward the control, not endlessly down past it."""
    if direction not in ("up", "down"):
        raise ValueError("Unexpected scroll direction")
    if gui.WINDOWS:
        gui.pyautogui.moveTo(990, 560)
        gui.pyautogui.scroll(3 if direction == "up" else -3)
    else:
        gui.command("xdotool", "mousemove", "990", "560")
        for _ in range(3):
            gui.command("xdotool", "click", "4" if direction == "up" else "5")


def wait_probe_scope(scope, seconds=45):
    deadline=time.monotonic()+seconds
    last={}
    while time.monotonic()<deadline:
        last=request_probe("artbazi")["result"]
        if last.get("scope")==scope:
            return last
        time.sleep(1.5)
    raise AssertionError(f"ArtBazi expected native UI {scope}, observed={last.get('scope')}")


def click_probe_control(scope, field, seconds=55):
    deadline=time.monotonic()+seconds
    last={}
    while time.monotonic()<deadline:
        state=request_probe("artbazi")["result"]
        if state.get("scope")!=scope:
            time.sleep(.5)
            continue
        controls=state.get("controls" if scope=="artbazi-launch-navigator" else "resultControls") or {}
        last=controls.get(field) or {}
        if last.get("visible") and last.get("enabled"):
            native_workspace_click(last)
            return
        # Scroll only when the actual target is outside the native viewport.
        # Disabled forms must not be "fixed" by repeatedly scrolling.
        if isinstance(last.get("y"), int) and last["y"] < 55:
            scroll_native_page("up")
        elif isinstance(last.get("y"), int) and last["y"] > 650:
            scroll_native_page("down")
        else:
            time.sleep(.8)
        time.sleep(.3)
    raise AssertionError(f"Real ArtBazi UI control {field} not ready, last={last}")


def verify_launch_answer(field, report, seconds=8):
    """A native click counts only after the real radio reports checked."""
    marker={"situation":"situationSelected","resources":"resourcesSelected"}[field]
    deadline=time.monotonic()+seconds
    last={}
    while time.monotonic()<deadline:
        last=request_probe("artbazi")["result"]
        if last.get("scope")=="artbazi-launch-navigator" and last.get(marker):
            report["checks"]["artbazi_launch_"+field+"_selected"]="PASS"
            return
        time.sleep(.5)
    screenshot("artbazi-radio-"+field+"-not-selected")
    raise AssertionError(f"Actual ArtBazi radio {field} was not selected by native click")


def wait_artbazi_t2_result_state(predicate, label, seconds=65):
    deadline=time.monotonic()+seconds
    last={}
    while time.monotonic()<deadline:
        state=request_probe("artbazi")["result"]
        if state.get("scope")=="public-workspace":
            last=state.get("t2Result") or {}
            if predicate(last):
                return last
        time.sleep(1.5)
    screenshot("artbazi-"+label+"-hold")
    raise AssertionError(f"ArtBazi {label} unavailable via visible UI, last={last}")


def attempt_artbazi_result(report, session):
    """All writes are native clicks in real ArtBazi -> domain T2 Kernel UI."""
    destination="https://artbazi.ru/services/launch-navigator/"
    type_address(destination)
    wait_session(session,lambda state:
        state.get("active")==0 and state.get("tabs",[""])[0].startswith(destination),
        "ArtBazi launch-navigator public service route",seconds=55)
    wait_remote_document("artbazi-launch-service")
    wait_probe_scope("artbazi-launch-navigator")
    click_probe_control("artbazi-launch-navigator","situation")
    verify_launch_answer("situation",report)
    click_probe_control("artbazi-launch-navigator","resources")
    verify_launch_answer("resources",report)
    click_probe_control("artbazi-launch-navigator","calculate")
    click_probe_control("artbazi-launch-navigator","save")
    click_probe_control("artbazi-launch-navigator","transfer")
    # The pinned upstream window.confirm() currently auto-accepts. This is
    # recorded as an independent browser UX HOLD, not a user confirmation.
    report["checks"]["obscura_javascript_confirm_dialog"]="HOLD_UPSTREAM_AUTO_ACCEPT"
    wait_session(session,lambda state:
        state.get("active")==0 and
        state.get("tabs",[""])[0].startswith("https://artbazi.ru/workspace/#/domain"),
        "ArtBazi staged transfer reached real T2 domain",seconds=55)
    pending=wait_artbazi_t2_result_state(
        lambda value:value.get("pending") and value.get("pendingSourceMatched"),
        "T2 pending result")
    report["checks"]["artbazi_source_result_transferred_via_ui"]="PASS"
    if not pending.get("focusSelected"):
        if pending.get("focusChoices")!=1:
            raise AssertionError("T2 Host has no unambiguous user-selectable Workspace")
        click_probe_control("public-workspace","focusWorkspace")
        gui.key("End")
        gui.key("Return")
        wait_artbazi_t2_result_state(lambda value:value.get("focusSelected"),
            "selected T2 Workspace",seconds=35)
    click_probe_control("public-workspace","confirm")
    wait_artbazi_t2_result_state(
        lambda value:value.get("verified") and value.get("freshReceiptAndReadback"),
        "Kernel receipt and matching readback")
    report["checks"]["artbazi_t2_kernel_receipt_verified_by_site"]="PASS"
    # Read the same committed Result through the domain's existing list/view.
    click_probe_control("public-workspace","selectSaved")
    wait_artbazi_t2_result_state(
        lambda value:value.get("revisionOne") and value.get("readbackMatches"),
        "T2 Result content readback")
    report["checks"]["artbazi_t2_result_readback"]="PASS"
    click_probe_control("public-workspace","history")
    wait_artbazi_t2_result_state(
        lambda value:value.get("savedEventVisible"),
        "T2 saved event in actual user history")
    report["checks"]["artbazi_t2_saved_event_visible"]="PASS"
    screenshot("artbazi-saved-result-kernel-readback")


def verify_native_save_first_exit(process, report, session):
    """System close, visual reminder, cancel preserving domain, accept and exit."""
    before=screenshot("artbazi-before-system-close")
    gui.key("alt+F4")
    if process.poll() is not None:
        raise AssertionError("OS close bypassed save-first reminder")
    wait_save_first_dialog(before,"save-first-os-close")
    report["checks"]["native_save_first_dialog_visible"]="PASS"
    reliable_click(253,42)  # header must not allow new tabs behind the modal
    time.sleep(.7)
    current_tabs=json.loads(session.read_text(encoding="utf-8"))["tabs"]
    if len(current_tabs)!=1:
        raise AssertionError("Toolbar created a tab behind save-first dialog")
    report["checks"]["native_save_first_modal_blocks_tabs"]="PASS"
    # A canceled close must preserve the domain page, not just process PID.
    gui.key("Escape")
    wait_save_dialog_dismissed(before)
    if process.poll() is not None:
        raise AssertionError("Cancel terminated Tauri")
    result=request_probe("artbazi")["result"]
    if not (result.get("t2Result") or {}).get("readbackMatches"):
        raise AssertionError("Cancel discarded the visible, verified T2 Result")
    report["checks"]["native_save_first_return_to_site"]="PASS"
    gui.key("alt+F4")
    wait_save_first_dialog(before,"save-first-confirmation")
    gui.key("Tab")   # button after auto-focused Return and save
    gui.key("Return")
    try:
        exit_code=process.wait(timeout=12)
    except subprocess.TimeoutExpired as error:
        raise AssertionError("Confirmed OS-close did not terminate Tauri") from error
    if exit_code!=0:
        raise AssertionError(f"Confirmed browser exit returned {exit_code}")
    report["checks"]["native_save_first_confirmed_exit"]="PASS"

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
            "navigation_timeout_ms": 200000 if THREE_MINUTE_SPA_EXPERIMENT else None,
            "cdp_command_timeout_ms": 200000 if THREE_MINUTE_SPA_EXPERIMENT else None,
        },
        "checks": {},
        "not_qualified": [
            "T2 receipts", "T2 Result readback",
            "Exchange", "arbitrary-Internet process isolation",
            "JavaScript confirm dialog (Obscura upstream auto-accept)"
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
            for stage in ("visit",):
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

                # Single real ArtBazi session: no restart or tab restoration.
                wait_picker("artbazi-picker-ready")
                type_address(SITES[1][1])
                wait_consent_dialog()
                gui.key("Tab")
                gui.key("Return")
                saved=wait_session(session,lambda state:
                    set(state.get("granted_sites", [])) == {"artbazi"} and
                    len(state.get("tabs", [])) == 1 and
                    state.get("active") == 0 and
                    state["tabs"][0].startswith(SITES[1][1]),
                    "ArtBazi consent and native navigation",seconds=65)
                report["checks"]["artbazi_address_approved"]="PASS"
                wait_remote_document("artbazi-public-page")
                report["checks"]["artbazi_native_document_ready"]="PASS"
                type_address(SITES[1][2])
                wait_session(session,lambda state:
                    state.get("active")==0 and len(state.get("tabs",[]))==1 and
                    state["tabs"][0].startswith(SITES[1][2]),
                    "ArtBazi Workspace address")
                report["checks"]["artbazi_workspace_address"]="PASS"
                report["checks"]["artbazi_workspace_html"]=observe_workspace_document(
                    "artbazi-workspace-capture")
                artbazi_probe=request_probe("artbazi")
                report["checks"]["artbazi_bootstrap_probe"]=artbazi_probe
                report["checks"]["artbazi_t2_host_mount"]=(
                    "PASS" if artbazi_probe["result"].get("hostMounted") else "HOLD")
                created_url=attempt_artbazi_create(artbazi_probe,report,session)
                attempt_artbazi_result(report,session)
                verify_native_save_first_exit(process,report,session)
                report["not_qualified"]=[
                    item for item in report["not_qualified"]
                    if item not in ("T2 receipts","T2 Result readback")
                ]
                gui.stop_tree(process)
                process = None
                # Observe the *actual T2 IndexedDB key* while the only
                # owning engine is closed. URL persistence alone is not data
                # persistence, and missing React is not proof of data loss.
                if created_url and stage == "visit":
                    report["checks"]["artbazi_workspace_idb_after_result"] = (
                        inspect_closed_engine_workspace(profile, created_url)
                    )
            report["status"] = "PASS_ARTBAZI_T2_RESULT_AND_GUI_EXIT"
        except Exception as error:
            report["status"] = "HOLD_PUBLIC_SITE_GUI_NAVIGATION_AND_SESSION"
            report["reason"] = repr(error)
            with contextlib.suppress(Exception):
                screenshot("hold")
        finally:
            if process is not None:
                with contextlib.suppress(Exception):
                    gui.stop_tree(process)
            # Even a readback failure must leave a data-versus-rendering
            # verdict. No modification is made to this test-only profile.
            if created_url:
                report["checks"]["artbazi_t2_idb_final"] = (
                    inspect_closed_engine_workspace(profile, created_url)
                )
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
            "PASS" if "IWXA_SPA_DIAGNOSTIC_ACTIVE script_ms=180000 module_ms=180000 nav_ms=200000 cdp_ms=200000" in engine_log
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
    return 0 if report["status"] == "PASS_ARTBAZI_T2_RESULT_AND_GUI_EXIT" else 1


if __name__ == "__main__":
    sys.exit(main())
