"""Windows GUI acceptance of real Tauri + native Obscura (no mocked invoke).

Requires interactive Windows desktop. Fails with explicit HOLD-like diagnostic if
the GitHub runner cannot expose a usable UI. No private product code.
"""
import ctypes
import hashlib
import http.server
import json
import os
import platform
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

if sys.platform!="win32":
    raise SystemExit("Windows native GUI only")

try:
    ctypes.windll.user32.SetProcessDPIAware()
except Exception:
    pass

import pyautogui
from PIL import Image

pyautogui.FAILSAFE=False
pyautogui.PAUSE=0.09

ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/"artifacts"/"browser-lab"/"windows-gui"
OUT.mkdir(parents=True,exist_ok=True)
EVENTS=[]
EVENT_LOCK=threading.Lock()

class Handler(http.server.SimpleHTTPRequestHandler):
    def __init__(self,*args,**kwargs):
        super().__init__(*args,directory=str(ROOT),**kwargs)
    def log_message(self,*args):pass
    def do_POST(self):
        if self.path!="/lab-event":self.send_error(404);return
        size=int(self.headers.get("Content-Length","0"))
        if size<1 or size>2048:self.send_error(413);return
        try:
            data=json.loads(self.rfile.read(size))
            event={k:str(data.get(k,""))[:128] for k in ("kind","tab","detail")}
            with EVENT_LOCK:EVENTS.append(event)
            self.send_response(204);self.end_headers()
        except (ValueError,UnicodeDecodeError):
            self.send_error(400)

def window_handle():
    user=ctypes.windll.user32
    result=[]
    callback=ctypes.WINFUNCTYPE(ctypes.c_bool,ctypes.c_void_p,ctypes.c_void_p)
    def visit(hwnd,arg):
        length=user.GetWindowTextLengthW(hwnd)
        buf=ctypes.create_unicode_buffer(length+1)
        user.GetWindowTextW(hwnd,buf,len(buf))
        if "IWXA Browser Preview" in buf.value and user.IsWindowVisible(hwnd):
            result.append(hwnd)
        return True
    callback_ref=callback(visit)
    user.EnumWindows(callback_ref,0)
    return result[-1] if result else None

def capture(name):
    filename=OUT/(name+".png")
    pyautogui.screenshot().save(filename)
    return filename

def wait_color(rgb,name,seconds=45):
    until=time.monotonic()+seconds
    while time.monotonic()<until:
        image=pyautogui.screenshot().convert("RGB")
        pts=[]
        for y in range(175,min(image.height,780),4):
            for x in range(60,min(image.width,455),4):
                v=image.getpixel((x,y))
                if all(abs(v[i]-rgb[i])<28 for i in range(3)):
                    pts.append((x,y))
        if len(pts)>65:
            capture(name)
            return sum(x for x,y in pts)//len(pts),sum(y for x,y in pts)//len(pts)
        time.sleep(.45)
    capture("not-found-"+name)
    raise AssertionError("Native screen lacks webpage color "+name)

def wait_event(kind,tab,detail,earlier,seconds=20):
    until=time.monotonic()+seconds
    while time.monotonic()<until:
        with EVENT_LOCK:
            events=EVENTS[earlier:]
            found=[e for e in events if e["kind"]==kind and e["tab"]==tab and e["detail"]==detail]
        if found:return found[-1]
        time.sleep(.15)
    raise AssertionError(f"No backend-observed {kind}/{tab}/{detail}; events={EVENTS}")

ADDRESS_ATTEMPTS=0
def type_url(url):
    global ADDRESS_ATTEMPTS
    ADDRESS_ATTEMPTS+=1
    # Windows WebView2 can consume Ctrl+L as a browser accelerator. Test the
    # ordinary pointer route first so we do not mistake the accelerator for
    # a navigation/engine regression.
    pyautogui.click(430,96)
    time.sleep(.28)
    pyautogui.hotkey("ctrl","a")
    pyautogui.write(url,interval=.025)
    capture("address-entered-"+str(ADDRESS_ATTEMPTS))
    pyautogui.press("enter")

def assert_unauthorized_cdp_rejected(app_pid):
    import psutil
    import websocket
    candidates=[]
    for child in psutil.Process(app_pid).children(recursive=True):
        try:
            argv=child.cmdline()
            if argv and Path(argv[0]).name.lower().startswith("obscura"):
                for conn in child.net_connections(kind="tcp"):
                    if conn.status==psutil.CONN_LISTEN and conn.laddr.ip=="127.0.0.1" and conn.laddr.port>0:
                        candidates.append("ws://127.0.0.1:"+str(conn.laddr.port)+"/")
        except psutil.Error:
            pass
    if len(candidates)!=1:raise AssertionError("Expected exactly one Obscura CDP owner: "+str(candidates))
    try:
        client=websocket.create_connection(candidates[0],timeout=4,suppress_origin=True)
    except websocket.WebSocketBadStatusException as error:
        if error.status_code!=401:raise AssertionError("Wrong CDP denial "+str(error.status_code))
        return {"unauthenticated_status":401,"accepted":False}
    except ConnectionResetError as error:
        # Windows loopback frequently resets a socket immediately after the
        # HTTP auth refusal. This proves only denial of the WebSocket upgrade,
        # not that the client read the server's 401 status line. Distinguish it.
        if getattr(error,"winerror",None)!=10054 and (not error.args or error.args[0]!=10054):
            raise
        return {"unauthenticated_status":"WINDOWS_RESET_10054","accepted":False}
    else:
        client.close()
        raise AssertionError("Unauthenticated CDP unexpectedly accepted")

