"""Integrated native Tauri GUI + bundled patched Obscura durability acceptance.

One actual visible GUI; no mock CDP. Write via a click in the Obscura viewport,
terminate the exact owned process tree, relaunch the Tauri GUI with the same
engine profile, and read three IndexedDB stores through another real click.
"""
import contextlib, hashlib, http.server, json, os, platform, subprocess, sys, tempfile, threading, time
from pathlib import Path
import psutil
from PIL import Image

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/"artifacts"/"browser-lab"/"patched-gui"
OUT.mkdir(parents=True,exist_ok=True)
WINDOWS=sys.platform=="win32"
EVENTS=[]
if WINDOWS:
    import ctypes,pyautogui
    ctypes.windll.user32.SetProcessDPIAware()
    pyautogui.FAILSAFE=False
else:
    pass

class Server(http.server.SimpleHTTPRequestHandler):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,directory=str(ROOT),**kwargs)
    def log_message(self,*args):pass
    def do_POST(self):
        if self.path!="/lab-event":self.send_error(404);return
        size=int(self.headers.get("Content-Length","0"))
        if not 0<size<2048:self.send_error(413);return
        try:
            item=json.loads(self.rfile.read(size))
            if not isinstance(item,dict):raise ValueError("invalid event")
            EVENTS.append(item)
            self.send_response(204);self.end_headers()
        except (ValueError,UnicodeError):self.send_error(400)

def command(*args):
    return subprocess.run(args,check=True,timeout=15,text=True,capture_output=True)

def screenshot(name):
    path=OUT/(name+".png")
    if WINDOWS:pyautogui.screenshot().save(path)
    else:command("import","-window","root",str(path))
    return Image.open(path).convert("RGB")

def key(chord):
    if WINDOWS:pyautogui.hotkey(*chord.split("+"))
    else:command("xdotool","key","--clearmodifiers",chord)

def text(value):
    if WINDOWS:pyautogui.write(value,interval=.012)
    else:command("xdotool","type","--clearmodifiers","--delay","15",value)

def click(x,y):
    if WINDOWS:pyautogui.click(x,y)
    else:
        command("xdotool","mousemove","--sync",str(x),str(y))
        command("xdotool","click","1")

def locate(rgb,tag,seconds=50):
    until=time.monotonic()+seconds
    while time.monotonic()<until:
        image=screenshot("poll")
        pixels=[]
        for y in range(175,min(740,image.height),4):
            for x in range(70,min(520,image.width),4):
                r=image.getpixel((x,y))
                if all(abs(r[i]-rgb[i])<24 for i in range(3)):pixels.append((x,y))
        if len(pixels)>100:
            screenshot(tag)
            return sum(x for x,_ in pixels)//len(pixels),sum(y for _,y in pixels)//len(pixels)
        time.sleep(.4)
    screenshot("failed-"+tag)
    raise AssertionError("Missing real webpage marker "+tag)

def wait_event(kind,seconds=40):
    until=time.monotonic()+seconds
    while time.monotonic()<until:
        found=[e for e in EVENTS if e.get("kind")==kind]
        if found:return found[-1]
        time.sleep(.15)
    raise AssertionError(f"No IndexedDB HTTP event {kind}; observed={EVENTS}")

def get_window(proc):
    until=time.monotonic()+65
    while time.monotonic()<until:
        if proc.poll() is not None:raise RuntimeError(f"Tauri exited early: {proc.returncode}")
        if WINDOWS:
            u=ctypes.windll.user32
            windows=[]
            CB=ctypes.WINFUNCTYPE(ctypes.c_bool,ctypes.c_void_p,ctypes.c_void_p)
            def record(hwnd,param):
                name=ctypes.create_unicode_buffer(300)
                u.GetWindowTextW(hwnd,name,300)
                if "IWXA Browser Preview" in name.value and u.IsWindowVisible(hwnd):windows.append(hwnd)
                return True
            u.EnumWindows(CB(record),0)
            if windows:
                hwnd=windows[-1]
                u.ShowWindow(hwnd,9)
                u.SetWindowPos(hwnd,None,0,0,1200,800,0x0040)
                u.SetForegroundWindow(hwnd)
                return hwnd
        else:
            found=subprocess.run(["xdotool","search","--onlyvisible","--name","IWXA Browser Preview"],
                capture_output=True,text=True)
            if found.returncode==0 and found.stdout.strip():
                wid=found.stdout.splitlines()[-1]
                command("xdotool","windowmove",wid,"0","0")
                command("xdotool","windowsize",wid,"1250","850")
                command("xdotool","windowactivate","--sync",wid)
                return wid
        time.sleep(.3)
    raise AssertionError("Native Tauri window not visible")

