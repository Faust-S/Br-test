"""Public n8n 2.46.4 real CLI / HTTP execution trial; no models or private sources."""
from __future__ import annotations

import argparse
import json
import os
import platform
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

VERSION = "2.46.4"
ARTIFACT = Path("artifacts/agent-platforms/n8n-real-workflow.json")


class PostLedger(BaseHTTPRequestHandler):
    entries: list[dict[str, object]] = []
    lock = threading.Lock()

    def log_message(self, *_args: object) -> None:
        pass

    def do_POST(self) -> None:
        try:
            size = int(self.headers.get("Content-Length", "0"))
            if not 1 <= size <= 2048:
                raise ValueError("invalid payload length")
            body = json.loads(self.rfile.read(size))
            if not isinstance(body, dict) or body.get("token") != "IWXA-N8N-PUBLIC":
                raise ValueError("unexpected token")
            if self.path not in {"/ok/1", "/ok/2", "/fail/1", "/fail/2"}:
                self.send_error(404)
                return
            with self.lock:
                self.entries.append({"path": self.path, "token": body["token"], "method": "POST"})
        except (ValueError, json.JSONDecodeError):
            self.send_error(400)
            return
        code = 503 if self.path == "/fail/2" else 200
        data = json.dumps({"ack": self.path, "ok": code == 200}).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def make_workflow(workflow_id: str, port: int, mode: str) -> dict[str, object]:
    assert mode in {"ok", "fail"}
    nodes = [{"id": "c87acbc2-0000-4000-8000-000000000001", "name": "Start",
              "type": "n8n-nodes-base.manualTrigger", "typeVersion": 1,
              "parameters": {}, "position": [200, 300]}]
    for step in (1, 2):
        nodes.append({
            "id": f"c87acbc2-0000-4000-8000-00000000000{step + 1}",
            "name": f"HTTP {step}", "type": "n8n-nodes-base.httpRequest",
            "typeVersion": 4.2, "position": [200 + step * 220, 300],
            "parameters": {"method": "POST",
                           "url": f"http://127.0.0.1:{port}/{mode}/{step}",
                           "sendBody": True, "specifyBody": "json",
                           "jsonBody": json.dumps({"token": "IWXA-N8N-PUBLIC", "step": step}),
                           "options": {"timeout": 10000}},
        })
    return {
        "id": workflow_id, "name": f"IWXA public n8n {mode} two HTTP steps",
        "nodes": nodes, "connections": {
            "Start": {"main": [[{"node": "HTTP 1", "type": "main", "index": 0}]]},
            "HTTP 1": {"main": [[{"node": "HTTP 2", "type": "main", "index": 0}]]}},
        "settings": {"executionOrder": "v1"},
        "staticData": None, "active": False, "tags": [],
    }


def cli(env: dict[str, str], *args: str, timeout: int = 110) -> dict[str, object]:
    started = time.monotonic()
    try:
        proc = subprocess.run(["n8n", *args], env=env, capture_output=True,
                              text=True, timeout=timeout, check=False)
        return {"args": list(args), "returncode": proc.returncode,
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "output_tail": (proc.stdout + "\n" + proc.stderr)[-2800:]}
    except subprocess.TimeoutExpired as exc:
        return {"args": list(args), "returncode": 124,
                "elapsed_seconds": round(time.monotonic() - started, 3),
                "output_tail": f"TIMEOUT: {str(exc)[:250]}"}


def execute(result: dict[str, object]) -> None:
    with tempfile.TemporaryDirectory(prefix="iwxa-public-n8n-") as folder:
        env = dict(os.environ)
        env.update({
            "N8N_USER_FOLDER": str(Path(folder) / "user"),
            "N8N_DIAGNOSTICS_ENABLED": "false",
            "N8N_VERSION_NOTIFICATIONS_ENABLED": "false",
            "N8N_TEMPLATES_ENABLED": "false",
            "N8N_PERSONALIZATION_ENABLED": "false",
            "N8N_RUNNERS_ENABLED": "false",
            "N8N_HIRING_BANNER_ENABLED": "false",
            "N8N_LOG_LEVEL": "error",
            "N8N_ENCRYPTION_KEY": "IWXA_PUBLIC_DISPOSABLE_TEST_ONLY_32CHARS",
        })
        with PostLedger.lock:
            PostLedger.entries = []
        server = ThreadingHTTPServer(("127.0.0.1", 0), PostLedger)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            version = cli(env, "--version", timeout=20)
            result["version_check"] = version
            if version["returncode"] != 0 or VERSION not in str(version["output_tail"]):
                raise AssertionError(f"n8n version mismatch: {version}")
            for mode in ("ok", "fail"):
                workflow_id = "iwxapublic" + mode + "20261103"
                workflow_file = Path(folder) / f"{mode}.json"
                workflow_file.write_text(json.dumps(make_workflow(
                    workflow_id, server.server_port, mode)), encoding="utf-8")
                imported = cli(env, "import:workflow", "--input", str(workflow_file))
                result[f"{mode}_import"] = imported
                if imported["returncode"] != 0:
                    raise AssertionError(f"n8n {mode} import failed: {imported['output_tail']}")
                with PostLedger.lock:
                    before = len(PostLedger.entries)
                run = cli(env, "execute", "--id", workflow_id)
                result[f"{mode}_execution"] = run
                time.sleep(0.5)
                with PostLedger.lock:
                    recorded = [dict(x) for x in PostLedger.entries[before:]]
                result[f"{mode}_observed_http"] = recorded
                expected = [f"/{mode}/1", f"/{mode}/2"]
                if [x["path"] for x in recorded] != expected:
                    raise AssertionError(f"{mode} expected {expected}, got {recorded!r}")
                if mode == "ok" and run["returncode"] != 0:
                    raise AssertionError(f"successful workflow exited {run['returncode']}")
                if mode == "fail":
                    output = str(run["output_tail"]).lower()
                    detected = run["returncode"] != 0 or any(
                        marker in output for marker in (
                            "503", "service unavailable", "workflow execution had an error",
                            "error running workflow"))
                    result["negative_503_observed_by_engine"] = detected
                    if not detected:
                        raise AssertionError("503 received, but n8n failure signal not observed")
            result["status"] = "PASS"
            result["evidence_scope"] = "Real n8n CLI workflows and two HTTP effects per scenario; no model or IWXA adapter"
        finally:
            server.shutdown()
            server.server_close()
            thread.join(timeout=3)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output", default=str(ARTIFACT))
    args = parser.parse_args()
    result: dict[str, object] = {
        "program": "n8n", "version_expected": VERSION,
        "system": platform.platform(), "python": platform.python_version(),
        "scope": "REAL_N8N_CLI_TWO_STEP_HTTP", "model_calls": 0,
        "iwxa_private_adapter": "NOT_TESTED", "status": "FAIL",
    }
    try:
        execute(result)
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {str(exc)[:800]}"
        result["traceback"] = traceback.format_exc()[-2600:]
    path = Path(args.output)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result, indent=2, ensure_ascii=False), encoding="utf-8")
    print("IWXA_N8N_REAL_WORKFLOW", json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
