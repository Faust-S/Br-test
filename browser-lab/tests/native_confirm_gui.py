"""Isolated real-OS regression for packaged Obscura's synchronous confirm().

GUI-mechanics test only: automated clicks are NOT human consent.
No T2, user documents, outside sites or JavaScript confirmation overrides.
"""
import concurrent.futures
import http.server
import json
import os
import subprocess
import tempfile
import threading
import time
from pathlib import Path

import websocket

ROOT = Path(__file__).resolve().parents[2]
OUT = ROOT / "artifacts" / "browser-lab" / "native-confirm"
OUT.mkdir(parents=True, exist_ok=True)
TITLE = "IWXA Browser - Site confirmation"
HTML = b'''<!doctype html><html><head><title>IWXA confirm fixture</title></head>
<body><button id="confirm" style="position:absolute;left:90px;top:80px;width:200px;height:65px"
onclick="window.__decision = window.confirm('Approve this test-only action?') ? 'yes' : 'no'">
Confirm</button><p>Test-only confirmation, never a T2 Result</p></body></html>'''


class Fixture(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path != "/":
            self.send_error(404)
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Content-Length", str(len(HTML)))
        self.end_headers()
        self.wfile.write(HTML)

    def log_message(self, *_args):
        pass


class CDP:
    def __init__(self, port):
        self.ws = websocket.create_connection(
            f"ws://127.0.0.1:{port}/", timeout=4, suppress_origin=True)
        self.next_id = 0

    def call(self, method, params=None, session=None):
        self.next_id += 1
        ident = self.next_id
        message = {"id": ident, "method": method, "params": params or {}}
        if session is not None:
            message["sessionId"] = session
        self.ws.send(json.dumps(message))
        deadline = time.monotonic() + 16
        while time.monotonic() < deadline:
            self.ws.settimeout(min(2.0, max(.2, deadline - time.monotonic())))
            try:
                received = json.loads(self.ws.recv())
            except websocket.WebSocketTimeoutException:
                continue
            if received.get("id") == ident:
                if received.get("error"):
                    raise AssertionError(f"CDP {method} failed: {received['error']}")
                return received.get("result", {})
        raise TimeoutError(f"CDP {method} had no response")

    def evaluate(self, expression, session):
        result = self.call("Runtime.evaluate", {
            "expression": expression, "returnByValue": True}, session)
        if result.get("exceptionDetails"):
            raise AssertionError(f"JS exception: {result['exceptionDetails']}")
        return result["result"].get("value")

    def close(self):
        self.ws.close()


def command(*args):
    return subprocess.run(args, text=True, capture_output=True,
                          check=True, timeout=5)


def start_engine(engine, temporary, native):
    profile = temporary / ("enabled" if native else "disabled")
    profile.mkdir()
    ready = temporary / ("enabled.ready" if native else "disabled.ready")
    env = dict(os.environ)
    env.pop("IWXA_OBSCURA_NATIVE_CONFIRM_UI", None)
    if native:
        env["IWXA_OBSCURA_NATIVE_CONFIRM_UI"] = "1"
    log = (OUT / ("enabled.log" if native else "disabled.log")).open("wb")
    process = subprocess.Popen([
        str(engine), "serve", "--host", "127.0.0.1", "--port", "0",
        "--max-connections", "1", "--workers", "1",
        "--storage-dir", str(profile), "--ready-file", str(ready),
        "--allow-private-network",
    ], stdout=log, stderr=subprocess.STDOUT, env=env)
    try:
        deadline = time.monotonic() + 25
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise AssertionError("Obscura exited before CDP readiness")
            if ready.exists():
                data = json.loads(ready.read_text(encoding="utf-8"))
                assert data["pid"] == process.pid
                assert data["host"] == "127.0.0.1"
                return process, log, CDP(data["port"])
            time.sleep(.15)
        raise TimeoutError("Obscura did not publish readiness")
    except BaseException:
        process.terminate()
        process.wait(timeout=7)
        log.close()
        raise


def navigate(client, origin):
    target = client.call("Target.createTarget", {"url": "about:blank"})["targetId"]
    session = client.call("Target.attachToTarget", {
        "targetId": target, "flatten": True})["sessionId"]
    client.call("Page.enable", session=session)
    client.call("Runtime.enable", session=session)
    client.call("Emulation.setDeviceMetricsOverride", {
        "width": 800, "height": 600, "deviceScaleFactor": 1, "mobile": False}, session)
    client.call("Page.navigate", {"url": origin}, session)
    deadline = time.monotonic() + 18
    while time.monotonic() < deadline:
        if client.evaluate("document.querySelector('#confirm') !== null", session):
            return session
        time.sleep(.2)
    raise AssertionError("Fixture button never appeared")


def press_site_button(client, session):
    point = client.evaluate(
        "(()=>{const r=document.querySelector('#confirm').getBoundingClientRect();"
        "return [r.left+r.width/2,r.top+r.height/2]})()", session)
    x, y = point
    for kind in ("mousePressed", "mouseReleased"):
        client.call("Input.dispatchMouseEvent", {
            "type": kind, "x": x, "y": y,
            "button": "left", "clickCount": 1}, session)


def visible_modal(seconds=10):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        result = subprocess.run([
            "xdotool", "search", "--onlyvisible", "--name", TITLE],
            capture_output=True, text=True)
        windows = [line.strip() for line in result.stdout.splitlines()
                   if line.strip().isdigit()]
        if result.returncode == 0 and windows:
            return windows[-1]
        time.sleep(.2)
    raise AssertionError("No native confirmation window appeared")


def screenshot(label):
    command("import", "-window", "root", str(OUT / (label + ".png")))


def decide(window_id, accept):
    command("xdotool", "windowactivate", "--sync", window_id)
    if not accept:
        command("xdotool", "key", "Escape")
        return
    geom = command("xdotool", "getwindowgeometry", "--shell", window_id)
    values = dict(line.split("=", 1) for line in geom.stdout.splitlines()
                  if "=" in line)
    width, height = int(values["WIDTH"]), int(values["HEIGHT"])
    if not (190 <= width <= 1800 and 110 <= height <= 1300):
        raise AssertionError("Unexpected native dialog size")
    # Measured GTK window 476x166: the Yes button spans the right half
    # of the 34px bottom action row. The old height-40 clicked ABOVE it.
    # Click its centre, not the arbitrary space above the button.
    command("xdotool", "mousemove", "--window", window_id,
            str(width * 3 // 4), str(height - 17))
    command("xdotool", "click", "1")
    # Verify the actual OS window closed before awaiting the blocked CDP
    # mouseReleased response. A missed click must be diagnosed immediately.
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        remaining = subprocess.run(["xdotool", "getwindowname", window_id],
                                   capture_output=True, text=True)
        if remaining.returncode != 0:
            return
        time.sleep(.1)
    raise AssertionError("Native Yes click did not dismiss the OS dialog")


def exercise(engine, origin, temporary, native, report):
    process = log = client = None
    try:
        process, log, client = start_engine(engine, temporary, native)
        session = navigate(client, origin)
        if not native:
            press_site_button(client, session)
            assert client.evaluate("window.__decision", session) == "no", (
                "Headless confirm was silently approved")
            report["checks"]["disabled"] = "DENIED_WITHOUT_NATIVE_UI"
            return
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
            for accepted, label in ((False, "no"), (True, "yes")):
                client.evaluate("window.__decision = null", session)
                future = pool.submit(press_site_button, client, session)
                window_id = visible_modal()
                screenshot("native-confirm-" + label)
                decide(window_id, accepted)
                future.result(timeout=14)
                answer = client.evaluate("window.__decision", session)
                assert answer == label, f"Expected actual OS {label}, got {answer!r}"
                report["checks"][label] = "REAL_OS_WINDOW_AND_CDP_READBACK"
    finally:
        if client:
            client.close()
        if process:
            process.terminate()
            try:
                process.wait(timeout=5)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=5)
        if log:
            log.close()


def main():
    engine = Path(os.environ["OBSCURA_PATCHED_BINARY"]).resolve(strict=True)
    report = {"scope": "native dialog mechanics; no human consent",
              "checks": {}, "not_qualified": ["Human consent", "T2 Result", "Tauri UI"]}
    manager = subprocess.Popen(["openbox"], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL)
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        time.sleep(.5)
        origin = f"http://127.0.0.1:{server.server_port}/"
        with tempfile.TemporaryDirectory(prefix="iwxa-os-confirm-") as temp:
            exercise(engine, origin, Path(temp), False, report)
            exercise(engine, origin, Path(temp), True, report)
        report["status"] = "PASS"
    except BaseException as error:
        report["status"] = "FAIL"
        report["reason"] = repr(error)
        raise
    finally:
        (OUT / "native-confirm-report.json").write_text(
            json.dumps(report, indent=2), encoding="utf-8")
        server.shutdown()
        manager.terminate()
        manager.wait(timeout=5)
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