def wait_chrome(proc):
    until=time.monotonic()+55
    while time.monotonic()<until:
        if proc.poll() is not None:raise RuntimeError("Browser exited before frontend ready")
        im=screenshot("chrome-poll")
        if im.width>900 and im.height>500:
            bar=im.getpixel((750,95))
            if all(abs(bar[i]-[240,244,250][i])<19 for i in range(3)):
                time.sleep(.7)
                return
        time.sleep(.3)
    raise AssertionError("Native React chrome not mounted")

def stop_tree(proc):
    if proc.poll() is None:
        parent=psutil.Process(proc.pid)
        children=parent.children(recursive=True)
        for child in children:
            with contextlib.suppress(psutil.Error):child.terminate()
        with contextlib.suppress(psutil.Error):parent.terminate()
        gone,alive=psutil.wait_procs(children+[parent],timeout=7)
        for p in alive:
            with contextlib.suppress(psutil.Error):p.kill()
        proc.wait(timeout=9)

def main():
    app=Path(os.environ["OBSCURA_LAB_GUI_BINARY"]).resolve(strict=True)
    engine=Path(os.environ["OBSCURA_PATCHED_BINARY"]).resolve(strict=True)
    expected=hashlib.sha256(engine.read_bytes()).hexdigest()
    report={"scope":"actual Tauri plus resource-bundled patched Obscura IndexedDB",
        "sha":os.getenv("GITHUB_SHA"),"platform":platform.platform(),
        "engine_sha256":expected,"checks":{},"not_t2_acceptance":True}
    server=http.server.ThreadingHTTPServer(("127.0.0.1",0),Server)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    origin=f"http://127.0.0.1:{server.server_port}"
    managers=[]
    if not WINDOWS:managers.append(subprocess.Popen(["openbox"],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL))
    with tempfile.TemporaryDirectory(prefix="iwxa-public-bundle-") as temp, (OUT/"app.log").open("wb") as log:
        profile=Path(temp)/"profile";profile.mkdir()
        try:
            env=dict(os.environ,OBSCURA_LAB_FIXTURE="1",
                OBSCURA_LAB_ALLOWED_ORIGINS=origin,OBSCURA_LAB_PROFILE_DIR=str(profile))
            env.pop("OBSCURA_LAB_BINARY",None)
            env.pop("OBSCURA_LAB_SHA256",None)
            if not WINDOWS:env["WEBKIT_DISABLE_COMPOSITING_MODE"]="1"
            # Two distinct Tauri instances, same engine profile. A launch
            # succeeds only by locating Obscura in Tauri resource_dir.
            for phase in ("write","readback"):
                start=time.monotonic()
                process=subprocess.Popen([str(app)],env=env,cwd=app.parent,
                    stdout=log,stderr=subprocess.STDOUT)
                try:
                    get_window(process)
                    wait_chrome(process)
                    key("ctrl+l")
                    if WINDOWS:
                        click(430,96);key("ctrl+a")
                    text(origin+"/browser-lab/fixtures/idb-crash-recovery.html")
                    key("Return")
                    point=locate((238,53,78) if phase=="write" else (33,106,202),
                        phase+"-viewport")
                    click(*point)
                    event=wait_event("idb_write" if phase=="write" else "idb_read")
                    if event.get("detail")!="PASS" or event.get("values")!=[7,7,7]:
                        raise AssertionError("IndexedDB GUI event failed "+repr(event))
                    report["checks"][phase]={"status":"PASS","event":event,
                        "startup_to_action_ms":round(1000*(time.monotonic()-start))}
                finally:
                    stop_tree(process)
            snapshot=profile/"indexedDB"/"snapshot-v1.json"
            if not snapshot.is_file():raise AssertionError("No durable engine IndexedDB file")
            report["checks"]["native_profile_snapshot_bytes"]=snapshot.stat().st_size
            report["status"]="PASS_BUNDLED_IDB_GUI_RESTART"
        except Exception as exc:
            report["status"]="FAIL_BUNDLED_IDB_GUI_RESTART"
            report["error"]=repr(exc)
            with contextlib.suppress(Exception):screenshot("failure")
        finally:
            for p in managers:
                if p.poll() is None:
                    p.terminate()
                    with contextlib.suppress(Exception):p.wait(timeout=5)
            server.shutdown()
    report["events"]=EVENTS
    (OUT/"report.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps(report,indent=2))
    return 0 if report["status"]=="PASS_BUNDLED_IDB_GUI_RESTART" else 1
if __name__=="__main__":sys.exit(main())
