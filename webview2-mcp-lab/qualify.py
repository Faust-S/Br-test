"""Native Windows + original Google MCP qualification. No direct browser automation SDK."""
import base64
import hashlib
import ctypes
from ctypes import wintypes as W
import http.server
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from snapshot_refs import uid
from stdio_transport import StdioMcpTransport
from public_sites import SITES, page_catalog, validate_public_page, validate_public_snapshot
from tab_identity import stable_tab_id, bind_bootstrap, rebind_pages, attach_tab, detach_tab
from advanced_fixture import DOWNLOAD_BYTES, UPLOAD_BYTES, handle_get, handle_post

ROOT = Path(__file__).resolve().parent
OUT = ROOT / "artifacts"
OUT.mkdir(exist_ok=True)
EVENTS = []
LOCK = threading.Lock()
CHECKS = []
REPORT = {"status": "FAIL", "checks": CHECKS, "mcp_version": "1.14.0", "tauri_version": "2.12.7"}

def check(name, evidence=True):
    CHECKS.append({"name": name, "evidence": evidence})
    print("PASS", name, flush=True)

def wait_for(description, fn, timeout=30):
    deadline = time.monotonic() + timeout
    last = None
    while time.monotonic() < deadline:
        try:
            value = fn()
            if value:
                return value
        except (OSError, ValueError, RuntimeError) as exc:
            last = repr(exc)
        time.sleep(.25)
    raise RuntimeError(f"timeout: {description}; last={last}")

def record_fixture(event):
    with LOCK:
        EVENTS.append(event)

class Fixture(http.server.BaseHTTPRequestHandler):
    def log_message(self, *args): pass
    def do_GET(self):
        if handle_get(self, record_fixture): return
        if self.path in {"/form","/tab-a","/tab-b"} or re.fullmatch(r"/tab-new/[0-9]+", self.path):
            data = (ROOT / "fixture.html").read_bytes()
        elif self.path == "/second":
            data = b"<!doctype html><title>Second fixture</title><h1>Second fixture page</h1>"
        else:
            self.send_error(404); return
        self.send_response(200)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)
    def do_POST(self):
        if handle_post(self, record_fixture): return
        size = int(self.headers.get("Content-Length", "0"))
        if self.path != "/event" or size < 1 or size > 8192:
            self.send_error(400); return
        event = json.loads(self.rfile.read(size))
        record_fixture(event)
        self.send_response(204); self.end_headers()

SHELL_EVENTS=[]

class TrustedShell(http.server.BaseHTTPRequestHandler):
    def log_message(self,*args): pass
    def do_GET(self):
        allowed={"/desktop/index.html":("index.html","text/html; charset=utf-8"),
                 "/desktop/shell.css":("shell.css","text/css; charset=utf-8"),
                 "/desktop/shell.js":("shell.js","text/javascript; charset=utf-8"),
                 "/desktop/probe.js":("probe.js","text/javascript; charset=utf-8")}
        item=allowed.get(self.path)
        if not item: self.send_error(404);return
        name,mime=item
        with LOCK:SHELL_EVENTS.append({"stage":"http_get","message":self.path})
        data=(ROOT/"ui"/name).read_bytes()
        self.send_response(200)
        self.send_header("Content-Type",mime)
        self.send_header("Content-Security-Policy","default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; connect-src 'self' ipc: http://ipc.localhost https://ipc.localhost; object-src 'none'; frame-src 'none'; base-uri 'none'")
        self.send_header("Cache-Control","no-store")
        self.send_header("Content-Length",str(len(data)));self.end_headers();self.wfile.write(data)
    def do_POST(self):
        if self.path!="/desktop/probe":self.send_error(404);return
        try:
            length=int(self.headers.get("Content-Length","0"))
            if not 1<=length<=8192:raise ValueError("size")
            event=json.loads(self.rfile.read(length))
            if not isinstance(event,dict) or set(event)!={"stage","message"} or not all(isinstance(v,str) for v in event.values()):
                raise ValueError("probe shape")
            event={"stage":event["stage"][:100],"message":event["message"][:2000]}
        except (ValueError,TypeError,KeyError):
            self.send_error(400);return
        with LOCK:SHELL_EVENTS.append(event)
        self.send_response(204);self.end_headers()
        if event["stage"] in ("bridge_missing","script_error","native_layout_error","csp_violation","browser_url_error"):
            print("SHELL_PROBE",json.dumps(event),flush=True)


def events(kind, **expected):
    with LOCK:
        return next((e for e in reversed(EVENTS) if e.get("kind") == kind and all(e.get(k) == v for k, v in expected.items())), None)

