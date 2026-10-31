import http.server
import json
from pathlib import Path
import shutil
import sys
import threading
import time

import servo_probe as base
import idb_probe as runner

OUT=base.OUT
LIVE={
    "artbazi": "https://artbazi.ru",
    "learn": "https://learn.iwxa.ru",
}
ROUTES=("/", "/workspace/")
REQUIRED_FIRST=("init","commit","readback","index_getall","index_cursor","transaction_abort","upgrade","index_scope")
REQUIRED_RESTART=("restart_read","restart_index_getall","restart_index_cursor")

def observe(server,sid,duration=60):
    until=time.monotonic()+duration
    latest=None
    while time.monotonic()<until:
        try:
            latest=base.execute(server,sid,"window.__advanced",timeout=7)
            if isinstance(latest,dict) and latest.get("phase") in ("done","error"):
                return latest
        except Exception as error:
            latest={"poll_error":str(error)}
        time.sleep(0.3)
    return {"phase":"timeout","last":latest}

def start(server,sid,phase):
    until=time.monotonic()+12
    ready=False
    while time.monotonic()<until:
        try:
            ready=base.execute(server,sid,'typeof window.startAdvanced === "function"',timeout=4)
            if ready: break
        except Exception:
            pass
        time.sleep(0.4)
    if not ready:
        return {"phase":"not-ready","detail":"advanced script unavailable"}
    base.execute(server,sid,f'window.startAdvanced("{phase}")',timeout=8)
    return observe(server,sid)

def live_routes(server,sid):
    results={}
    for name,origin in LIVE.items():
        results[name]={}
        for route in ROUTES:
            url=origin+route
            key="home" if route=="/" else "workspace"
            try:
                base.visit(server,sid,url)
                time.sleep(1.8)
                info=base.execute(server,sid, """({
                    url:location.href,
                    title:document.title,
                    h1:document.querySelector('h1')?.textContent?.trim() ?? null,
                    textLength:document.body?.innerText?.length ?? 0,
                    scriptCount:document.scripts.length,
                    hasIndexedDB:!!window.indexedDB,
                    hasWorkspaceText:!!document.body && /workspace|рабочая область|пространство|пространств/i.test(document.body.innerText)
                })""",timeout=10)
                good=isinstance(info,dict) and info.get("textLength",0)>100
                results[name][key]={"status":"HTML_PRESENT" if good else "HOLD","detail":info}
                base.capture(server,sid,f"real-{name}-{key}")
            except Exception as error:
                results[name][key]={"status":"HOLD","error":str(error)}
    return results

def one_case(binary,url,mode):
    label="advanced-"+mode
    profile=OUT/"profiles"/label
    if profile.exists():
        shutil.rmtree(profile)
    state={"mode":mode}
    p,f,server,sid=runner.open_session(binary,url,profile,mode,label+"-first")
    try:
        state["first"]=start(server,sid,"first")
        state["public_routes"]=live_routes(server,sid)
    finally:
        runner.close_session(p,f,server,sid)
    p,f,server,sid=runner.open_session(binary,url,profile,mode,label+"-restart")
    try:
        state["restart"]=start(server,sid,"restart")
    finally:
        runner.close_session(p,f,server,sid)
    a=state.get("first",{}).get("tests",{})
    b=state.get("restart",{}).get("tests",{})
    state["feature_status"]={k:a.get(k,{"status":"NOT_REACHED"}) for k in REQUIRED_FIRST}
    state["recovery_status"]={k:b.get(k,{"status":"NOT_REACHED"}) for k in REQUIRED_RESTART}
    state["passed"]=all(x.get("status")=="PASS" for x in state["feature_status"].values()) and all(x.get("status")=="PASS" for x in state["recovery_status"].values())
    return state

def main():
    OUT.mkdir(exist_ok=True)
    result={"servo":"v0.7.0","platform":sys.platform,"status":"HOLD","cases":[]}
    local=None
    try:
        binary,result["binary"]=base.acquire()
        local=http.server.ThreadingHTTPServer(("127.0.0.1",0),base.QuietServer)
        threading.Thread(target=local.serve_forever,daemon=True).start()
        url=f"http://127.0.0.1:{local.server_port}/storage.html"
        modes=["idb-only","experimental"]
        for mode in modes:
            try:result["cases"].append(one_case(binary,url,mode))
            except Exception as error:result["cases"].append({"mode":mode,"passed":False,"runner_error":str(error)})
        result["status"]="PASS" if all(case.get("passed") for case in result["cases"]) else "HOLD"
    except Exception as error:
        result["status"]="INFRA_FAILURE"
        result["error"]=str(error)
    finally:
        if local: local.shutdown()
        (OUT/"advanced-results.json").write_text(json.dumps(result,ensure_ascii=False,indent=2,default=str),encoding="utf-8")
        print(json.dumps({"status":result["status"],"platform":result["platform"],"cases":[{
            "mode":case.get("mode"),
            "passed":case.get("passed"),
            "feature_status":case.get("feature_status"),
            "recovery_status":case.get("recovery_status"),
            "public_routes":case.get("public_routes"),
            "runner_error":case.get("runner_error")
        } for case in result["cases"]]},ensure_ascii=False,indent=2))
    return 0 if result["status"]=="PASS" else 1

if __name__=="__main__":sys.exit(main())