def main():
    executable=Path(os.environ["OBSCURA_LAB_GUI_BINARY"]).resolve(strict=True)
    engine=Path(os.environ["OBSCURA_LAB_BINARY"]).resolve(strict=True)
    report={"kind":"REAL_WINDOWS_TAURI_OBSCURA_GUI",
        "platform":platform.platform(),"head":os.getenv("GITHUB_SHA"),
        "checks":{},"not_t2_acceptance":True}
    server=http.server.ThreadingHTTPServer(("127.0.0.1",0),Handler)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    origin=f"http://127.0.0.1:{server.server_port}"
    process=None
    with tempfile.TemporaryDirectory(prefix="obscura-windows-ui-") as folder:
        profile=Path(folder)/"profile"
        profile.mkdir()
        log=(OUT/"tauri-stdout.log").open("wb")
        try:
            env=dict(os.environ,OBSCURA_LAB_FIXTURE="1",OBSCURA_LAB_ALLOWED_ORIGINS=origin,
                OBSCURA_LAB_PROFILE_DIR=str(profile),
                OBSCURA_LAB_BINARY=str(engine),
                OBSCURA_LAB_SHA256=hashlib.sha256(engine.read_bytes()).hexdigest())
            start=time.monotonic()
            process=subprocess.Popen([str(executable)],env=env,cwd=executable.parent,
                stdout=log,stderr=subprocess.STDOUT)
            hwnd=None
            until=time.monotonic()+65
            while time.monotonic()<until:
                if process.poll() is not None:
                    raise RuntimeError(f"Tauri exited before window: {process.returncode}")
                hwnd=window_handle()
                if hwnd:break
                time.sleep(.35)
            if hwnd is None:raise RuntimeError("No interactive Tauri window found")
            user=ctypes.windll.user32
            user.ShowWindow(hwnd,9)
            user.SetWindowPos(hwnd,None,0,0,1200,800,0x0040)
            user.SetForegroundWindow(hwnd)
            # WebView2 is asynchronous: HWND creation is not React readiness.
            # The first captured HWND can be entirely white for several seconds.
            # Wait for two independent chrome regions from real rendered CSS
            # before touching the omnibox, otherwise early SendInput is lost.
            until_ready=time.monotonic()+55
            while time.monotonic()<until_ready:
                if process.poll() is not None:
                    raise RuntimeError("Tauri exited before rendering browser chrome")
                image=pyautogui.screenshot().convert("RGB")
                if image.width>850 and image.height>130:
                    tab=image.getpixel((750,50))
                    omnibox=image.getpixel((750,98))
                    if all(abs(tab[i]-(226,234,245)[i])<15 for i in range(3)) and all(
                        abs(omnibox[i]-(240,244,250)[i])<15 for i in range(3)):
                        break
                time.sleep(.35)
            else:
                capture("chrome-not-ready")
                raise AssertionError("WebView2 browser chrome never finished mounting")
            time.sleep(.75)
            capture("initial")
            report["checks"]["native_window"]="PASS"
            report["checks"]["chrome_ready"]="PASS"
            report["startup_window_ms"]=round((time.monotonic()-start)*1000)
            type_url(origin+"/browser-lab/fixtures/gui-smoke.html")
            red=wait_color((238,53,78),"first-tab",seconds=55)
            report["checks"]["navigation_render"]="PASS"
            report["checks"]["unauthenticated_cdp_refused"]=assert_unauthorized_cdp_rejected(process.pid)
            count=len(EVENTS)
            pyautogui.click(*red)
            wait_event("click","one","1",count)
            report["checks"]["pointer_readback"]="PASS"
            green=wait_color((40,184,93),"typed-field")
            count=len(EVENTS)
            pyautogui.click(*green)
            pyautogui.write("PUBLIC-WINDOWS",interval=.06)
            wait_event("typed","one","PUBLIC-WINDOWS",count)
            report["checks"]["text_readback"]="PASS"
            count=len(EVENTS)
            pyautogui.hotkey("ctrl","a")
            pyautogui.write("PUBLIC-EDIT",interval=.06)
            wait_event("typed","one","PUBLIC-EDIT",count)
            report["checks"]["native_field_select_all_and_replace"]="PASS"
            pyautogui.hotkey("ctrl","t")
            time.sleep(.7)
            type_url(origin+"/browser-lab/fixtures/gui-smoke.html?tab=two")
            blue=wait_color((33,106,202),"second-tab")
            count=len(EVENTS)
            pyautogui.click(*blue)
            wait_event("click","two","1",count)
            report["checks"]["two_tabs"]="PASS"
            count=len(EVENTS)
            pyautogui.hotkey("ctrl","alt","shift","e")
            blue=wait_color((33,106,202),"restarted-tab",seconds=70)
            pyautogui.click(*blue)
            wait_event("click","two","1",count,seconds=30)
            report["checks"]["owned_engine_restart"]="PASS"
            report["status"]="PASS_WINDOWS_GUI"
        except Exception as e:
            report["status"]="FAIL_WINDOWS_GUI"
            report["error"]=repr(e)
            try:capture("failure")
            except Exception as error:report["screenshot_error"]=str(error)
        finally:
            if process and process.poll() is None:
                process.terminate()
                try:process.wait(timeout=7)
                except subprocess.TimeoutExpired:
                    process.kill()
                    process.wait(timeout=7)
            log.close()
            server.shutdown()
    report["events"]=EVENTS
    (OUT/"report.json").write_text(json.dumps(report,indent=2),encoding="utf-8")
    print(json.dumps(report,indent=2))
    return 0 if report["status"]=="PASS_WINDOWS_GUI" else 1

if __name__=="__main__":
    sys.exit(main())
