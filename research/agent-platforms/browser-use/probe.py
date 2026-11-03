"""Independent public Browser Use execution probe.

Exercises the real vendor browser/session and a real HTTP POST in a disposable,
localhost-only fixture. Does NOT test model inference or IWXA private code.
"""
from __future__ import annotations

import asyncio
import importlib.metadata
import json
import os
import platform
import tempfile
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

EXPECTED_VERSION = "0.13.14"
RESULT_PATH = Path("artifacts/agent-platforms/browser-use-execution.json")


class FixtureHandler(BaseHTTPRequestHandler):
    records: list[dict[str, str]] = []
    html = b"""<!doctype html><html lang="en"><meta charset="utf-8"><title>IWXA public browser action test</title>
<body><h1>IWXA browser action fixture</h1>
<form id="entry"><label>Reference <input id="reference" name="reference" required></label>
<button id="submit" type="submit">Send</button></form><p id="status">WAITING</p>
<script>
document.querySelector('#entry').addEventListener('submit',async(event)=>{
event.preventDefault();
const reference=document.querySelector('#reference').value;
const res=await fetch('/submit',{method:'POST',headers:{'Content-Type':'application/json'},
body:JSON.stringify({reference})});
document.querySelector('#status').textContent=res.ok?'SAVED':'FAILED';
});
</script></body></html>"""

    def log_message(self, *_args: object) -> None:
        return

    def do_GET(self) -> None:
        if self.path == "/":
            body, kind, code = self.html, "text/html; charset=utf-8", 200
        elif self.path == "/records":
            body, kind, code = json.dumps(self.records).encode(), "application/json", 200
        else:
            body, kind, code = b"not found", "text/plain", 404
        self.send_response(code)
        self.send_header("Content-Type", kind)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:
        if self.path != "/submit":
            self.send_error(404)
            return
        try:
            length = int(self.headers.get("Content-Length", "0"))
            if length < 1 or length > 2048:
                raise ValueError("invalid body length")
            value = json.loads(self.rfile.read(length))
            if not isinstance(value, dict) or value.get("reference") != "IWXA-PUBLIC-TEST":
                raise ValueError("unexpected payload")
            self.records.append({"reference": value["reference"]})
        except (ValueError, json.JSONDecodeError):
            self.send_error(400)
            return
        self.send_response(200)
        self.send_header("Content-Length", "2")
        self.end_headers()
        self.wfile.write(b"OK")


async def find_element(page, selector: str):
    for _ in range(35):
        elements = await page.get_elements_by_css_selector(selector)
        if elements:
            return elements[0]
        await asyncio.sleep(0.2)
    raise RuntimeError(f"element not found: {selector}")


async def exercise() -> dict:
    from browser_use import Browser

    FixtureHandler.records = []
    server = ThreadingHTTPServer(("127.0.0.1", 0), FixtureHandler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    browser = None
    started = time.monotonic()
    evidence = {"fixture_origin": "localhost", "model_calls": 0, "vendor_agent_inference": "NOT_TESTED"}
    try:
        with tempfile.TemporaryDirectory(prefix="iwxa-public-browser-use-") as profile:
            browser = Browser(
                headless=True,
                user_data_dir=profile,
                chromium_sandbox=False,
                enable_default_extensions=False,
            )
            await browser.start()
            page = await browser.new_page()
            await page.goto(f"http://127.0.0.1:{server.server_port}/")
            await find_element(page, "#reference")

            # Negative case: HTML required field should stop an empty submit.
            await (await find_element(page, "#submit")).click()
            await asyncio.sleep(0.4)
            if FixtureHandler.records:
                raise AssertionError("empty required form caused an external POST")
            evidence["negative_empty_submit"] = "PASS"

            # Real browser interaction, real HTTP side effect, independent readback.
            await (await find_element(page, "#reference")).fill("IWXA-PUBLIC-TEST")
            await (await find_element(page, "#submit")).click()
            for _ in range(40):
                if FixtureHandler.records:
                    break
                await asyncio.sleep(0.2)
            if FixtureHandler.records != [{"reference": "IWXA-PUBLIC-TEST"}]:
                raise AssertionError(f"actual HTTP POST not observed: {FixtureHandler.records!r}")
            evidence["external_post_readback"] = "PASS"

            for _ in range(30):
                dom_status = await page.evaluate("() => document.querySelector('#status')?.textContent")
                if "SAVED" in dom_status:
                    break
                await asyncio.sleep(0.2)
            else:
                raise AssertionError("success not rendered in actual DOM")
            evidence["browser_dom_readback"] = "PASS"
            evidence["document_title"] = await page.get_title()
            evidence["elapsed_seconds"] = round(time.monotonic() - started, 3)
    finally:
        if browser:
            await browser.kill()
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
    return evidence


def main() -> int:
    RESULT_PATH.parent.mkdir(parents=True, exist_ok=True)
    result = {
        "product": "browser-use",
        "version": importlib.metadata.version("browser-use"),
        "expected_version": EXPECTED_VERSION,
        "system": platform.platform(),
        "python": platform.python_version(),
        "scope": "REAL_BROWSER_SESSION_AND_HTTP_EFFECT_NO_LLM",
        "status": "FAIL",
        "source": "public third-party installed package, no private IWXA files",
    }
    try:
        if result["version"] != EXPECTED_VERSION:
            raise RuntimeError("unexpected installed browser-use version")
        result.update(asyncio.run(asyncio.wait_for(exercise(), timeout=150)))
        result["status"] = "PASS"
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {str(exc)[:400]}"
        traceback.print_exc()
    RESULT_PATH.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("IWXA_BROWSER_USE_PROBE", json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
