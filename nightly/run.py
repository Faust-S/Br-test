import hashlib
import http.server
import json
import os
from pathlib import Path
import re
import shutil
import sys
import threading
import time
import urllib.request
import zipfile

TESTS = Path(__file__).resolve().parent.parent / "tests"
sys.path.insert(0, str(TESTS))
import servo_probe as base
import advanced_probe as advanced

URL = "https://download.servo.org/nightly/windows-msvc/servo-x86_64-windows-msvc.zip"
OUT = base.OUT
def acquire_nightly():
    OUT.mkdir(exist_ok=True)
    sha_url=URL+".sha256"
    req=urllib.request.Request(sha_url, headers={"User-Agent":"IWXA-Browser-Servo-Compat/1.0"})
    with urllib.request.urlopen(req, timeout=40) as response:
        checksum_text=response.read().decode("utf-8").strip()
    match=re.search(r"\b[a-fA-F0-9]{64}\b",checksum_text)
    if not match:
        raise RuntimeError("Unexpected nightly checksum format: "+checksum_text[:240])
    expected=match.group(0).lower()
    archive=OUT/"servo-nightly.zip"
    req=urllib.request.Request(URL, headers={"User-Agent":"IWXA-Browser-Servo-Compat/1.0"})
    hash_=hashlib.sha256()
    with urllib.request.urlopen(req, timeout=180) as response, archive.open("wb") as f:
        for chunk in iter(lambda:response.read(1024*1024),b""):
            f.write(chunk)
            hash_.update(chunk)
    observed=hash_.hexdigest()
    if observed!=expected:
        raise RuntimeError("Nightly archive changed or corrupt; expected "+expected+" got "+observed)
    dest=OUT/"servo-nightly"
    dest.mkdir(exist_ok=True)
    with zipfile.ZipFile(archive) as z:
        z.extractall(dest)
    bins=[p for p in dest.rglob("*.exe") if p.name.lower() in ("servo.exe","servoshell.exe")]
    if not bins:
        raise RuntimeError("Servo binary missing: "+str([str(p) for p in list(dest.rglob("*"))[:35]]))
    binary=min(bins,key=lambda p:len(p.parts))
    info={"origin":URL,"sha256":observed,"downloaded_utc":time.strftime("%Y-%m-%dT%H:%M:%SZ",time.gmtime()),"executable":str(binary)}
    (OUT/"nightly-manifest.json").write_text(json.dumps(info,indent=2),encoding="utf-8")
    return binary,info

def main():
    OUT.mkdir(exist_ok=True)
    report={"engine":"servo-nightly","platform":sys.platform,"cases":[],"status":"INCOMPLETE"}
    server=None
    try:
        binary,report["build"]=acquire_nightly()
        server=http.server.ThreadingHTTPServer(("127.0.0.1",0),base.QuietServer)
        threading.Thread(target=server.serve_forever,daemon=True).start()
        url=f"http://127.0.0.1:{server.server_port}/storage.html"
        for mode in ("idb-only","experimental"):
            try:
                case=advanced.one_case(binary,url,mode)
                report["cases"].append(case)
            except Exception as e:
                report["cases"].append({"mode":mode,"passed":False,"runner_error":repr(e)})
        report["status"]="PASS" if all(c.get("passed") for c in report["cases"]) else "HOLD"
    except Exception as e:
        report["status"]="INFRA_FAILURE"
        report["error"]=repr(e)
    finally:
        if server:
            server.shutdown()
        (OUT/"nightly-results.json").write_text(json.dumps(report,ensure_ascii=False,indent=2),encoding="utf-8")
        print(json.dumps({"engine":report["engine"],"status":report["status"],"build":report.get("build"),"cases":[{
            "mode":c.get("mode"),"passed":c.get("passed"),
            "feature_status":c.get("feature_status"),"recovery_status":c.get("recovery_status"),
            "public_routes":c.get("public_routes"),"runner_error":c.get("runner_error")
        } for c in report.get("cases",[])],"error":report.get("error")},ensure_ascii=False,indent=2))
    return 0 if report["status"]=="PASS" else 1

if __name__=="__main__":
    sys.exit(main())
