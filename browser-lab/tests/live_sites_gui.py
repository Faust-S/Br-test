"""Real bundled-Tauri opt-in routes for public Learn and ArtBazi.

No mock CDP, T2 fixtures or secrets. The test must observe a fully booted
native GUI before interacting. URL/session readback does not prove that a
remote site rendered or created a T2 Result: screenshots remain review evidence.
"""
import contextlib
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


def screenshot(name):
    image = gui.screenshot("site-" + name)
    image.save(OUT / (name + ".png"))
    return image


def wait_session(path, condition, what, seconds=55):
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


def wait_remote_document(name, seconds=70):
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


def type_address(url):
    # Actual pointer path works on both WebKitGTK and Windows WebView2.
    gui.click(430, 86)
    time.sleep(.3)
    gui.key("ctrl+a")
    gui.text(url)
    gui.key("Return")


def open_tab(session, number):
    # A real user click on the small '+' control, not a keyboard accelerator
    # potentially consumed by GTK/WebView2 while a remote page is loading.
    # In the 1250px native window the new-tab button is directly after
    # a single 220px tab; x=285 is outside its 34px hit target.
    gui.click(253, 42)
    wait_session(session, lambda state:
        len(state.get("tabs", [])) == number and
        state["tabs"][state["active"]] == "about:blank",
        f"new tab {number}")
    wait_picker("new-tab-picker")


def main():
    app = Path(os.environ["OBSCURA_LAB_GUI_BINARY"]).resolve(strict=True)
    report = {
        "kind": "LIVE_SITE_NATIVE_GUI",
        "head": os.getenv("GITHUB_SHA"),
        "platform": platform.platform(),
        "checks": {},
        "not_qualified": [
            "remote document hydration", "T2 receipts", "T2 Result readback",
            "Exchange", "arbitrary-Internet process isolation"
        ],
    }
    manager = None
    process = None
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
        if not gui.WINDOWS:
            env["WEBKIT_DISABLE_COMPOSITING_MODE"] = "1"
            manager = subprocess.Popen(["openbox"], stdout=log, stderr=subprocess.STDOUT)
        try:
            for stage in ("visit", "restore"):
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
                    gui.click(475, 597)  # Learn card, actual new-tab UI
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
                else:
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
                    wait_remote_document("artbazi-workspace-capture")
                    report["checks"]["artbazi_workspace_document_ready"] = "PASS"

                    gui.click(145, 43)  # original Learn tab
                    time.sleep(.6)
                    type_address(SITES[0][2])
                    wait_session(session, lambda state:
                        any(url.startswith(SITES[0][2]) for url in state.get("tabs", [])),
                        "Learn Workspace address")
                    report["checks"]["learn_workspace_address"] = "PASS"
                    wait_remote_document("learn-workspace-capture")
                    report["checks"]["learn_workspace_document_ready"] = "PASS"
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
    (OUT / "report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["status"] == "PASS_PUBLIC_SITE_GUI_NAVIGATION_AND_SESSION" else 1


if __name__ == "__main__":
    sys.exit(main())
