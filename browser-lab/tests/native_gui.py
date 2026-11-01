"""End-to-end public native Tauri -> Obscura GUI test; no proprietary code."""
import contextlib,hashlib,http.server,json,os,platform,subprocess,sys,tempfile,threading,time
from pathlib import Path
from PIL import Image
ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/"artifacts"/"browser-lab"/"gui"
OUT.mkdir(parents=True,exist_ok=True)
EVENTS=[]
class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self,*args,**kwargs):super().__init__(*args,directory=str(ROOT),**kwargs)
    def log_message(self,*args):pass
    def do_POST(self):
        if self.path!="/lab-event":self.send_error(404);return
        n=int(self.headers.get("Content-Length","0"))
        if not 0<n<2048:self.send_error(413);return
        try:
            data=json.loads(self.rfile.read(n))
            EVENTS.append({k:str(data.get(k,""))[:128] for k in ("kind","detail","tab")})
            self.send_response(204);self.end_headers()
        except ValueError:self.send_error(400)
def cmd(*args,check=True):
    return subprocess.run(args,check=check,text=True,capture_output=True,timeout=20)
def key(chord):cmd("xdotool","key","--clearmodifiers",chord)
def type_text(s):cmd("xdotool","type","--clearmodifiers","--delay","17",s)
def click(p):cmd("xdotool","mousemove","--sync",str(p[0]),str(p[1]));cmd("xdotool","click","1")
def shot(name):
    path=OUT/(name+".png");cmd("import","-window","root",str(path));return path
def find_color(rgb,name,seconds=50):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        shot("current")
        im=Image.open(OUT/"current.png").convert("RGB")
        pts=[]
        for y in range(100,min(im.height,810),3):
            for x in range(10,min(im.width,1200),3):
                c=im.getpixel((x,y))
                if all(abs(c[i]-rgb[i])<=23 for i in range(3)):pts.append((x,y))
        if len(pts)>100:
            p=(sum(x for x,_ in pts)//len(pts),sum(y for _,y in pts)//len(pts))
            shot(name)
            return p
        time.sleep(.5)
    raise AssertionError("No native rendered color "+name+" "+str(rgb))
def await_event(kind,tab,detail,since,seconds=20):
    deadline=time.monotonic()+seconds
    while time.monotonic()<deadline:
        hits=[e for e in EVENTS[since:] if e["kind"]==kind and e["tab"]==tab and e["detail"]==detail]
        if hits:return hits[-1]
        time.sleep(.15)
    raise AssertionError(f"Missing real HTTP interaction {kind}/{tab}/{detail}: {EVENTS[-8:]}")
def main():
    binary=Path(os.environ["OBSCURA_LAB_BINARY"]).resolve(strict=True)
    appbinary=Path(os.environ["OBSCURA_LAB_GUI_BINARY"]).resolve(strict=True)
    report={"kind":"NATIVE_TAURI_OBSCURA_UI","platform":platform.platform(),
       "head":os.getenv("GITHUB_SHA"),"checks":{},"not_t2_acceptance":True}
    server=http.server.ThreadingHTTPServer(("127.0.0.1",0),Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    origin=f"http://127.0.0.1:{server.server_port}"
    with tempfile.TemporaryDirectory(prefix="public-gui-") as tmp, (OUT/"native-app.log").open("wb") as log:
        profile=Path(tmp)/"profile";profile.mkdir()
        procs=[]
        try:
            procs.append(subprocess.Popen(["openbox"],stdout=log,stderr=subprocess.STDOUT))
            env=dict(os.environ,OBSCURA_LAB_SHA256=hashlib.sha256(binary.read_bytes()).hexdigest(),
               OBSCURA_LAB_FIXTURE="1",OBSCURA_LAB_ALLOWED_ORIGINS=origin,
               OBSCURA_LAB_PROFILE_DIR=str(profile),WEBKIT_DISABLE_COMPOSITING_MODE="1")
            procs.append(subprocess.Popen([str(appbinary)],cwd=str(appbinary.parent),env=env,stdout=log,stderr=subprocess.STDOUT))
            until=time.monotonic()+80;wid=None
            while time.monotonic()<until:
                if procs[-1].poll() is not None:raise RuntimeError("Tauri exited "+str(procs[-1].returncode))
                result=cmd("xdotool","search","--onlyvisible","--name","Obscura Browser Lab",check=False)
                if result.returncode==0 and result.stdout.strip():wid=result.stdout.splitlines()[-1];break
                time.sleep(.4)
            if wid is None:raise AssertionError("Tauri window never appeared")
            cmd("xdotool","windowmove",wid,"0","0")
            cmd("xdotool","windowsize",wid,"1250","850")
            cmd("xdotool","windowactivate","--sync",wid)
            shot("initial");report["checks"]["native_window"]="PASS"
            key("ctrl+l");type_text(origin+"/browser-lab/fixtures/gui-smoke.html");key("Return")
            red=find_color((238,53,78),"first-tab")
            report["checks"]["real_obscura_page_render"]="PASS"
            n=len(EVENTS);click(red);await_event("click","one","1",n)
            report["checks"]["real_mouse_click"]="PASS"
            green=find_color((40,184,93),"text-input")
            n=len(EVENTS);click(green);type_text("PUBLIC-GUI")
            await_event("typed","one","PUBLIC-GUI",n)
            report["checks"]["real_keyboard_input"]="PASS"
            key("ctrl+t");time.sleep(.6);key("ctrl+l")
            type_text(origin+"/browser-lab/fixtures/gui-smoke.html?tab=two");key("Return")
            blue=find_color((33,106,202),"second-tab")
            n=len(EVENTS);click(blue);await_event("click","two","1",n)
            report["checks"]["native_second_tab"]="PASS"
            n=len(EVENTS);key("ctrl+alt+shift+e")
            blue=find_color((33,106,202),"after-engine-restart",seconds=65)
            click(blue);await_event("click","two","1",n)
            report["checks"]["owned_engine_restart_with_tab_readback"]="PASS"
            report["status"]="PASS_REAL_GUI"
        except Exception as ex:
            report["status"]="FAIL_REAL_GUI";report["error"]=repr(ex)
            with contextlib.suppress(Exception):
                png=shot("failure")
                from collections import Counter
                im=Image.open(png).convert("RGB")
                report["screen_most_common_rgb"]=Counter(im.resize((128,90)).getdata()).most_common(12)
            report["processes_alive"]=[p.poll() is None for p in procs]
        finally:
            for p in reversed(procs):
                if p.poll() is None:
                    p.terminate()
                    try:p.wait(timeout=6)
                    except subprocess.TimeoutExpired:p.kill();p.wait(timeout=5)
            server.shutdown()
    report["events"]=EVENTS
    (OUT/"gui-report.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps(report,indent=2))
    return 0 if report["status"]=="PASS_REAL_GUI" else 1
if __name__=="__main__":sys.exit(main())
