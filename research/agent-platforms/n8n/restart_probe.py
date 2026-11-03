"""Real n8n 2.46.4 published webhook after a complete server process restart.

Run only in disposable public Linux CI. Shares one persistent temporary n8n
SQLite profile across two separate n8n Docker *server processes*. Imports and
publishes the workflow once. Independently reads HTTP side-effects in each phase.

No private IWXA sources, real accounts, cloud models, or user records.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import platform
import subprocess
import tempfile
import threading
import time
import traceback
from http.server import ThreadingHTTPServer

from probe import PostLedger, VERSION, cli
from webhook_probe import (
    IMAGE, free_port, request_workflow, wait_for_server, workflow_for_webhook,
)

OUTPUT = Path("artifacts/agent-platforms/n8n-restart-real.json")
ENV_KEYS = (
    "N8N_USER_FOLDER", "N8N_ENCRYPTION_KEY", "N8N_DIAGNOSTICS_ENABLED",
    "N8N_VERSION_NOTIFICATIONS_ENABLED", "N8N_TEMPLATES_ENABLED",
    "N8N_PERSONALIZATION_ENABLED", "N8N_RUNNERS_ENABLED", "N8N_LOG_LEVEL",
    "N8N_PORT", "N8N_HOST", "N8N_LISTEN_ADDRESS", "N8N_PROTOCOL",
    "WEBHOOK_URL", "N8N_EDITOR_BASE_URL", "N8N_SECURE_COOKIE",
)


def start_server(name: str, env: dict[str, str]) -> None:
    tmpdir = os.environ["RUNNER_TEMP"]
    args = [
        "docker", "run", "--detach", "--rm", "--network", "host",
        "--name", name, "--user", f"{os.getuid()}:{os.getgid()}",
        "--volume", f"{tmpdir}:{tmpdir}", "--env", f"HOME={tmpdir}",
    ]
    for key in ENV_KEYS:
        args.extend(("--env", key))
    args.extend((IMAGE, "start"))
    child = subprocess.run(args, env=env, text=True, capture_output=True, timeout=30)
    if child.returncode:
        raise RuntimeError("start_server failed: " + child.stderr[-1200:])


def stop_server(name: str, evidence: dict, phase: str) -> None:
    observed = subprocess.run(
        ["docker", "logs", "--tail", "40", name],
        capture_output=True, text=True, timeout=10,
    )
    evidence[phase + "_server_log_tail"] = (observed.stdout + observed.stderr)[-1800:]
    stop = subprocess.run(
        ["docker", "stop", "--time", "3", name],
        capture_output=True, text=True, timeout=14,
    )
    evidence[phase + "_server_stop_exit"] = stop.returncode
    if stop.returncode != 0:
        raise RuntimeError("n8n server stop failed: " + stop.stderr[-300:])


def invoke_after_registration(port: int, key: str) -> dict:
    # Only retry 404: no workflow was registered and therefore no steps ran.
    # Never retry timeout, connection drop, 500 or an unknown external outcome.
    for n in range(16):
        response = request_workflow(port, "ok", key)
        if response["status"] != 404:
            response["registration_404_count"] = n
            return response
        time.sleep(1)
    raise RuntimeError("published webhook never registered")


def exercise(evidence: dict) -> None:
    if "RUNNER_TEMP" not in os.environ:
        raise RuntimeError("requires ephemeral public runner")
    with tempfile.TemporaryDirectory(
        dir=os.environ["RUNNER_TEMP"], prefix="iwxa-n8n-restart-"
    ) as directory:
        root = Path(directory)
        env = dict(os.environ)
        env.update({
            "N8N_USER_FOLDER": str(root / "user"),
            "N8N_ENCRYPTION_KEY": "IWXA_PUBLIC_RESTART_FIXTURE_NOT_A_REAL_SECRET",
            "N8N_DIAGNOSTICS_ENABLED": "false",
            "N8N_VERSION_NOTIFICATIONS_ENABLED": "false",
            "N8N_TEMPLATES_ENABLED": "false",
            "N8N_PERSONALIZATION_ENABLED": "false",
            "N8N_RUNNERS_ENABLED": "false",
            "N8N_LOG_LEVEL": "error",
        })
        with PostLedger.lock:
            PostLedger.entries = []
        ledger = ThreadingHTTPServer(("127.0.0.1", 0), PostLedger)
        thread = threading.Thread(target=ledger.serve_forever, daemon=True)
        thread.start()
        name = "iwxa-n8n-restart-" + str(os.getpid())
        running = False
        try:
            version = cli(env, "--version", timeout=22)
            evidence["actual_n8n_version"] = version["output_tail"].strip()[-110:]
            if version["returncode"] != 0 or VERSION not in version["output_tail"]:
                raise AssertionError("n8n version differs from pinned 2.46.4")
            workflow = workflow_for_webhook("ok", ledger.server_port)
            path = root / "published.json"
            path.write_text(json.dumps(workflow), encoding="utf-8")
            imported = cli(env, "import:workflow", "--input", str(path), timeout=85)
            published = cli(env, "publish:workflow", "--id", workflow["id"], timeout=85)
            evidence["single_import_exit"] = imported["returncode"]
            evidence["single_publish_exit"] = published["returncode"]
            if imported["returncode"] or published["returncode"]:
                raise AssertionError(
                    "workflow not imported/published: "
                    + imported["output_tail"][-300:] + published["output_tail"][-300:]
                )
            port = free_port()
            env.update({
                "N8N_PORT": str(port),
                "N8N_HOST": "127.0.0.1",
                "N8N_LISTEN_ADDRESS": "127.0.0.1",
                "N8N_PROTOCOL": "http",
                "N8N_SECURE_COOKIE": "false",
                "WEBHOOK_URL": f"http://127.0.0.1:{port}/",
                "N8N_EDITOR_BASE_URL": f"http://127.0.0.1:{port}/",
            })
            for phase, key in (
                ("before_restart", "IWXA-PUBLIC-BEFORE-RESTART"),
                ("after_restart", "IWXA-PUBLIC-AFTER-RESTART"),
            ):
                started = time.monotonic()
                start_server(name, env)
                running = True
                wait_for_server(port, name, seconds=80)
                response = invoke_after_registration(port, key)
                with PostLedger.lock:
                    receipts = [dict(row) for row in PostLedger.entries]
                evidence[phase] = {
                    "http_status": response["status"],
                    "webhook_response": response["body_tail"][:600],
                    "registration_404_count": response["registration_404_count"],
                    "ledger_paths": [r["path"] for r in receipts],
                    "phase_seconds": round(time.monotonic() - started, 3),
                }
                expected = ["/ok/1", "/ok/2"] * (1 if phase == "before_restart" else 2)
                if response["status"] != 200 or "/ok/2" not in response["body_tail"]:
                    raise AssertionError("published webhook not live in " + phase)
                if evidence[phase]["ledger_paths"] != expected:
                    raise AssertionError(
                        f"unexpected independent effects in {phase}: {receipts!r}"
                    )
                stop_server(name, evidence, phase)
                running = False
            evidence["workflow_imported_once"] = True
            evidence["persistent_profile_reused"] = True
            evidence["restart_did_not_create_unsolicited_effects"] = True
            evidence["status"] = "PASS"
        finally:
            if running:
                try:
                    stop_server(name, evidence, "failed_run")
                except (OSError, RuntimeError, subprocess.SubprocessError) as err:
                    evidence["cleanup_error"] = str(err)[:200]
            ledger.shutdown()
            ledger.server_close()
            thread.join(timeout=3)


def main() -> int:
    report = {
        "product": "n8n",
        "version_expected": VERSION,
        "runner": platform.platform(),
        "scope": "REAL_PUBLISHED_WEBHOOK_AFTER_SERVER_PROCESS_RESTART",
        "iwxa_private_adapter": "NOT_TESTED",
        "iwxa_task_result": "NOT_TESTED",
        "model_calls": 0,
        "status": "FAIL",
    }
    try:
        exercise(report)
    except Exception as err:
        report["error"] = type(err).__name__ + ": " + str(err)[:550]
        report["traceback_tail"] = traceback.format_exc()[-1400:]
    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print("IWXA_N8N_RESTART_REAL", json.dumps(report, ensure_ascii=False))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