class Mcp:
    """Browser qualification using the reusable, transport-only MCP client."""
    def __init__(self, profile, log_name):
        package = ROOT / "node_modules/chrome-devtools-mcp"
        manifest = json.loads((package / "package.json").read_text(encoding="utf-8"))
        if manifest["version"] != "1.14.0":
            raise RuntimeError("unexpected original MCP version")
        entry = (package / manifest["bin"]["chrome-devtools-mcp"]).resolve()
        self.log = (OUT / log_name).open("wb")
        try:
            self.transport = StdioMcpTransport(
                ["node", str(entry), "--autoConnect", "--user-data-dir=" + str(profile),
                 "--no-usage-statistics", "--no-performance-crux", "--no-javascript-evaluation", "--no-file-navigations",
                 "--experimental-structured-content", "--experimentalInteropTools"],
                cwd=ROOT, stderr=self.log)
        except BaseException:
            self.log.close()
            raise
        self.proc = self.transport.process

    def request(self, method, params, timeout=70):
        # Fixture calls are conservatively treated as possibly consequential.
        # Production classification stays in the existing common ToolProvider.
        return self.transport.request(method, params, timeout=timeout, write=method=="tools/call")

    def initialize(self):
        response = self.transport.initialize(client_name="iwxa-webview2-lab", client_version="0.6.7")
        REPORT.setdefault("mcp_negotiations",[]).append({
            "protocolVersion":response.get("protocolVersion"),
            "serverInfo":response.get("serverInfo"),
            "capabilities":response.get("capabilities")})
        return response["serverInfo"]

    def call(self, name, **args):
        start = time.monotonic()
        result = self.request("tools/call", {"name": name, "arguments": args})
        REPORT.setdefault("calls", []).append({"name": name, "elapsed_ms": round((time.monotonic()-start)*1000), "text_bytes": len(self.text(result).encode("utf-8")), "wire_result_bytes":len(json.dumps(result,ensure_ascii=False,separators=(",",":")).encode("utf-8")), "is_error": bool(result.get("isError")), "uid": args.get("uid")})
        if result.get("isError"):
            raise RuntimeError(f"{name}: {self.text(result)[:1500]}")
        return result
    @staticmethod
    def text(result):
        return "\n".join(part["text"] for part in result.get("content", []) if part.get("type") == "text")
    def close(self):
        try:self.transport.close()
        finally:self.log.close()


def read_json(url):
    with urllib.request.urlopen(url, timeout=3) as response:
        return json.load(response)

def endpoint(profile, form_url, other_urls=()):
    expected_urls={form_url,*other_urls}
    # Microsoft's documented path is the automatically appended EBWebView directory.
    path = profile / "guest" / "EBWebView"
    port_file = path / "DevToolsActivePort"
    def ready():
        if not port_file.is_file(): return None
        port, ws_path = port_file.read_text(encoding="utf-8").splitlines()[:2]
        port = int(port)
        if not (0 < port <= 65535 and ws_path.startswith("/devtools/browser/")):
            raise RuntimeError("invalid DevToolsActivePort")
        base = "http://127.0.0.1:" + str(port)
        targets = read_json(base + "/json/list")
        if not expected_urls <= {t.get("url") for t in targets}: return None
        return path, base, targets
    result = wait_for("guest DevToolsActivePort and live fixture target", ready, timeout=60)
    _, base, targets = result
    shell_files = list((profile / "shell").rglob("DevToolsActivePort"))
    assert not shell_files, "trusted shell unexpectedly exposes CDP"
    assert all(t.get("url") in expected_urls or t.get("url", "").startswith("about:blank") for t in targets), "unexpected target in guest endpoint"
    check("guest_endpoint_without_shell", {"target_types":[t.get("type") for t in targets], "browser":read_json(base+"/json/version").get("Browser")})
    return result[0]

def page_id(client, url):
    result = client.call("list_pages")
    text = client.text(result)
    # 1.14.0: formatPage outputs "<id>: <title> (<url>)".
    rows = re.findall(r"(?m)^\s*(\d+):\s*(.+)$", text)
    found = [int(i) for i, line in rows if url in line]
    if len(found) != 1 or len(rows) != 1:
        raise RuntimeError("expected exactly one visible guest: " + text[:1500])
    return found[0]

def snapshot(client, page, name):
    text = client.text(client.call("take_snapshot", pageId=page))
    (OUT / (name + ".txt")).write_text(text, encoding="utf-8")
    return text

def await_snapshot(client, page, expected, name):
    def probe():
        text = snapshot(client, page, name)
        return text if expected in text else None
    return wait_for("visible page contains " + expected, probe, timeout=20)

def shot(client, page, name):
    result = client.call("take_screenshot", pageId=page, format="png")
    image = next((p for p in result.get("content", []) if p.get("type") == "image"), None)
    if image is None or image.get("mimeType") != "image/png": raise RuntimeError("original MCP returned no PNG")
    data = base64.b64decode(image["data"], validate=True)
    assert data.startswith(b"\x89PNG\r\n\x1a\n")
    (OUT / (name + ".png")).write_bytes(data)

