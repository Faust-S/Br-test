"""Real Obscura IndexedDB commit, abort and cross-process readback."""
import os,sys,json,platform,socket,subprocess,tempfile,time,threading,http.server
from pathlib import Path
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/"comparison"))
import run
OUT=ROOT/"artifacts"/"browser-lab"
OUT.mkdir(parents=True,exist_ok=True)
class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self,*a,**k):super().__init__(*a,directory=str(ROOT),**k)
    def log_message(self,*a):pass
def port():
    with socket.socket() as s:
        s.bind(("127.0.0.1",0))
        return s.getsockname()[1]
def session(binary,profile,url,phase):
    p=port()
    proc=client=None
    with (OUT/f"idb-{phase}.log").open("wb") as log:
        try:
            proc=subprocess.Popen([str(binary),"serve","--host","127.0.0.1",
                "--port",str(p),"--max-connections","1","--storage-dir",
                str(profile),"--allow-private-network"],cwd=binary.parent,
                stdout=log,stderr=subprocess.STDOUT)
            until=time.monotonic()+30
            while time.monotonic()<until:
                if proc.poll() is not None:raise RuntimeError("Obscura exited early")
                try:
                    client=run.CDP(f"ws://127.0.0.1:{p}/")
                    break
                except Exception:time.sleep(.25)
            if not client:raise TimeoutError("CDP not ready")
            client.visit(url)
            run.poll(client,"document.readyState",lambda x:x=="complete",20)
            action="startPersist" if phase=="write" else "startReadback"
            client.evaluate(f"(window.{action}(),true)")
            return run.poll(client,"window.__persist.phase === 'done' && window.__persist",
                lambda x:isinstance(x,dict) and x.get("phase")=="done",25)
        finally:
            if client:
                try:client.close()
                except Exception:pass
            if proc and proc.poll() is None:
                proc.kill() if phase=="write" else proc.terminate()
                try:proc.wait(timeout=8)
                except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=5)
def main():
    raw=os.environ.get("OBSCURA_PATCHED_BINARY")
    if not raw:raise RuntimeError("Set OBSCURA_PATCHED_BINARY to patched engine")
    binary=Path(raw).resolve(strict=True)
    report={"kind":"OBSCURA_REAL_IDB_PROCESS_RESTART","platform":platform.platform(),
        "source_sha":os.environ.get("GITHUB_SHA"),"not_t2_acceptance":True,"checks":{}}
    server=http.server.ThreadingHTTPServer(("127.0.0.1",0),Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    url=f"http://127.0.0.1:{server.server_port}/comparison/iwxa-ui-fixture.html"
    try:
        with tempfile.TemporaryDirectory(prefix="idb-public-") as folder:
            profile=Path(folder)/"profile"
            profile.mkdir()
            write=session(binary,profile,url,"write")
            report["checks"]["commit_and_abort"]=write
            if write.get("status")!="PASS":raise AssertionError(write)
            snapshot=profile/"indexedDB"/"snapshot-v1.json"
            if not snapshot.is_file():raise AssertionError("No durable engine snapshot")
            report["checks"]["snapshot_bytes"]=snapshot.stat().st_size
            for i in (1,2):
                value=session(binary,profile,url,f"restart-{i}")
                report["checks"][f"restart_{i}"]=value
                if value.get("status")!="PASS" or value.get("values")!=[7,7,7]:
                    raise AssertionError(value)
        report["status"]="PASS_REAL_PROCESS_RESTART"
    except Exception as e:
        report["status"]="FAIL_REAL_PROCESS_RESTART"
        report["error"]=repr(e)
    finally:server.shutdown()
    (OUT/"idb-restart.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps(report,indent=2))
    return 0 if report["status"]=="PASS_REAL_PROCESS_RESTART" else 1
if __name__=="__main__":sys.exit(main())
