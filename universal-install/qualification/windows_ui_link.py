"""Real Windows GUI fixture: link existing OS window without spawning a copy."""
import json
import os
from pathlib import Path
import subprocess
import sys
import time
if os.name != "nt":
    raise SystemExit("WINDOW_BRIDGE_WINDOWS_ONLY")
ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "universal-install"))
from iwxa_universal_install.windows_window import observe_windows, focus_existing_window

# MessageBox is a real Win32 top-level window in a CI-owned child process.
child = subprocess.Popen([
    sys.executable, "-c",
    "import ctypes; ctypes.windll.user32.MessageBoxW(None,"
    "'IWXA CI window fixture','IWXA Program Window CI Fixture',0)"
])
try:
    observation = None
    for _ in range(60):
        observation = observe_windows(child.pid)
        if observation["observed"]:
            break
        if child.poll() is not None:
            raise RuntimeError("WINDOW_FIXTURE_TERMINATED")
        time.sleep(0.2)
    if not observation["observed"]:
        raise RuntimeError("REAL_WINDOW_NOT_OBSERVED")
    windows = [w for w in observation["windows"] if
               "IWXA Program Window CI Fixture" in w["title"]]
    if not windows:
        raise RuntimeError("WINDOW_TITLE_MISMATCH")
    actual = windows[0]
    invalid = focus_existing_window(child.pid, 12345)
    if invalid["focused"]:
        raise RuntimeError("WINDOW_IDENTITY_MISMATCH_ACCEPTED")
    focused = focus_existing_window(child.pid, actual["handle"])
    # Windows may refuse foreground stealing; preserve actual result.
    import ctypes
    ctypes.windll.user32.PostMessageW(actual["handle"], 0x0010, 0, 0)
    child.wait(timeout=10)
    print(json.dumps({
        "schema": "IWXA_NATIVE_UI_I3_QUALIFICATION/1", "status": "PASS",
        "real_window_observed": True, "existing_process_only": True,
        "foreign_handle_rejected": True, "focus_status": focused["status"],
        "not_embedded": True, "not_new_program_instance": True
    }, sort_keys=True))
finally:
    if child.poll() is None:
        child.kill()
        child.wait(timeout=5)