def run(advanced=False, public_sites=False, tab_probe=False):
    if sys.platform != "win32": raise RuntimeError("actual Windows GUI required")
    user = ctypes.windll.user32
    try: user.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))
    except AttributeError: user.SetProcessDPIAware()
    import pyautogui as gui
    gui.FAILSAFE = False
    gui.PAUSE = .1
    user.GetClientRect.argtypes = [W.HWND, ctypes.POINTER(W.RECT)]
    user.ClientToScreen.argtypes = [W.HWND, ctypes.POINTER(W.POINT)]
    user.GetWindowThreadProcessId.argtypes = [W.HWND, ctypes.POINTER(W.DWORD)]
    user.GetDpiForWindow.argtypes = [W.HWND]
    user.GetDpiForWindow.restype = W.UINT
    user.GetWindowTextW.argtypes = [W.HWND, W.LPWSTR, ctypes.c_int]
    user.IsWindowVisible.argtypes = [W.HWND]
    user.SetWindowPos.argtypes = [W.HWND,W.HWND,ctypes.c_int,ctypes.c_int,ctypes.c_int,ctypes.c_int,W.UINT]
    user.PostMessageW.argtypes = [W.HWND,W.UINT,W.WPARAM,W.LPARAM]
    CALLBACK = ctypes.WINFUNCTYPE(W.BOOL,W.HWND,W.LPARAM)
    server = http.server.ThreadingHTTPServer(("127.0.0.1",0),Fixture)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    url = f"http://127.0.0.1:{server.server_port}/form"
    shell_server=http.server.ThreadingHTTPServer(("127.0.0.1",0),TrustedShell)
    threading.Thread(target=shell_server.serve_forever,daemon=True).start()
    shell_url=f"http://127.0.0.1:{shell_server.server_port}/desktop/index.html"
    profile=Path(tempfile.mkdtemp(prefix="iwxa-wv2-data-")).resolve()
    install=Path(tempfile.mkdtemp(prefix="iwxa-wv2-install-")).resolve()
    binary=install/"iwxa-webview2-mcp-lab.exe"
    shutil.copy2(ROOT/"src-tauri/target/debug/iwxa-webview2-mcp-lab.exe",binary)
    clients = []
    hosts = []
    def start_host(name,two_tabs=False):
        log = (OUT/(name+".log")).open("wb")
        env = dict(os.environ, IWXA_LAB_PROFILE=str(profile), IWXA_LAB_URL=url, IWXA_LAB_SHELL_URL=shell_url, WEBVIEW2_USER_DATA_FOLDER=str(profile/"shell"), IWXA_LAB_PUBLIC_SITES="1" if public_sites else "0", IWXA_LAB_TWO_TABS="1" if two_tabs else "0")
        # No global remote-debugging flag; do not inherit an externally configured one.
        env.pop("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", None)
        proc = subprocess.Popen([str(binary)],env=env,stdout=log,stderr=subprocess.STDOUT)
        hosts.append((proc,log))
        def find():
            if proc.poll() is not None: raise RuntimeError("native app exited: " + str(proc.returncode))
            matches=[]
            @CALLBACK
            def visit(hwnd, _):
                pid = W.DWORD()
                user.GetWindowThreadProcessId(hwnd,ctypes.byref(pid))
                title = ctypes.create_unicode_buffer(128)
                user.GetWindowTextW(hwnd,title,128)
                if pid.value == proc.pid and title.value == "IWXA WebView2 MCP Lab" and user.IsWindowVisible(hwnd):
                    matches.append(hwnd)
                return True
            user.EnumWindows(visit,0)
            if len(matches) > 1: raise AssertionError("more than one lab HWND")
            return matches[0] if matches else None
        hwnd = wait_for("one native lab window",find,timeout=70)
        return proc,hwnd
    def geom(hwnd):
        rect=W.RECT(); pt=W.POINT(0,0)
        if not user.GetClientRect(hwnd,ctypes.byref(rect)) or not user.ClientToScreen(hwnd,ctypes.byref(pt)):
            raise RuntimeError("window geometry unavailable")
        return pt.x,pt.y,rect.right,rect.bottom,user.GetDpiForWindow(hwnd)/96
    def layout_pixels(hwnd, visible, name):
        def probe():
            x,y,w,h,_=geom(hwnd)
            current=pane_bounds(hwnd)
            if visible:
                if not current:return False
                bx,by,bw,bh=current
                if not (0<bx<w and 0<by<h and bx+bw==w and by+bh==h):return False
            elif current is not None:return False
            im=gui.screenshot().convert("RGB")
            if x<0 or y<0 or x+w>=im.width or y+h>=im.height: return False
            left=im.getpixel((x+min(8,w//4),y+h-18))
            right=im.getpixel((x+w-18,y+h-18))
            near=lambda a,b: all(abs(i-j)<=12 for i,j in zip(a,b))
            expected=(229,244,236) if visible else (242,232,252)
            if near(left,(242,232,252)) and near(right,expected):
                im.save(OUT/(name+".png")); return True
            return False
        wait_for("actual pixels "+name,probe,timeout=30)
    def click_shell(hwnd):
        x,y,w,h,d=geom(hwnd)
        gui.click(round(x+100*d),round(y+47*d))
    def pane_bounds(hwnd):
        pid=W.DWORD();user.GetWindowThreadProcessId(hwnd,ctypes.byref(pid))
        logs=[Path(log.name) for process,log in hosts if process.pid==pid.value]
        assert len(logs)==1
        entries=re.findall(r"(?m)^LAB_PANE_(?:BOUNDS (\d+) (\d+) (\d+) (\d+)|HIDDEN)\s*$",logs[0].read_text(encoding="utf-8",errors="replace"))
        if not entries or not entries[-1][0]: return None
        return tuple(map(int,entries[-1]))
    def click_guest(hwnd,x_css,y_css):
        x,y,w,h,d=geom(hwnd)
        bx,by,bw,bh=wait_for("native slot bounds",lambda:pane_bounds(hwnd))
        gui.click(round(x+bx+x_css*d),round(y+by+y_css*d))
    def check_profile_layout():
        assert (profile/"shell"/"EBWebView").is_dir()
        assert (profile/"guest"/"EBWebView").is_dir()
        assert not list(install.glob("*.WebView2"))
        assert not list((profile/"shell").rglob("DevToolsActivePort"))
        check("installed_like_separate_profiles_without_adjacent_cache")
    def start_client(folder,name):
        client=Mcp(folder,name+".log"); clients.append(client)
        info=client.initialize()
        check("mcp_handshake_"+name, info)
        return client
    try:
        proc,hwnd=start_host("host")
        folder=endpoint(profile,url)
        # Place the real window within the visible desktop; do not emulate page geometry.
        sw,sh=gui.size()
        user.SetWindowPos(hwnd,0,12,12,min(sw-36,1040),min(sh-80,680),0x0040)
        layout_pixels(hwnd,True,"01-native-split")
        check("one_window_two_visible_native_surfaces",list(geom(hwnd)))
        check_profile_layout()
        check("remote_shell_native_dock_rect",list(wait_for("slot bounds",lambda:pane_bounds(hwnd))))
        def layout_ack():
            with LOCK:return any(event["stage"]=="native_layout_ack" and event["message"]=="true" for event in SHELL_EVENTS)
        wait_for("trusted shell received native IPC acknowledgement",layout_ack,timeout=10)
        check("remote_shell_native_ipc_ack")
        client=start_client(folder,"first")
        catalog=client.request("tools/list",{})
        tools=catalog["tools"]
        (OUT/"original-mcp-catalog.json").write_text(json.dumps(catalog,indent=2,ensure_ascii=False),encoding="utf-8")
        names=[t["name"] for t in tools]
        required={"list_pages","take_snapshot","fill","click","take_screenshot","navigate_page"}
        if advanced:required|={"fill_form","upload_file"}
        assert len(names)==len(set(names)) and required<=set(names)
        assert all(isinstance(t.get("inputSchema"),dict) for t in tools)
        check("original_tool_catalog",len(tools))
        page=page_id(client,url)
        text=snapshot(client,page,"02-initial-ax")
        assert "IWXA shared browser fixture" in text
        check("original_mcp_target_and_accessibility_tree",page)
        click_guest(hwnd,95,118)
        gui.write("Human-167",interval=.07)
        wait_for("physical human typing observed by fixture",lambda:events("human",human="Human-167",trusted=True))
        text=snapshot(client,page,"03-human-input")
        assert "Human-167" in text
        check("human_input_visible_to_original_mcp")
        client.call("fill",pageId=page,uid=uid(text,"textbox","Agent entry"),value="Agent-167")
        wait_for("actual fixture input event from original MCP",lambda:events("agent",human="Human-167",agent="Agent-167"))
        text=snapshot(client,page,"04-agent-input")
        assert "Agent-167" in text and "Human-167" in text
        client.call("click",pageId=page,uid=uid(text,"button","Save by agent"))
        wait_for("actual site saved by MCP",lambda:events("Saved",human="Human-167",agent="Agent-167"))
        text=snapshot(client,page,"05-saved")
        assert "Saved Human-167 Agent-167" in text
        shot(client,page,"06-original-mcp")
        layout_pixels(hwnd,True,"07-native-after-mcp")
        check("mcp_fill_click_readback_and_png")
        # Hide and reopen the actual child through the trusted local shell.
        click_shell(hwnd);layout_pixels(hwnd,False,"08-hidden")
        click_shell(hwnd);layout_pixels(hwnd,True,"09-reopened")
        assert "Saved Human-167 Agent-167" in snapshot(client,page,"10-after-reopen")
        check("hide_show_preserves_page")
        before=geom(hwnd)
        user.SetWindowPos(hwnd,0,12,12,max(720,min(sw-36,1040)-160),max(480,min(sh-80,680)-95),0x0040)
        layout_pixels(hwnd,True,"11-resized")
        assert geom(hwnd)[2:4] != before[2:4]
        check("native_resize_preserves_two_surfaces",list(geom(hwnd)))
        initial_bounds=pane_bounds(hwnd)
        x,y,w,h,d=geom(hwnd)
        gui.click(round(x+48*d),round(y+203*d))
        wait_for("user adjusted browser pane width",lambda:pane_bounds(hwnd) and pane_bounds(hwnd)[0]>initial_bounds[0])
        layout_pixels(hwnd,True,"11b-adjusted-width")
        check("native_dock_width_tracks_trusted_dom",list(pane_bounds(hwnd)))
        client.close();clients.remove(client)
        click_guest(hwnd,95,282)
        wait_for("physical finish after MCP disconnect",lambda:events("Finished",human="Human-167",agent="Agent-167",trusted=True))
        check("manual_finish_after_mcp_disconnect")
        user.PostMessageW(hwnd,0x0010,0,0)
        proc.wait(timeout=25)
        with LOCK: EVENTS.clear()
        proc2,hwnd2=start_host("host-restart")
        wait_for("persisted fixture data restored by page load",lambda:events("load",result="Finished Human-167 Agent-167"),timeout=60)
        folder2=endpoint(profile,url)
        client2=start_client(folder2,"restart")
        page2=page_id(client2,url)
        assert "Finished Human-167 Agent-167" in snapshot(client2,page2,"12-cold-restart")
        check("cold_restart_same_profile_and_original_mcp")
        second_url=url.rsplit("/",1)[0]+"/second"
        client2.call("navigate_page",pageId=page2,type="url",url=second_url)
        assert page_id(client2,second_url)==page2
        assert "Second fixture page" in snapshot(client2,page2,"13-second-page")
        client2.call("navigate_page",pageId=page2,type="back")
        assert page_id(client2,url)==page2
        assert "Finished Human-167 Agent-167" in snapshot(client2,page2,"14-history-back")
        check("original_mcp_navigation_and_back_in_same_native_guest")
        # The native toolbar navigates the same page seen by the MCP, not a second tab.
        user.SetWindowPos(hwnd2,0,12,12,min(sw-36,1040),min(sh-80,680),0x0040)
        layout_pixels(hwnd2,True,"14b-toolbar-ready")
        x,y,w,h,d=geom(hwnd2); bx,by,bw,bh=wait_for("toolbar slot",lambda:pane_bounds(hwnd2))
        gui.click(round(x+bx+200*d),round(y+25*d));gui.hotkey("ctrl","a");gui.write(second_url,interval=.02);gui.press("enter")
        wait_for("manual toolbar navigation",lambda:page_id(client2,second_url)==page2)
        gui.click(round(x+bx+22*d),round(y+25*d))
        wait_for("native toolbar back",lambda:page_id(client2,url)==page2)
        await_snapshot(client2,page2,"Finished Human-167 Agent-167","14c-toolbar-back")
        check("native_toolbar_address_and_history_share_mcp_target")
        REPORT["g1_status"]="PASS"
        if advanced:
            REPORT["g2_status"]="FAIL"
            advanced_url=url.rsplit("/",1)[0]+"/advanced"
            client2.call("navigate_page",pageId=page2,type="url",url=advanced_url)
            text=snapshot(client2,page2,"15-advanced-unsigned")
            assert "Session: signed out" in text
            user.SetWindowPos(hwnd2,0,12,12,min(sw-36,1040),min(sh-80,680),0x0040)
            layout_pixels(hwnd2,True,"16-advanced-native")
            click_guest(hwnd2,95,97)
            wait_for("native human signin",lambda:events("advanced_signin"))
            wait_for("server authenticated page",lambda:events("advanced_load",authenticated=True))
            text=await_snapshot(client2,page2,"Session: test operator","17-authenticated")
            check("human_cookie_signin_visible_to_original_mcp")
            # One original batch call; no custom JS evaluator and no individual fake actions.
            client2.call("fill_form",pageId=page2,elements=[
                {"uid":uid(text,"textbox","Company"),"value":"IWXA Lab"},
                {"uid":uid(text,"combobox","Delivery method"),"value":"Express"},
                {"uid":uid(text,"checkbox","Include receipt"),"value":"true"}])
            text=snapshot(client2,page2,"18-batch-form")
            client2.call("click",pageId=page2,uid=uid(text,"button","Submit order"))
            order=wait_for("real authenticated POST",lambda:events("advanced_order"))
            assert order["fields"]=={"company":["IWXA Lab"],"delivery":["Express"],"receipt":["yes"]}
            check("original_fill_form_and_server_order",order["fields"])
            text=await_snapshot(client2,page2,"Order accepted 167","19-advanced-order")
            upload=profile/"iwxa-supporting.txt"
            upload.write_bytes(UPLOAD_BYTES)
            client2.call("upload_file",pageId=page2,uid=uid(text,"button","Supporting file"),filePaths=[str(upload)])
            text=snapshot(client2,page2,"20-file-selected")
            client2.call("click",pageId=page2,uid=uid(text,"button","Upload evidence"))
            uploaded=wait_for("actual uploaded file bytes",lambda:events("advanced_upload"))
            assert uploaded["size"]==len(UPLOAD_BYTES)
            assert uploaded["sha256"]==hashlib.sha256(UPLOAD_BYTES).hexdigest()
            check("original_mcp_file_upload_byte_identity",uploaded)
            text=await_snapshot(client2,page2,"Uploaded "+str(len(UPLOAD_BYTES))+" bytes","21-uploaded")
            client2.call("click",pageId=page2,uid=uid(text,"link","Download result"))
            wait_for("actual download HTTP request",lambda:events("advanced_download"))
            destination=profile/"downloads"/"iwxa-result.txt"
            wait_for("native WebView2 downloaded exact bytes",lambda:destination.is_file() and destination.read_bytes()==DOWNLOAD_BYTES,timeout=45)
            check("native_webview_download_byte_identity",{"size":len(DOWNLOAD_BYTES),"sha256":hashlib.sha256(destination.read_bytes()).hexdigest()})
            shot(client2,page2,"22-advanced-original-mcp")
            try:
                import psutil
                def process_tree(pid):
                    root=psutil.Process(pid)
                    rows=[]
                    for process in [root]+root.children(recursive=True):
                        try: rows.append({"name":process.name(),"rss_bytes":process.memory_info().rss})
                        except (psutil.NoSuchProcess,psutil.AccessDenied): pass
                    return rows
                REPORT["resource_sample"]={"host_process_tree":process_tree(proc2.pid),"mcp_process_tree":process_tree(client2.proc.pid),"note":"RSS sum may double-count shared pages; not unique physical memory"}
            except (ImportError,OSError) as exc:
                REPORT["resource_sample_error"]=repr(exc)
            client2.close();clients.remove(client2)
            user.PostMessageW(hwnd2,0x0010,0,0);proc2.wait(timeout=25)
            with LOCK: EVENTS.clear()
            proc3,hwnd3=start_host("host-cookie-restart")
            folder3=endpoint(profile,url)
            client3=start_client(folder3,"cookie-restart")
            page3=page_id(client3,url)
            client3.call("navigate_page",pageId=page3,type="url",url=advanced_url)
            wait_for("cookie restored at HTTP server after app restart",lambda:events("advanced_load",authenticated=True))
            await_snapshot(client3,page3,"Session: test operator","23-cookie-restart")
            check("http_only_cookie_persists_after_cold_restart")
            REPORT["g2_status"]="PASS"
        if public_sites:
            if not advanced:raise RuntimeError("public smoke requires the completed local browser route")
            from PIL import Image,ImageChops,ImageStat
            user.SetWindowPos(hwnd3,0,12,12,min(sw-36,1040),min(sh-80,680),0x0040)
            # The last fixture page is still visible before leaving the local website.
            layout_pixels(hwnd3,True,"24-public-start")
            REPORT["public_sites_status"]="PARTIAL"
            REPORT["public_sites"]=[]
            for name,address,allowed_hosts,markers in SITES:
                entry={"name":name,"requested_url":address,"status":"NOT_CONFIRMED"}
                REPORT["public_sites"].append(entry)
                start=time.monotonic()
                try:
                    # Only navigation and observation of public sites; no forms, account or mutation.
                    client3.call("navigate_page",pageId=page3,type="url",url=address,timeout=30000)
                    actual=validate_public_page(client3.call("list_pages"),page3,allowed_hosts)
                    entry["actual_url"]=actual["url"];entry["title"]=actual["title"]
                    def public_content():
                        value=snapshot(client3,page3,"public-"+name+"-ax")
                        try:count=validate_public_snapshot(value,markers)
                        except RuntimeError:return None
                        return value,count
                    observed,nodes=wait_for("actual public AX "+name,public_content,timeout=20)
                    entry["ax_nodes"]=nodes;entry["ax_text_bytes"]=len(observed.encode("utf-8"))
                    shot(client3,page3,"public-"+name+"-mcp")
                    desktop=gui.screenshot().convert("RGB")
                    desktop.save(OUT/("public-"+name+"-native.png"))
                    x,y,w,h,d=geom(hwnd3)
                    bx,by,bw,bh=wait_for("native public slot",lambda:pane_bounds(hwnd3))
                    if not(0<=x+bx<x+bx+bw<=desktop.width and 0<=y+by<y+by+bh<=desktop.height):
                        raise RuntimeError("public_native_slot_outside_desktop")
                    native=desktop.crop((x+bx,y+by,x+bx+bw,y+by+bh))
                    native.save(OUT/("public-"+name+"-crop.png"))
                    with Image.open(OUT/("public-"+name+"-mcp.png")) as image:
                        actual_image=image.convert("RGB")
                    entry["mcp_pixels"]=list(actual_image.size);entry["native_pixels"]=list(native.size)
                    # Same actual viewport, not an image used as the UI.
                    entry["pixel_mean_difference"]=round(sum(ImageStat.Stat(ImageChops.difference(
                        native.resize((64,64)),actual_image.resize((64,64)))).mean)/3,3)
                    if actual_image.size!=native.size or entry["pixel_mean_difference"]>16:
                        raise RuntimeError("public_native_viewport_not_confirmed")
                    entry["status"]="PASS"
                    check("public_visible_page_"+name,entry.copy())
                except (RuntimeError,AssertionError,OSError,ValueError) as exc:
                    entry["error"]=str(exc)[:1000]
                    print("PUBLIC_NOT_CONFIRMED",name,entry["error"],flush=True)
                finally:entry["elapsed_ms"]=round((time.monotonic()-start)*1000)
            if all(e["status"]=="PASS" for e in REPORT["public_sites"]):
                REPORT["public_sites_status"]="PASS"
        if tab_probe:
            if not advanced:raise RuntimeError("tab probe requires completed local route")
            REPORT["tabs_status"]="FAIL"
            client3.close();clients.remove(client3)
            user.PostMessageW(hwnd3,0x0010,0,0);proc3.wait(timeout=25)
            profile=Path(tempfile.mkdtemp(prefix="iwxa-wv2-tabs-")).resolve()
            def blank_bootstrap(index):
                return f"about:blank#lab-native-tab-{index}"
            bootstrap={"guest":blank_bootstrap(0),"guest-2":blank_bootstrap(1)}
            proc4,hwnd4=start_host("host-tabs",two_tabs=True)
            folder4=endpoint(profile,bootstrap["guest"],[bootstrap["guest-2"]])
            user.SetWindowPos(hwnd4,0,12,12,min(sw-36,1040),min(sh-80,680),0x0040)
            # The blank page has no green fixture background. First observe
            # its identity through the original MCP without navigating it.
            client4=start_client(folder4,"tabs")
            def tab_catalog(client):
                pages=page_catalog(client.call("list_pages"))
                ids={p["id"]:stable_tab_id(client.call("get_tab_id",pageId=p["id"])) for p in pages}
                return pages,ids
            pages,ids=tab_catalog(client4)
            binding=bind_bootstrap(bootstrap,pages,ids)
            by_label=rebind_pages(binding,pages,ids)
            check("two_native_guests_same_profile_original_tab_identity",{"binding":binding})
            check("blank_fragment_bootstrap_visible_to_original_mcp",{"indices":[0,1]})
            for page in by_label.values():
                client4.call("navigate_page",pageId=page,type="url",url=url)
            pages,ids=tab_catalog(client4)
            assert len(pages)==2 and all(p["url"]==url for p in pages)
            assert rebind_pages(binding,pages,ids)==by_label
            check("same_url_tabs_have_distinct_original_identity")
            # The existing color/geometry witness applies to the loaded
            # fixture, not to a genuinely empty WebView2 document.
            layout_pixels(hwnd4,True,"25-two-native-tabs")
            page_a,page_b=by_label["guest"],by_label["guest-2"]
            click_guest(hwnd4,95,118);gui.hotkey("ctrl","a");gui.write("Tab-A-Human",interval=.04)
            wait_for("physical input in native tab A",lambda:events("human",human="Tab-A-Human",trusted=True))
            assert "Tab-A-Human" in snapshot(client4,page_a,"26-tab-a")
            assert "Tab-A-Human" not in snapshot(client4,page_b,"27-tab-b-before")
            check("human_input_does_not_cross_same_url_tabs")
            x,y,w,h,d=geom(hwnd4);bx,by,bw,bh=wait_for("tab strip native slot",lambda:pane_bounds(hwnd4))
            # Toolbar is 54 CSS px; tab strip occupies the next 36.
            gui.click(round(x+bx+135*d),round(y+72*d))
            wait_for("native host selected B",lambda:"LAB_ACTIVE_TAB 1" in (OUT/"host-tabs.log").read_text(encoding="utf-8",errors="replace"))
            layout_pixels(hwnd4,True,"28-visible-tab-b")
            text=snapshot(client4,page_b,"29-tab-b")
            client4.call("fill",pageId=page_b,uid=uid(text,"textbox","Agent entry"),value="Tab-B-Agent")
            assert "Tab-B-Agent" in snapshot(client4,page_b,"30-tab-b-agent")
            assert "Tab-B-Agent" not in snapshot(client4,page_a,"31-tab-a-unchanged")
            shot(client4,page_b,"32-tab-b-mcp")
            from PIL import Image,ImageChops,ImageStat
            desktop=gui.screenshot().convert("RGB");desktop.save(OUT/"32-tab-b-native.png")
            x,y,w,h,d=geom(hwnd4);bx,by,bw,bh=wait_for("visible tab B bounds",lambda:pane_bounds(hwnd4))
            native=desktop.crop((x+bx,y+by,x+bx+bw,y+by+bh))
            with Image.open(OUT/"32-tab-b-mcp.png") as image:original=image.convert("RGB")
            diff=sum(ImageStat.Stat(ImageChops.difference(native.resize((64,64)),original.resize((64,64)))).mean)/3
            assert native.size==original.size and diff<=12
            check("original_mcp_acts_on_selected_native_tab",{"pixel_mean_difference":round(diff,3)})
            client4.close();clients.remove(client4)
            client5=start_client(folder4,"tabs-reconnect")
            pages,ids=tab_catalog(client5)
            rebound=rebind_pages(binding,pages,ids)
            assert len(pages)==2 and all(p["url"]==url for p in pages)
            assert "Tab-A-Human" in snapshot(client5,rebound["guest"],"33-tab-a-reconnected")
            assert "Tab-B-Agent" in snapshot(client5,rebound["guest-2"],"34-tab-b-reconnected")
            check("same_url_native_tabs_rebound_after_mcp_disconnect")
            REPORT["tabs_status"]="PASS"
            REPORT["dynamic_tabs_status"]="FAIL"
            def click_tab_action(new):
                x,y,w,h,d=geom(hwnd4)
                gui.click(round(x+w-(59 if new else 21)*d),round(y+72*d))
            def added(old,label,bootstrap_url):
                def probe():
                    pages,ids=tab_catalog(client5)
                    return attach_tab(old,label,bootstrap_url,pages,ids)
                return wait_for("new original tab "+label,probe,timeout=35)
            def removed(old,label):
                def probe():
                    pages,ids=tab_catalog(client5)
                    return detach_tab(old,label,pages,ids)
                return wait_for("closed original tab "+label,probe,timeout=35)
            click_tab_action(True)
            wait_for("native create C",lambda:"LAB_TAB_CREATED 2 " in (OUT/"host-tabs.log").read_text(encoding="utf-8",errors="replace"))
            binding=added(binding,"guest-3",blank_bootstrap(2))
            pages,ids=tab_catalog(client5);by_label=rebind_pages(binding,pages,ids)
            page_c=by_label["guest-3"]
            client5.call("navigate_page",pageId=page_c,type="url",url=url)
            layout_pixels(hwnd4,True,"35-created-tab-c")
            text=snapshot(client5,page_c,"36-c-before")
            client5.call("fill",pageId=page_c,uid=uid(text,"textbox","Agent entry"),value="Tab-C-Agent")
            assert "Tab-C-Agent" in snapshot(client5,page_c,"37-c-after")
            assert "Tab-C-Agent" not in snapshot(client5,by_label["guest-2"],"38-b-unchanged")
            check("native_dynamic_create_same_profile_original_identity")
            closed_id=binding["guest-3"]
            click_tab_action(False)
            wait_for("native close C",lambda:"LAB_TAB_CLOSED 2" in (OUT/"host-tabs.log").read_text(encoding="utf-8",errors="replace"))
            binding=removed(binding,"guest-3")
            layout_pixels(hwnd4,True,"39-closed-tab-c")
            pages,ids=tab_catalog(client5);by_label=rebind_pages(binding,pages,ids)
            assert "Tab-B-Agent" in snapshot(client5,by_label["guest-2"],"40-b-after-close")
            assert "Tab-A-Human" in snapshot(client5,by_label["guest"],"41-a-after-close")
            check("native_dynamic_close_preserves_surviving_tab_identity")
            click_tab_action(True)
            wait_for("native create D",lambda:"LAB_TAB_CREATED 3 " in (OUT/"host-tabs.log").read_text(encoding="utf-8",errors="replace"))
            binding=added(binding,"guest-4",blank_bootstrap(3))
            assert binding["guest-4"]!=closed_id
            pages,ids=tab_catalog(client5);by_label=rebind_pages(binding,pages,ids)
            page_d=by_label["guest-4"]
            assert "Tab-C-Agent" not in snapshot(client5,page_d,"42-d-new")
            client5.call("navigate_page",pageId=page_d,type="url",url=url)
            pages,ids=tab_catalog(client5)
            assert rebind_pages(binding,pages,ids)["guest-4"]==page_d
            assert all(p["url"]==url for p in pages)
            assert "Tab-C-Agent" not in snapshot(client5,page_d,"42b-d-fixture")
            layout_pixels(hwnd4,True,"43-new-tab-d")
            check("native_dynamic_reopen_never_reuses_closed_identity")
            REPORT["dynamic_tabs_status"]="PASS"
            check("blank_fragment_dynamic_tab_identity",{"indices":[2,3]})
        if public_sites and REPORT["public_sites_status"]!="PASS":
            raise RuntimeError("one or more public sites not confirmed; local and tab results preserved")
        REPORT["status"]="PASS"
    except BaseException:
        try:gui.screenshot().save(OUT/"failure-native-before-cleanup.png")
        except Exception:pass
        for process,log in hosts:
            try:print("NATIVE_HOST_LOG",Path(log.name).read_text(encoding="utf-8",errors="replace")[-6000:],flush=True)
            except OSError:pass
        raise
    finally:
        for client in clients:
            try: client.close()
            except Exception: pass
        for proc, log in hosts:
            if proc.poll() is None:
                subprocess.run(["taskkill","/PID",str(proc.pid),"/T","/F"],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                try: proc.wait(timeout=10)
                except subprocess.TimeoutExpired: proc.kill()
            log.close()
        server.shutdown(); server.server_close()
        shell_server.shutdown(); shell_server.server_close()
        with LOCK:
            REPORT["last_fixture_events"]=EVENTS[-30:]
            REPORT["shell_events"]=SHELL_EVENTS[-100:]

if __name__ == "__main__":
    try: run(advanced="--advanced" in sys.argv,public_sites="--public-sites" in sys.argv,tab_probe="--two-tabs" in sys.argv)
    except BaseException as exc:
        REPORT["error"]=repr(exc)
        try:
            import pyautogui
            pyautogui.screenshot().save(OUT/"failure-desktop.png")
        except Exception: pass
        raise
    finally:
        (OUT/"report.json").write_text(json.dumps(REPORT,indent=2),encoding="utf-8")
