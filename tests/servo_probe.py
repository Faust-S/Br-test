import argparse
import hashlib
import http.server
import json
import os
from pathlib import Path
import shutil
import socket
import subprocess
import sys
import tarfile
import tempfile
import threading
import time
import urllib.error
import urllib.request
import zipfile

RELEASE = "v0.7.0"
ASSETS = {
    "Linux": ("servo-x86_64-linux-gnu.tar.gz", "728eba1be1cc1851e05dfaa90e18f98ab8644dd2355bedb0b8a5e89795ebe333"),
    "Windows": ("servo-x86_64-windows-msvc.zip", "49d4b2c2d3576f93fad70812ee9e42820f5b84da9103f2a633838109a2fcde53"),
}
ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "artifacts"
FIXTURE = ROOT / "probes"

def fetch_json(url, body=None, method=None, timeout=15):
    data = None if body is None else json.dumps(body).encode("utf-8")
    req = urllib.request.Request(url, data=data, method=method, headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as reply:
        data = reply.read()
        if not data:
            return {}
        return json.loads(data)

def request_json(url, body=None, method=None, timeout=15):
    try:
        value = fetch_json(url, body, method, timeout)
        if "value" in value:
            if isinstance(value["value"], dict) and value["value"].get("error"):
                raise RuntimeError(json.dumps(value["value"]))
            return value["value"]
        return value
    except urllib.error.HTTPError as error:
        raise RuntimeError(f"HTTP {error.code}: {error.read()[:1000]!r}") from error

class QuietServer(http.server.SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(FIXTURE), **kwargs)
    def log_message(self, *args):
        return

def port():
    sock = socket.socket()
    sock.bind(("127.0.0.1", 0))
    number = sock.getsockname()[1]
    sock.close()
    return number

def acquire():
    system = "Windows" if sys.platform == "win32" else "Linux"
    name, digest = ASSETS[system]
    archive = OUT / name
    url = f"https://github.com/servo/servo/releases/download/{RELEASE}/{name}"
    if not archive.exists():
        with urllib.request.urlopen(url, timeout=90) as response, archive.open("wb") as dest:
            shutil.copyfileobj(response, dest)
    h = hashlib.sha256()
    with archive.open("rb") as inp:
        for chunk in iter(lambda: inp.read(1024 * 1024), b""):
            h.update(chunk)
    if h.hexdigest() != digest:
        raise RuntimeError(f"release digest mismatch: {h.hexdigest()}")
    folder = OUT / "servo"
    folder.mkdir(parents=True, exist_ok=True)
    if system == "Windows":
        with zipfile.ZipFile(archive) as z:
            z.extractall(folder)
    else:
        with tarfile.open(archive, "r:gz") as tar:
            tar.extractall(folder, filter="data")
    candidates = [
        p for p in folder.rglob("*")
        if p.is_file() and p.name.lower() in (("servo.exe", "servoshell.exe") if system == "Windows" else ("servo", "servoshell"))
    ]
    if not candidates:
        raise RuntimeError("No Servo executable in release; files: " + str([str(p) for p in list(folder.rglob("*"))[:50]]))
    binary = sorted(candidates, key=lambda p: len(p.parts))[0]
    if system != "Windows":
        binary.chmod(binary.stat().st_mode | 0o111)
    return binary, {"tag": RELEASE, "asset": name, "sha256": digest, "binary": str(binary)}

def launch(binary, url, wdport, logpath):
    args = [str(binary), "--headless", "--webdriver", str(wdport), "--enable-experimental-web-platform-features", url]
    log = logpath.open("wb")
    proc = subprocess.Popen(args, cwd=binary.parent, stdout=log, stderr=subprocess.STDOUT)
    return proc, log

def wait_webdriver(base, proc, seconds=70):
    deadline = time.monotonic() + seconds
    last = None
    while time.monotonic() < deadline:
        if proc.poll() is not None:
            raise RuntimeError(f"Servo exited {proc.returncode} before WebDriver was ready")
        try:
            v = fetch_json(base + "/status", timeout=2)
            return v
        except Exception as exc:
            last = str(exc)
        time.sleep(0.5)
    raise RuntimeError(f"WebDriver did not start: {last}")

def wd(base, action, value=None, method="POST", timeout=12):
    return request_json(base + action, value, method, timeout)

def try_session(base):
    candidates = [{"capabilities":{"alwaysMatch":{"browserName":"servo"}}},{"capabilities":{"alwaysMatch":{}}}]
    errors = []
    for cap in candidates:
        try:
            payload = wd(base, "/session", cap, timeout=20)
            session_id = payload.get("sessionId") if isinstance(payload, dict) else None
            if not session_id and isinstance(payload, dict):
                session_id = payload.get("session_id")
            if session_id:
                return str(session_id)
            errors.append(repr(payload))
        except Exception as exc:
            errors.append(str(exc))
    raise RuntimeError("WebDriver session: " + " | ".join(errors))

def execute(base, sid, expr, timeout=15):
    return wd(base, f"/session/{sid}/execute/sync", {"script": "return (" + expr + ");", "args": []}, timeout=timeout)

def visit(base, sid, url):
    return wd(base, f"/session/{sid}/url", {"url": url}, timeout=25)

def capture(base, sid, label):
    try:
        data = wd(base, f"/session/{sid}/screenshot", method="GET", timeout=20)
        if isinstance(data, str):
            import base64
            (OUT / (label + ".png")).write_bytes(base64.b64decode(data))
    except Exception as exc:
        return str(exc)
    return "OK"

def await_probe(base, sid, name, duration=65):
    deadline = time.monotonic() + duration
    while time.monotonic() < deadline:
        obj = execute(base, sid, f"window.{name}", timeout=9)
        if isinstance(obj, dict) and obj.get("phase") in ("done", "error"):
            return obj
        time.sleep(0.7)
    return {"phase":"timeout"}

def shutdown(proc, log):
    try:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=6)
            except subprocess.TimeoutExpired:
                proc.kill()
                proc.wait(timeout=5)
    finally:
        log.close()

