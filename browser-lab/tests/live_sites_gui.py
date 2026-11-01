"""Real native bundled Tauri opt-in and restored public site routes.

No mock CDP and no proprietary IWXA code. This deliberately separates
navigation/session evidence from still-unqualified T2 Result readback.
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

from PIL import Image, ImageChops, ImageStat
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

def wait_file(path, predicate, name, seconds=45):
    deadline = time.monotonic() + seconds
    last = None
    while time.monotonic() < deadline:
        try:
            last = json.loads(path.read_text(encoding="utf-8"))
            if predicate(last):
                return last
        except (FileNotFoundError, OSError, ValueError):
            pass
        time.sleep(.4)
    raise AssertionError(name + " not saved in native browser session: " + repr(last))

def type_address(url):
    if gui.WINDOWS:
        gui.click(430, 96)
        time.sleep(.35)
        gui.key("ctrl+a")
    else:
        gui.key("ctrl+l")
        time.sleep(.2)
    gui.text(url)
    gui.key("Return")

def approve():
    # The dialog intentionally autofocuses Cancel; Tab + Enter exercises
    # deliberate user selection of Allow, not a hidden test bypass.
    time.sleep(.6)
    gui.key("Tab")
    gui.key("Return")

def main():
    app = Path(os.environ["OBSCURA_LAB_GUI_BINARY"]).resolve(strict=True)
    report = {"kind":"LIVE_SITE_GUI", "head":os.getenv("GITHUB_SHA"),
        "platform":platform.platform(), "checks":{},
        "not_qualified":["T2 receipts","Result readback","Exchange","external-site OS isolation"]}
    manager = None
    process = None
    with tempfile.TemporaryDirectory(prefix="iwxa-public-sites-") as temp, (OUT/"native.log").open("wb") as log:
        profile = Path(temp) / "profile"
        profile.mkdir()
        session = profile / "browser-tabs-v1.json"
        env = dict(os.environ)
        for k in list(env):
            if k in ("OBSCURA_LAB_FIXTURE", "OBSCURA_LAB_ALLOWED_ORIGINS",
                     "OBSCURA_LAB_BINARY", "OBSCURA_LAB_SHA256"):
                env.pop(k)
        env["OBSCURA_LAB_PROFILE_DIR"] = str(profile)
        if not gui.WINDOWS:
            env["WEBKIT_DISABLE_COMPOSITING_MODE"] = "1"
            manager = subprocess.Popen(["openbox"], stdout=log, stderr=subprocess.STDOUT)
        try:
            for stage in ("visit", "restore"):
                process = subprocess.Popen([str(app)], env=env, cwd=app.parent,
                    stdout=log, stderr=subprocess.STDOUT)
                gui.get_window(process)
                gui.wait_chrome(process)
                time.sleep(.7)
                screenshot(stage+"-initial")
                if stage == "visit":
                    for index,(name,origin,workspace) in enumerate(SITES):
                        if index:
                            gui.key("ctrl+t")
                            wait_file(session,lambda obj:len(obj.get("tabs",[])) == index+1,
                                "New tab for " + name)
                        type_address(origin)
                        approve()
                        saved = wait_file(session, lambda obj:
                            name in obj.get("granted_sites",[]) and
                            any(u.startswith(origin) for u in obj.get("tabs",[])),
                            "Native opt-in " + name, seconds=60)
                        report["checks"][name+"_approved"] = "PASS"
                        report["checks"][name+"_saved_url"] = saved["tabs"][saved["active"]]
                        time.sleep(7)
                        screenshot(name+"-website")
                    report["checks"]["site_approval_storage"] = "PASS"
                    saved = wait_file(session,lambda x:
                        set(x.get("granted_sites",[])) == {"learn","artbazi"} and
                        len(x.get("tabs",[])) == 2 and x.get("active")==1,
                        "Two approved websites")
                    report["checks"]["session_before_relaunch"] = saved["tabs"]
                else:
                    saved = wait_file(session,lambda x:
                        set(x.get("granted_sites",[])) == {"learn","artbazi"} and
                        len(x.get("tabs",[])) == 2 and x.get("active")==1 and
                        x["tabs"][0].startswith(SITES[0][1]) and
                        x["tabs"][1].startswith(SITES[1][1]),
                        "Real user browser session after relaunch",seconds=65)
                    report["checks"]["two_real_sites_restored"] = "PASS"
                    screenshot("after-relaunch")
                    # Use the address bar to visit the actual public Workspace
                    # route on the active ArtBazi site, no private test hooks.
                    type_address(SITES[1][2])
                    saved = wait_file(session,lambda x:
                        SITES[1][2] in x.get("tabs",[]),"ArtBazi Workspace navigation",seconds=50)
                    report["checks"]["artbazi_workspace_url_saved"] = "PASS"
                    time.sleep(6)
                    screenshot("artbazi-workspace")
                    # Learn remains a separate tab with its own origin/grant.
                    gui.click(105, 48)
                    time.sleep(1.1)
                    type_address(SITES[0][2])
                    wait_file(session,lambda x:
                        SITES[0][2] in x.get("tabs",[]),"Learn Workspace navigation",seconds=50)
                    report["checks"]["learn_workspace_url_saved"] = "PASS"
                    time.sleep(6)
                    screenshot("learn-workspace")
                gui.stop_tree(process)
                process = None
            report["status"] = "PASS_LIVE_SITE_SESSION_ROUTE"
        except Exception as err:
            report["status"] = "HOLD_LIVE_SITE_SESSION_ROUTE"
            report["reason"] = repr(err)
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
    (OUT/"report.json").write_text(json.dumps(report, indent=2, ensure_ascii=False),encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    return 0 if report["status"] == "PASS_LIVE_SITE_SESSION_ROUTE" else 1

if __name__ == "__main__":
    sys.exit(main())
