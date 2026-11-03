"""Real Goose 1.55.0 CLI to synthetic local OpenAI-compatible HTTP fixture.
This tests protocol and CLI execution, NOT real model inference or IWXA integration.
"""
from __future__ import annotations
import json
import os
import platform
import subprocess
import tempfile
import threading
import time
import traceback
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

TOKEN = "IWXA-GOOSE-FIXTURE-REPLY-20261103"
OUTPUT = Path("artifacts/agent-platforms/goose-cli-contract.json")


class Fixture(BaseHTTPRequestHandler):
    calls = []
    def log_message(self, *_args):
        pass
    def do_GET(self):
        if self.path not in ("/v1/models", "/models"):
            self.send_error(404)
            return
        self.respond(200, {"object": "list", "data": [{"id":"gpt-4o","object":"model"}]})
    def respond(self, status, body):
        payload = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)
    def do_POST(self):
        if self.path.rstrip("/") not in ("/v1/chat/completions", "/chat/completions"):
            self.send_error(404)
            return
        length = int(self.headers.get("Content-Length", "0"))
        if not 2 <= length <= 1_500_000:
            self.send_error(400)
            return
        try:
            data = json.loads(self.rfile.read(length))
            if not isinstance(data, dict) or not isinstance(data.get("messages"), list):
                raise ValueError("invalid model payload")
        except (ValueError, json.JSONDecodeError):
            self.send_error(400)
            return
        self.calls.append({"route":self.path,"model":data.get("model"),
                           "stream":data.get("stream"),"messages":len(data["messages"]),
                           "has_tools":bool(data.get("tools"))})
        if data.get("stream"):
            chunks = [
                {"id":"chatcmpl-iwxa","object":"chat.completion.chunk","created":1,"model":"gpt-4o",
                 "choices":[{"index":0,"delta":{"role":"assistant","content":""},"finish_reason":None}]},
                {"id":"chatcmpl-iwxa","object":"chat.completion.chunk","created":1,"model":"gpt-4o",
                 "choices":[{"index":0,"delta":{"content":TOKEN},"finish_reason":None}]},
                {"id":"chatcmpl-iwxa","object":"chat.completion.chunk","created":1,"model":"gpt-4o",
                 "choices":[{"index":0,"delta":{},"finish_reason":"stop"}]}
            ]
            raw = ("".join("data: " + json.dumps(x) + "\n\n" for x in chunks) + "data: [DONE]\n\n").encode()
            self.send_response(200)
            self.send_header("Content-Type","text/event-stream")
            self.send_header("Content-Length",str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)
        else:
            self.respond(200, {"id":"chatcmpl-iwxa","object":"chat.completion","created":1,
                "model":"gpt-4o","choices":[{"index":0,"finish_reason":"stop",
                "message":{"role":"assistant","content":TOKEN}}],
                "usage":{"prompt_tokens":10,"completion_tokens":10,"total_tokens":20}})


def run(args, env, timeout=70):
    start = time.monotonic()
    try:
        p = subprocess.run(args, env=env, text=True, capture_output=True, timeout=timeout)
        return {"returncode":p.returncode,"elapsed_seconds":round(time.monotonic()-start,3),
                "stdout_tail":p.stdout[-6000:],"stderr_tail":p.stderr[-2800:]}
    except subprocess.TimeoutExpired:
        return {"returncode":124,"elapsed_seconds":round(time.monotonic()-start,3),"error":"TIMEOUT"}


def main():
    result = {"program":"Goose","version_required":"1.55.0","system":platform.platform(),
        "scope":"REAL_VENDOR_CLI_SYNTHETIC_MODEL_ENDPOINT","real_llm":"NOT_TESTED",
        "iwxa_agent_runtime_port":"NOT_TESTED","status":"FAIL"}
    Fixture.calls = []
    server = ThreadingHTTPServer(("127.0.0.1",0), Fixture)
    thread = threading.Thread(target=server.serve_forever,daemon=True)
    thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix="iwxa-goose-public-") as home:
            env = dict(os.environ)
            env.update({"HOME":home,"XDG_CONFIG_HOME":home,"XDG_DATA_HOME":home,
              "GOOSE_PROVIDER":"openai","GOOSE_MODEL":"gpt-4o",
              "OPENAI_HOST":f"http://127.0.0.1:{server.server_port}",
              "OPENAI_BASE_PATH":"v1/chat/completions",
              "OPENAI_API_KEY":"PUBLIC_SYNTHETIC_NO_CREDENTIALS",
              "GOOSE_DISABLE_KEYRING":"1","GOOSE_DISABLE_SESSION_NAMING":"true",
              "GOOSE_MODE":"auto","GOOSE_MAX_TURNS":"2","NO_COLOR":"1"})
            result["version"] = run(["goose","--version"],env,15)
            v=result["version"]
            if v["returncode"] != 0 or "1.55.0" not in v["stdout_tail"]+v["stderr_tail"]:
                raise AssertionError("exact CLI release not available")
            help_run=run(["goose","run","--help"],env,15)
            result["run_help"]={"returncode":help_run["returncode"],
                "json_flag_present":"output-format" in help_run.get("stdout_tail","")+help_run.get("stderr_tail","")}
            if not result["run_help"]["json_flag_present"] or help_run["returncode"] != 0:
                raise AssertionError("structured output CLI contract not available")
            actual=run(["goose","run","--provider","openai","--model","gpt-4o",
                        "--no-session","--output-format","json","-t",
                        "Provide a short response to the fixture. No tools required."],env,75)
            result["execution"]=actual
            result["observed_model_http"]=Fixture.calls.copy()
            if not Fixture.calls:
                raise AssertionError("Goose did not call local model endpoint")
            if actual["returncode"] != 0 or TOKEN not in actual.get("stdout_tail",""):
                raise AssertionError("CLI did not complete with the exact synthetic response")
            result["status"]="PASS"
    except Exception as exc:
        result["error"]=type(exc).__name__+": "+str(exc)
        result["traceback_tail"]=traceback.format_exc()[-1400:]
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=3)
    OUTPUT.parent.mkdir(parents=True,exist_ok=True)
    OUTPUT.write_text(json.dumps(result,ensure_ascii=False,indent=2),encoding="utf-8")
    print("IWXA_GOOSE_CLI_CONTRACT",json.dumps(result,ensure_ascii=False))
    return 0 if result["status"]=="PASS" else 1


if __name__=="__main__":
    raise SystemExit(main())