def main():
    OUT.mkdir(exist_ok=True)
    result = {"status": "NOT_RUN", "checks": {}, "release": RELEASE}
    failures = []
    server = None
    try:
        binary, release = acquire()
        result["binary"] = release
        server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), QuietServer)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        site = f"http://127.0.0.1:{server.server_port}/fixture.html"
        # Localhost is a trustworthy origin for WebCrypto.
        wdport = port()
        proc, log = launch(binary, site, wdport, OUT / "servo-first.log")
        try:
            base = f"http://127.0.0.1:{wdport}"
            result["webdriver_status"] = wait_webdriver(base, proc)
            sid = try_session(base)
            try:
                visit(base, sid, site)
                execute(base, sid, "window.startIWXAProbe()", timeout=10)
                data = await_probe(base, sid, "__iwxaProbe")
                result["checks"] = data
                result["first_screenshot"] = capture(base, sid, "first")
                if data.get("phase") != "done":
                    failures.append("probe did not finish")
            finally:
                try:
                    wd(base, f"/session/{sid}", method="DELETE", timeout=5)
                except Exception:
                    pass
        finally:
            shutdown(proc, log)
        # Use the same browser profile on both runs. No --temporary-storage.
        wdport2 = port()
        proc2, log2 = launch(binary, site, wdport2, OUT / "servo-second.log")
        try:
            base2 = f"http://127.0.0.1:{wdport2}"
            wait_webdriver(base2, proc2)
            sid2 = try_session(base2)
            try:
                visit(base2, sid2, site)
                execute(base2, sid2, "window.startRestartProbe()")
                result["restart"] = await_probe(base2, sid2, "__iwxaRestart", 25)
                result["second_screenshot"] = capture(base2, sid2, "second")
            finally:
                try:
                    wd(base2, f"/session/{sid2}", method="DELETE", timeout=5)
                except Exception:
                    pass
        finally:
            shutdown(proc2, log2)
        result["status"] = "MEASURED"
    except Exception as exc:
        failures.append(f"{type(exc).__name__}: {exc}")
        result["status"] = "INFRA_OR_BROWSER_FAILURE"
    finally:
        if server:
            server.shutdown()
        result["errors"] = failures
        (OUT / "result.json").write_text(json.dumps(result, indent=2, ensure_ascii=False, default=str), encoding="utf-8")
        print(json.dumps(result, indent=2, ensure_ascii=False, default=str))
    return 0 if result["status"] == "MEASURED" else 1

if __name__ == "__main__":
    sys.exit(main())
