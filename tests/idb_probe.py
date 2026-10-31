import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import threading
import time
import http.server

import servo_probe as base

OUT = base.OUT
STABLE_SITES = (
    ("artbazi", "https://artbazi.ru/"),
    ("learn", "https://learn.iwxa.ru/"),
)

def launch(binary, url, wdport, profile, mode, path):
    profile.mkdir(parents=True, exist_ok=True)
    flags = ["--enable-experimental-web-platform-features"] if mode == "experimental" else ["--pref", "dom_indexeddb_enabled=true"]
    args = [str(binary), "--headless", "--webdriver", str(wdport), "--config-dir", str(profile), *flags, url]
    f = path.open("wb")
    p = subprocess.Popen(args, cwd=binary.parent, stdout=f, stderr=subprocess.STDOUT)
    return p, f

def wait_result(server, sid, limit=19):
    deadline = time.monotonic() + limit
    last = None
    while time.monotonic() < deadline:
        try:
            last = base.execute(server,sid,"window.__idb",timeout=5)
            if isinstance(last, dict) and last.get("phase") in ("done","error"):
                return last
        except Exception as error:
            last = {"poll_error":str(error)}
        time.sleep(0.35)
    return {"phase":"host-timeout", "last":last}

def open_session(binary, url, profile, mode, label):
    wdport = base.port()
    proc, log = launch(binary,url,wdport,profile,mode,OUT / (label + ".log"))
    server = f"http://127.0.0.1:{wdport}"
    try:
        base.wait_webdriver(server,proc,25)
        sid = base.try_session(server)
        base.visit(server,sid,url)
        return proc, log, server, sid
    except:
        base.shutdown(proc,log)
        raise

def close_session(proc,log,server,sid):
    try:
        try:
            base.wd(server,f"/session/{sid}",method="DELETE",timeout=5)
        except Exception:
            pass
    finally:
        base.shutdown(proc,log)

def step(server,sid,mode):
    response = base.execute(server,sid,f'window.startIDB("{mode}")',timeout=8)
    if response != "started":
        return {"phase":"failed-to-start","returned":response}
    return wait_result(server,sid)

def sites(server,sid):
    values = {}
    for name, origin in STABLE_SITES:
        try:
            base.visit(server,sid,origin)
            time.sleep(1)
            info = base.execute(server,sid,"({url:location.href,title:document.title,h1:document.querySelector('h1')?.textContent?.trim() ?? null,bodyLength:document.body?.innerText?.length ?? 0})",timeout=10)
            values[name] = {"status":"HTML_PRESENT" if isinstance(info,dict) and info.get("bodyLength",0)>100 else "NOT_CONFIRMED", "snapshot":info}
            base.capture(server,sid,"stable-"+name)
        except Exception as err:
            values[name] = {"status":"HOLD","error":str(err)}
    return values

def trial(binary, url, mode, number, show_sites=False):
    name=f"{mode}-{number}"
    profile=OUT/"profiles"/name
    if profile.exists():
        shutil.rmtree(profile)
    output={"mode":mode,"trial":number,"profile_isolated":True,"close_style":"WebDriver session delete then process terminate"}
    p,f,server,sid=open_session(binary,url,profile,mode,name+"-first")
    try:
        output["write"] = step(server,sid,"write")
        base.visit(server,sid,url)
        output["reload"] = step(server,sid,"read")
        if show_sites:
            output["stable_sites"] = sites(server,sid)
    finally:
        close_session(p,f,server,sid)
    p2,f2,server2,sid2=open_session(binary,url,profile,mode,name+"-restart")
    try:
        output["restart"] = step(server2,sid2,"read")
    finally:
        close_session(p2,f2,server2,sid2)
    output["pass"] = all(output.get(stage,{}).get("phase")=="done" for stage in ("write","reload","restart"))
    return output

def mixed_trial(binary, origin, mode, trial_num):
    result={"mode": mode, "case":"mixed-api", "trial": trial_num}
    profile=OUT/"profiles"/f"mixed-{mode}-{trial_num}"
    if profile.exists():
        shutil.rmtree(profile)
    for iteration in range(2):
        p,f,server,sid=open_session(binary,origin,profile,mode,f"mixed-{mode}-{trial_num}-{iteration}")
        try:
            base.execute(server,sid,"window.startIWXAProbe()",timeout=10)
            result[f"pass_{iteration}"]=base.await_probe(server,sid,"__iwxaProbe",duration=42)
        finally:
            close_session(p,f,server,sid)
    phases=[result[f"pass_{i}"].get("phase") for i in range(2)]
    result["pass"]=phases==["done","done"] and all(
        result[f"pass_{i}"].get("data",{}).get("indexeddb",{}).get("status")=="PASS"
        for i in range(2)
    )
    return result

def main():
    OUT.mkdir(exist_ok=True)
    data = {"servo":"v0.7.0","platform":sys.platform,"status":"INCOMPLETE","trials":[]}
    server=None
    try:
        binary,data["release"] = base.acquire()
        server=http.server.ThreadingHTTPServer(("127.0.0.1",0),base.QuietServer)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        url=f"http://127.0.0.1:{server.server_port}/idb.html"
        if sys.platform=="win32":
            experiments=[("idb-only",1),("idb-only",2),("experimental",1),("experimental",2)]
        else:
            experiments=[("idb-only",1),("experimental",1)]
        for mode,number in experiments:
            try:
                data["trials"].append(trial(binary,url,mode,number,show_sites=(mode=="idb-only" and number==1)))
            except Exception as exc:
                data["trials"].append({"mode":mode,"trial":number,"pass":False,"runner_error":str(exc)})
        if sys.platform=="win32":
            mixed_url=f"http://127.0.0.1:{server.server_port}/fixture.html"
            for attempt in range(2):
                try:
                    data["trials"].append(mixed_trial(binary,mixed_url,"experimental",attempt))
                except Exception as exc:
                    data["trials"].append({"mode":"experimental","case":"mixed-api","trial":attempt,"pass":False,"runner_error":str(exc)})
        data["status"]="PASS" if all(t["pass"] for t in data["trials"]) else "HOLD"
    except Exception as exc:
        data["status"]="INFRA_FAILURE"
        data["error"]=str(exc)
    finally:
        if server:
            server.shutdown()
        (OUT/"idb-results.json").write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding="utf-8")
        print(json.dumps(data,ensure_ascii=False,indent=2))
    return 0 if data["status"]=="PASS" else 1

if __name__=="__main__":
    sys.exit(main())
