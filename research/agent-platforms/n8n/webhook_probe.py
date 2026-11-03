"""Independent n8n 2.46.4 *server webhook* test, matching IWXA's HTTP route shape.

Uses the official n8n container and disposable SQLite profile; no private IWXA code.
The receiving ledger is independent from the workflow's own returned response.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import platform
import socket
import subprocess
import tempfile
import threading
import time
import traceback
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from probe import VERSION, PostLedger, cli, make_workflow
from http.server import ThreadingHTTPServer

ARTIFACT = Path("artifacts/agent-platforms/n8n-webhook-real.json")
IMAGE = "docker.n8n.io/n8nio/n8n:2.46.4"


def workflow_for_webhook(mode: str, receipt_port: int) -> dict:
    workflow_id = "iwxawebhook" + mode + "20261103"
    w = make_workflow(workflow_id, receipt_port, mode)
    w["name"] = "IWXA published webhook " + mode
    w["nodes"][0] = {
        "id": "c87acbc2-0000-4000-8000-000000000001",
        "name": "Webhook", "type": "n8n-nodes-base.webhook",
        "typeVersion": 2.1, "webhookId": "iwxa-public-" + mode,
        "parameters": {
            "path": "iwxa-public-" + mode,
            "httpMethod": "POST", "responseMode": "lastNode",
            "responseData": "firstEntryJson", "options": {},
        },
        "position": [200, 300],
    }
    w["connections"]["Webhook"] = w["connections"].pop("Start")
    return w


def request_workflow(port: int, mode: str, key: str) -> dict:
    body = {"iwxa_operation": "workflow.execute", "workflow_target": mode,
            "idempotency_key": key, "arguments": {"case": mode}}
    req = Request(
        f"http://127.0.0.1:{port}/webhook/iwxa-public-{mode}",
        data=json.dumps(body).encode(), method="POST",
        headers={
            "Content-Type": "application/json",
            "X-IWXA-Idempotency-Key": key,
        },
    )
    start = time.monotonic()
    try:
        with urlopen(req, timeout=30) as response:
            status = response.status
            text = response.read(16000).decode("utf-8", "replace")
    except HTTPError as exc:
        status = exc.code
        text = exc.read(16000).decode("utf-8", "replace")
    return {"status": status, "body_tail": text[-1600:],
            "elapsed_seconds": round(time.monotonic() - start, 3)}


def free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return sock.getsockname()[1]


def wait_for_server(port: int, container: str, seconds: float = 95):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        with socket.socket() as s:
            s.settimeout(1.0)
            if s.connect_ex(("127.0.0.1", port)) == 0:
                return
        state = subprocess.run(["docker", "inspect", "-f", "{{.State.Running}}",
                                container], text=True, capture_output=True)
        if state.returncode != 0 or state.stdout.strip() != "true":
            raise RuntimeError("n8n server container exited before binding port")
        time.sleep(1.0)
    raise TimeoutError("n8n server did not bind HTTP port within 95 seconds")


def execute(result: dict) -> None:
    with tempfile.TemporaryDirectory(prefix="iwxa-webhook-n8n-") as directory:
        base = Path(directory)
        env = dict(os.environ)
        env.update({
            "N8N_USER_FOLDER": str(base / "user"),
            "N8N_ENCRYPTION_KEY": "IWXA_PUBLIC_WEBHOOK_DISPOSABLE_KEY_ONLY",
            "N8N_DIAGNOSTICS_ENABLED": "false",
            "N8N_VERSION_NOTIFICATIONS_ENABLED": "false",
            "N8N_TEMPLATES_ENABLED": "false",
            "N8N_PERSONALIZATION_ENABLED": "false",
            "N8N_RUNNERS_ENABLED": "false",
            "N8N_LOG_LEVEL": "error",
        })
        with PostLedger.lock:
            PostLedger.entries = []
        receiver = ThreadingHTTPServer(("127.0.0.1", 0), PostLedger)
        thread = threading.Thread(target=receiver.serve_forever, daemon=True)
        thread.start()
        server_container = "iwxa-n8n-webhook-" + str(os.getpid())
        try:
            for mode in ("ok", "fail"):
                workflow = workflow_for_webhook(mode, receiver.server_port)
                file = base / f"{mode}.json"
                file.write_text(json.dumps(workflow), encoding="utf-8")
                imported = cli(env, "import:workflow", "--input", str(file), timeout=80)
                result[f"{mode}_import"] = imported
                if imported["returncode"] != 0:
                    raise AssertionError(f"import {mode} failed: {imported['output_tail']}")
                published = cli(env, "publish:workflow", "--id", workflow["id"], timeout=80)
                result[f"{mode}_publish"] = published
                if published["returncode"] != 0:
                    raise AssertionError(f"publish {mode} failed: {published['output_tail']}")

            port = free_port()
            env.update({
                "N8N_PORT": str(port),
                "N8N_HOST": "127.0.0.1",
                "N8N_LISTEN_ADDRESS": "127.0.0.1",
                "N8N_PROTOCOL": "http",
                "WEBHOOK_URL": f"http://127.0.0.1:{port}/",
                "N8N_EDITOR_BASE_URL": f"http://127.0.0.1:{port}/",
                "N8N_SECURE_COOKIE": "false",
            })
            allowed_env = (
                "N8N_USER_FOLDER", "N8N_ENCRYPTION_KEY", "N8N_DIAGNOSTICS_ENABLED",
                "N8N_VERSION_NOTIFICATIONS_ENABLED", "N8N_TEMPLATES_ENABLED",
                "N8N_PERSONALIZATION_ENABLED", "N8N_RUNNERS_ENABLED",
                "N8N_LOG_LEVEL", "N8N_PORT", "N8N_HOST", "N8N_LISTEN_ADDRESS",
                "N8N_PROTOCOL", "WEBHOOK_URL", "N8N_EDITOR_BASE_URL",
                "N8N_SECURE_COOKIE",
            )
            runner_temp = os.environ["RUNNER_TEMP"]
            args = [
                "docker", "run", "--detach", "--rm", "--network", "host",
                "--name", server_container,
                "--user", f"{os.getuid()}:{os.getgid()}",
                "--volume", f"{runner_temp}:{runner_temp}",
                "--env", f"HOME={runner_temp}",
            ]
            for name in allowed_env:
                args += ["--env", name]
            args += [IMAGE, "start"]
            spawned = subprocess.run(args, env=env, capture_output=True,
                                     text=True, timeout=30)
            if spawned.returncode != 0:
                raise RuntimeError("failed to start n8n: " + spawned.stderr[-1200:])
            wait_for_server(port, server_container)
            result["server_http_ready"] = True
            key = "IWXA-SAME-PUBLIC-KEY"
            result["ok_response"] = request_workflow(port, "ok", key)
            with PostLedger.lock:
                after_first = [dict(x) for x in PostLedger.entries]
            result["ok_server_receipts"] = after_first
            if result["ok_response"]["status"] != 200 or "/ok/2" not in result["ok_response"]["body_tail"]:
                raise AssertionError("positive webhook did not acknowledge final HTTP node")
            if [x["path"] for x in after_first] != ["/ok/1", "/ok/2"]:
                raise AssertionError("positive webhook did not create exactly two HTTP effects")

            # Same header and same request key: test what the external engine *actually* does.
            result["duplicate_key_response"] = request_workflow(port, "ok", key)
            with PostLedger.lock:
                after_duplicate = [dict(x) for x in PostLedger.entries]
            result["duplicate_key_receipts"] = after_duplicate[2:]
            result["duplicate_key_repeats_effects"] = [x["path"] for x in after_duplicate] == [
                "/ok/1", "/ok/2", "/ok/1", "/ok/2",
            ]
            if result["duplicate_key_response"]["status"] != 200:
                raise AssertionError("duplicate webhook HTTP was unexpectedly rejected")
            if not result["duplicate_key_repeats_effects"]:
                raise AssertionError("unexpected duplicate-key behavior; inspect receipts before claiming idempotency")

            result["fail_response"] = request_workflow(port, "fail", "IWXA-FAIL-PUBLIC")
            with PostLedger.lock:
                after_fail = [dict(x) for x in PostLedger.entries]
            result["fail_server_receipts"] = after_fail[4:]
            if [x["path"] for x in after_fail[4:]] != ["/fail/1", "/fail/2"]:
                raise AssertionError("failing webhook did not execute both steps exactly once")
            if result["fail_response"]["status"] < 400:
                raise AssertionError("workflow failed on 503 but webhook returned false-success HTTP")
            result["status"] = "PASS"
            result["scope"] = "REAL_PUBLISHED_N8N_WEBHOOK_HTTP_EFFECTS_NO_IWXA_PRIVATE_CODE"
        finally:
            # Retain server logs only for troubleshooting; never persist cookies or keys.
            inspect = subprocess.run(
                ["docker", "logs", "--tail", "60", server_container],
                capture_output=True, text=True, timeout=12,
            )
            result["n8n_server_log_tail"] = (inspect.stdout + inspect.stderr)[-2600:]
            subprocess.run(["docker", "stop", "--time", "3", server_container],
                           capture_output=True, timeout=14)
            subprocess.run(["docker", "rm", "-f", server_container],
                           capture_output=True, timeout=10)
            receiver.shutdown()
            receiver.server_close()
            thread.join(timeout=3)


def main() -> int:
    result = {
        "product": "n8n", "version": VERSION, "runner": platform.platform(),
        "scope": "PUBLISHED_N8N_WEBHOOK", "model_calls": 0,
        "iwxa_private_adapters": "NOT_TESTED", "status": "FAIL",
    }
    try:
        execute(result)
    except Exception as exc:
        result["error"] = f"{type(exc).__name__}: {str(exc)[:1100]}"
        result["traceback_tail"] = traceback.format_exc()[-2000:]
    ARTIFACT.parent.mkdir(parents=True, exist_ok=True)
    ARTIFACT.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("IWXA_N8N_WEBHOOK", json.dumps(result, ensure_ascii=False))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
