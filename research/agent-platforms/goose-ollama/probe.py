"""Real small-model inference with actual Goose 1.55.0 and pinned local Ollama.

No private sources, cloud inference, tokens, credentials, or installed IWXA Agent.
The task requires reading one real file and writing another with computed content.
"""
from __future__ import annotations
import json
import os
from pathlib import Path
import platform
import subprocess
import tempfile
import time
import traceback
from urllib.request import urlopen

REPORT = Path("artifacts/agent-platforms/goose-ollama-real.json")
MODEL = "qwen2.5:0.5b"


def run(args, env, cwd, timeout=165):
    start = time.monotonic()
    try:
        p = subprocess.run(args,cwd=cwd,env=env,capture_output=True,text=True,timeout=timeout)
        return {"status":"FINISHED","returncode":p.returncode,
                "duration_seconds":round(time.monotonic()-start,3),
                "stdout_tail":p.stdout[-9000:],"stderr_tail":p.stderr[-3400:]}
    except subprocess.TimeoutExpired as err:
        return {"status":"TIMEOUT","returncode":124,
                "duration_seconds":round(time.monotonic()-start,3),
                "error":str(err)[:300]}


def ollama_json(route: str) -> dict:
    with urlopen("http://127.0.0.1:11434"+route, timeout=8) as r:
        return json.load(r)


def experiment(out: dict):
    models = ollama_json("/api/tags").get("models", [])
    matched = [m for m in models if m.get("name")==MODEL]
    out["ollama_listed_model"] = [{"name":m.get("name"),"digest":m.get("digest"),
                                   "size":m.get("size")} for m in matched]
    if not matched:
        raise RuntimeError("local model was not downloaded")
    with tempfile.TemporaryDirectory(prefix="iwxa-real-goose-task-") as td:
        workspace = Path(td)/"workspace"
        workspace.mkdir()
        (workspace/"task_input.txt").write_text("alpha=7\nbeta=5\n",encoding="utf-8")
        env = dict(os.environ)
        env.update({
            "HOME":str(Path(td)/"home"),
            "XDG_CONFIG_HOME":str(Path(td)/"config"),
            "XDG_DATA_HOME":str(Path(td)/"data"),
            "XDG_CACHE_HOME":str(Path(td)/"cache"),
            "GOOSE_PROVIDER":"ollama", "GOOSE_MODEL":MODEL,
            "OLLAMA_HOST":"http://127.0.0.1:11434",
            "GOOSE_MODE":"auto",
            "GOOSE_TELEMETRY_OFF":"1","GOOSE_DISABLE_KEYRING":"1",
            "GOOSE_DISABLE_SESSION_NAMING":"true",
            "NO_PROXY":"127.0.0.1,localhost",
            "no_proxy":"127.0.0.1,localhost",
            "NO_COLOR":"1",
        })
        for name in ("home","config","data","cache"):
            (Path(td)/name).mkdir(parents=True,exist_ok=True)
        out["cli_version"] = run(["goose","--version"],env,workspace,10)
        if out["cli_version"]["returncode"]!=0 or "1.55.0" not in (
            out["cli_version"].get("stdout_tail","")+out["cli_version"].get("stderr_tail","")):
            raise RuntimeError("Goose version mismatch")
        prompt=(
            "This is a bounded offline file task. Working directory contains task_input.txt. "
            "Use your file-reading tool to read task_input.txt, add the two integer values, "
            "then use your file-writing tool to create result.txt in the current working "
            "directory. result.txt must contain exactly the integer 12 and a newline, "
            "no other text. Do not run shell commands or access the network. "
            "After the file exists, briefly say completed."
        )
        out["task"] = run(["goose","run","--no-session","--provider","ollama",
                           "--model",MODEL,"--max-turns","5",
                           "--output-format","json","-t",prompt],env,workspace,165)
        file = workspace/"result.txt"
        out["file_created"] = file.is_file()
        out["file_value"] = file.read_text(encoding="utf-8")[:100] if file.is_file() else None
        out["expected_file_value"]="12\n"
        out["verified_file_effect"]=out["file_value"]=="12\n"
        out["input_unchanged"]=(workspace/"task_input.txt").read_text(encoding="utf-8")=="alpha=7\nbeta=5\n"
        out["status"]="PASS" if (out["task"]["returncode"]==0 and out["verified_file_effect"]
                                  and out["input_unchanged"]) else "FAIL"


def main():
    r={"product":"Goose","goose_version":"1.55.0","ollama_model":MODEL,
       "runner":platform.platform(),"mode":"REAL_CPU_LOCAL_LLM_WITH_FILE_TOOL",
       "external_model_calls":0,"private_iwxa_adapter":"NOT_TESTED",
       "status":"FAIL"}
    try:
        experiment(r)
    except Exception as e:
        r["error"]=type(e).__name__+": "+str(e)[:600]
        r["traceback_tail"]=traceback.format_exc()[-2000:]
    REPORT.parent.mkdir(parents=True,exist_ok=True)
    REPORT.write_text(json.dumps(r,ensure_ascii=False,indent=2),encoding="utf-8")
    print("IWXA_GOOSE_REAL_MODEL",json.dumps(r,ensure_ascii=False))
    return 0 if r["status"]=="PASS" else 1

if __name__=="__main__":
    raise SystemExit(main())
