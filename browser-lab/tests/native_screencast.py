"""Public real-engine CDP screencast experiment; no desktop acceptance claim.

Qualifies upstream activity-driven frames and acknowledgements before replacing
expensive timer-based Page.captureScreenshot calls in the desktop controller.
"""
import base64,hashlib,http.server,json,os,platform,socket,subprocess,sys,tempfile,threading,time
from pathlib import Path
import websocket
ROOT=Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/"comparison"))
from architecture_v026 import acquire_v026
OUT=ROOT/"artifacts"/"browser-lab"
OUT.mkdir(parents=True,exist_ok=True)

class Fixture(http.server.SimpleHTTPRequestHandler):
    def __init__(self,*a,**k):super().__init__(*a,directory=str(ROOT),**k)
    def log_message(self,*a):pass

def unused_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1",0))
        return s.getsockname()[1]

class CDP:
    def __init__(self,endpoint):
        self.ws=websocket.create_connection(endpoint,timeout=5,suppress_origin=True)
        self.next_id=0
        self.events=[]
        self.session=None
    def call(self,method,params=None,session=None):
        self.next_id+=1
        id=self.next_id
        command={"id":id,"method":method,"params":params or {}}
        if session is not None:command["sessionId"]=session
        self.ws.send(json.dumps(command))
        deadline=time.monotonic()+16
        while time.monotonic()<deadline:
            packet=json.loads(self.ws.recv())
            if packet.get("method")=="Page.screencastFrame":self.events.append(packet)
            if packet.get("id")==id:
                if "error" in packet:raise RuntimeError(f"{method}: {packet['error']}")
                return packet.get("result",{})
        raise TimeoutError(method)
    def receive_frame(self,timeout_s=12):
        deadline=time.monotonic()+timeout_s
        while time.monotonic()<deadline:
            if self.events:return self.events.pop(0)
            self.ws.settimeout(min(1,max(.1,deadline-time.monotonic())))
            try:msg=json.loads(self.ws.recv())
            except websocket.WebSocketTimeoutException:continue
            if msg.get("method")=="Page.screencastFrame":return msg
        raise TimeoutError("No activity-driven screencast frame")
    def close(self):self.ws.close()

def main():
    engine,identity=acquire_v026()
    result={"scope":"upstream Obscura activity-driven CDP screencast only",
            "sha":os.getenv("GITHUB_SHA"),"platform":platform.platform(),
            "engine":identity,"not_desktop_acceptance":True,"checks":{}}
    server=http.server.ThreadingHTTPServer(("127.0.0.1",0),Fixture)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    proc=None;client=None
    with tempfile.TemporaryDirectory(prefix="obscura-stream-") as temp:
        try:
            port=unused_port()
            with (OUT/"screencast.log").open("wb") as log:
                proc=subprocess.Popen([str(engine),"serve","--host","127.0.0.1",
                    "--port",str(port),"--max-connections","1","--storage-dir",
                    str(Path(temp)/"profile"),"--allow-private-network"],
                    cwd=engine.parent,stdout=log,stderr=subprocess.STDOUT)
                deadline=time.monotonic()+24
                while time.monotonic()<deadline:
                    if proc.poll() is not None:raise RuntimeError("Obscura exited")
                    try:
                        client=CDP(f"ws://127.0.0.1:{port}/")
                        break
                    except Exception:time.sleep(.2)
                if client is None:raise TimeoutError("CDP readiness")
                target=client.call("Target.createTarget",{"url":"about:blank"})["targetId"]
                session=client.call("Target.attachToTarget",{"targetId":target,"flatten":True})["sessionId"]
                client.session=session
                client.call("Page.enable",session=session)
                client.call("Runtime.enable",session=session)
                client.call("Emulation.setDeviceMetricsOverride",{
                    "width":800,"height":600,"deviceScaleFactor":1,"mobile":False},session=session)
                url=f"http://127.0.0.1:{server.server_port}/browser-lab/fixtures/gui-smoke.html"
                client.call("Page.navigate",{"url":url},session=session)
                response=client.call("Page.startScreencast",{
                    "format":"png","everyNthFrame":1,"maxWidth":800,"maxHeight":600},session=session)
                result["checks"]["start"]={"status":"PASS","response":response}
                f=client.receive_frame()
                png=base64.b64decode(f["params"]["data"],validate=True)
                if not png.startswith(b"\x89PNG\r\n\x1a\n"):raise AssertionError("Initial frame is not PNG")
                result["checks"]["initial_frame"]={"status":"PASS","bytes":len(png),
                    "sha256":hashlib.sha256(png).hexdigest(),"cdp_session":f.get("sessionId")==session}
                (OUT/"screencast-initial.png").write_bytes(png)
                first=f["params"]["sessionId"]
                client.call("Page.screencastFrameAck",{"sessionId":first},session=session)
                result["checks"]["ack"]={"status":"PASS"}
                client.call("Runtime.evaluate",{
                    "expression":"document.querySelector('#action').style.backgroundColor='rgb(10, 20, 230)'",
                    "returnByValue":True},session=session)
                second=client.receive_frame(timeout_s=12)
                png2=base64.b64decode(second["params"]["data"],validate=True)
                if not png2.startswith(b"\x89PNG\r\n\x1a\n"):raise AssertionError("Second frame not PNG")
                (OUT/"screencast-updated.png").write_bytes(png2)
                if hashlib.sha256(png).digest()==hashlib.sha256(png2).digest():
                    raise AssertionError("No visual change after DOM mutation")
                result["checks"]["activity_frame"]={"status":"PASS","bytes":len(png2),
                    "sha256":hashlib.sha256(png2).hexdigest()}
                client.call("Page.screencastFrameAck",
                    {"sessionId":second["params"]["sessionId"]},session=session)
                client.call("Page.stopScreencast",session=session)
                result["checks"]["stop"]={"status":"PASS"}
                result["status"]="PASS_UPSTREAM_SCREENCAST"
        except Exception as e:
            result["status"]="FAIL_UPSTREAM_SCREENCAST"
            result["error"]=repr(e)
        finally:
            if client:
                try:client.close()
                except Exception:pass
            if proc and proc.poll() is None:
                proc.terminate()
                try:proc.wait(timeout=5)
                except subprocess.TimeoutExpired:proc.kill();proc.wait(timeout=5)
            server.shutdown()
    (OUT/"screencast.json").write_text(json.dumps(result,indent=2),encoding="utf-8")
    print(json.dumps(result,indent=2))
    return 0 if result["status"]=="PASS_UPSTREAM_SCREENCAST" else 1
if __name__=="__main__":sys.exit(main())
