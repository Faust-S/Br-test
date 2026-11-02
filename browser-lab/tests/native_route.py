"""Real Obscura CDP integration: generic local fixture only, no T2 or private source."""
import base64
import hashlib
import http.server
import json
import os
import platform
import socket
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "comparison"))
import run

OUT = ROOT / "artifacts" / "browser-lab"
OUT.mkdir(parents=True, exist_ok=True)
PNG = b"\x89PNG\r\n\x1a\n"


def check(name, operation, report):
    started = time.monotonic()
    try:
        value = operation()
        report["checks"][name] = {
            "status": "PASS", "value": value,
            "elapsed_ms": round((time.monotonic() - started) * 1000)
        }
        return value
    except Exception as error:
        report["checks"][name] = {
            "status": "FAIL", "error": repr(error),
            "elapsed_ms": round((time.monotonic() - started) * 1000)
        }
        raise


def center(client, selector):
    return client.evaluate(
        "(()=>{const r=document.querySelector(" + json.dumps(selector) +
        ").getBoundingClientRect();return [r.left+r.width/2,r.top+r.height/2]})()"
    )


def main():
    from architecture_v026 import acquire_v026

    engine, identity = acquire_v026()
    report = {
        "platform": platform.platform(), "source_sha": os.getenv("GITHUB_SHA"),
        "identity": identity, "scope": "single Obscura engine, real local fixture",
        "not_qualified": ["hostile Internet", "T2", "OS sandbox", "native installed GUI"],
        "checks": {},
    }
    server = client = process = log = None
    with tempfile.TemporaryDirectory(prefix="obscura-public-lab-") as directory:
        temp = Path(directory)
        try:
            class Fixture(http.server.SimpleHTTPRequestHandler):
                def __init__(self, *args, **kwargs):
                    super().__init__(*args, directory=str(ROOT), **kwargs)

                def log_message(self, *args):
                    pass

            server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Fixture)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            with socket.socket() as sock:
                sock.bind(("127.0.0.1", 0))
                cdp_port = sock.getsockname()[1]
            log = (OUT / "native-route.log").open("wb")
            engine_start = time.monotonic()
            process = subprocess.Popen([
                str(engine), "serve", "--host", "127.0.0.1", "--port",
                str(cdp_port), "--max-connections", "1",
                "--storage-dir", str(temp / "profile"), "--allow-private-network"
            ], cwd=engine.parent, stdout=log, stderr=subprocess.STDOUT)
            deadline = time.monotonic() + 30
            while time.monotonic() < deadline:
                if process.poll() is not None:
                    raise RuntimeError("Obscura exited before CDP ready")
                try:
                    client = run.CDP(f"ws://127.0.0.1:{cdp_port}/")
                    break
                except Exception:
                    time.sleep(.2)
            if not client:
                raise TimeoutError("CDP connection unavailable")
            report["engine_cdp_ready_ms"] = round((time.monotonic() - engine_start) * 1000)
            url = f"http://127.0.0.1:{server.server_port}/comparison/iwxa-ui-fixture.html"

            def navigate():
                client.call("Emulation.setDeviceMetricsOverride", {
                    "width": 1080, "height": 700, "deviceScaleFactor": 1, "mobile": False
                })
                client.visit(url)
                return run.poll(client, "document.readyState", lambda x: x == "complete", 20)
            check("navigate", navigate, report)

            def screenshot():
                response = client.call("Page.captureScreenshot", {"format": "png"})
                decoded = base64.b64decode(response["data"], validate=True)
                assert decoded.startswith(PNG)
                assert len(decoded) > 500
                (OUT / "native-route.png").write_bytes(decoded)
                return {"bytes":len(decoded), "sha256":hashlib.sha256(decoded).hexdigest()}
            check("render", screenshot, report)

            def click():
                x, y = center(client, "#increment")
                for kind in ("mousePressed", "mouseReleased"):
                    client.call("Input.dispatchMouseEvent", {
                        "type": kind, "x": x, "y": y, "button":"left", "clickCount":1
                    })
                return run.poll(client,"window.__iwxaUi.clicked",lambda x:x==1,8)
            check("pointer", click, report)

            def type_input():
                x, y = center(client, "#entry")
                for kind in ("mousePressed", "mouseReleased"):
                    client.call("Input.dispatchMouseEvent", {
                        "type": kind, "x":x, "y":y, "button":"left", "clickCount":1
                    })
                client.call("Input.insertText", {"text": "PUBLIC-LAB"})
                return run.poll(client, "window.__iwxaUi.typed", lambda x:x=="PUBLIC-LAB",8)
            check("keyboard", type_input, report)

            def scroll():
                client.call("Input.dispatchMouseEvent", {
                    "type":"mouseWheel", "x":160, "y":300, "deltaX":0, "deltaY":620
                })
                return run.poll(client, "window.scrollY", lambda y:y>100,8)
            check("wheel", scroll, report)

            def tabs():
                new = client.call("Target.createTarget", {"url":"about:blank"})["targetId"]
                sid = client.call("Target.attachToTarget", {"targetId":new, "flatten":True})["sessionId"]
                original = client.session
                try:
                    client.session = sid
                    client.call("Page.enable")
                    client.call("Runtime.enable")
                    client.visit(url)
                    run.poll(client,"document.readyState",lambda v:v=="complete",15)
                    marker = client.evaluate("location.pathname")
                finally:
                    client.session = original
                existing = client.evaluate("window.__iwxaUi.clicked")
                client.call("Target.closeTarget", {"targetId":new})
                assert marker.endswith("iwxa-ui-fixture.html") and existing == 1
                return {"new_tab_path":marker, "original_tab_clicks":existing}
            check("tab_isolation", tabs, report)

            def history():
                client.visit(url + "?route=second")
                run.poll(client,"location.search",lambda x:x=="?route=second",8)
                h = client.call("Page.getNavigationHistory")
                index = h.get("currentIndex",0)
                assert index > 0
                entries = h["entries"]
                client.call("Page.navigateToHistoryEntry", {"entryId":entries[index-1]["id"]})
                run.poll(client,"location.search",lambda x:x=="",8)
                return {"entries":len(entries),"back":True}
            check("back_navigation", history, report)
            report["status"] = "PASS_REAL_ENGINE_ROUTE"
        except Exception as exc:
            report["status"] = "FAIL_REAL_ENGINE_ROUTE"
            report["error"] = repr(exc)
        finally:
            if client:
                try:
                    client.close()
                except Exception:
                    pass
            if process and process.poll() is None:
                process.terminate()
                try:
                    process.wait(timeout=8)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=5)
            if log:
                log.close()
            if server:
                server.shutdown()
        (OUT / "native-route.json").write_text(
            json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8"
        )
    print(json.dumps({"status":report["status"],"checks":report["checks"],
                      "error":report.get("error")}, indent=2))
    return 0 if report["status"] == "PASS_REAL_ENGINE_ROUTE" else 1


if __name__ == "__main__":
    sys.exit(main())
