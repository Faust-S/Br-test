"""Native Windows + original Google MCP qualification. No direct browser automation SDK."""
import base64
import hashlib
import ctypes
from ctypes import wintypes as W
import http.server
import json
import os
from pathlib import Path
import queue
import re
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from snapshot_refs import uid
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
        if self.path == "/form":
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

def events(kind, **expected):
    with LOCK:
        return next((e for e in reversed(EVENTS) if e.get("kind") == kind and all(e.get(k) == v for k, v in expected.items())), None)

class Mcp:
    """Qualification-only JSON-RPC stdio driver. The browser tools are Google's, unchanged."""
    def __init__(self, profile, log_name):
        package = ROOT / "node_modules/chrome-devtools-mcp"
        manifest = json.loads((package / "package.json").read_text(encoding="utf-8"))
        if manifest["version"] != "1.14.0":
            raise RuntimeError("unexpected original MCP version")
        entry = (package / manifest["bin"]["chrome-devtools-mcp"]).resolve()
        self.log = (OUT / log_name).open("wb")
        self.proc = subprocess.Popen(
            ["node", str(entry), "--autoConnect", "--user-data-dir=" + str(profile),
             "--no-usage-statistics", "--no-performance-crux", "--no-javascript-evaluation", "--no-file-navigations"],
            cwd=ROOT, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=self.log,
            encoding="utf-8", text=True, bufsize=1, creationflags=subprocess.CREATE_NO_WINDOW)
        self.messages = queue.Queue()
        self.sequence = 0
        self.reader = threading.Thread(target=self._read, daemon=True)
        self.reader.start()
    def _read(self):
        try:
            for line in self.proc.stdout:
                try: self.messages.put(json.loads(line))
                except ValueError: self.messages.put({"_invalid_stdout": line[:300]})
        finally:
            self.messages.put({"_eof": True})
    def send(self, message):
        self.proc.stdin.write(json.dumps(message, ensure_ascii=True) + "\n")
        self.proc.stdin.flush()
    def request(self, method, params, timeout=70):
        self.sequence += 1
        request_id = self.sequence
        self.send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        until = time.monotonic() + timeout
        while True:
            remaining = until - time.monotonic()
            if remaining <= 0:
                raise RuntimeError("MCP timeout: " + method)
            try: response = self.messages.get(timeout=remaining)
            except queue.Empty: raise RuntimeError("MCP timeout: " + method)
            if "_invalid_stdout" in response or "_eof" in response:
                raise RuntimeError("MCP stdio failure: " + str(response))
            if response.get("id") != request_id:
                # No sampling/elicitation is needed for these deterministic tools.
                if "id" in response and "method" in response:
                    self.send({"jsonrpc":"2.0","id":response["id"],"error":{"code":-32601,"message":"Qualification client does not implement this method"}})
                continue
            if "error" in response:
                raise RuntimeError(f"MCP {method}: {response['error']}")
            return response["result"]
    def initialize(self):
        response = self.request("initialize", {"protocolVersion":"2025-11-25","capabilities":{},"clientInfo":{"name":"iwxa-webview2-lab","version":"0.6.7"}})
        self.send({"jsonrpc":"2.0","method":"notifications/initialized"})
        return response["serverInfo"]
    def call(self, name, **args):
        start = time.monotonic()
        result = self.request("tools/call", {"name": name, "arguments": args})
        REPORT.setdefault("calls", []).append({"name": name, "elapsed_ms": round((time.monotonic()-start)*1000), "text_bytes": len(self.text(result).encode("utf-8")), "is_error": bool(result.get("isError")), "uid": args.get("uid")})
        if result.get("isError"):
            raise RuntimeError(f"{name}: {self.text(result)[:1500]}")
        return result
    @staticmethod
    def text(result):
        return "\n".join(part["text"] for part in result.get("content", []) if part.get("type") == "text")
    def close(self):
        if self.proc.stdin and not self.proc.stdin.closed: self.proc.stdin.close()
        try: self.proc.wait(timeout=15)
        except subprocess.TimeoutExpired:
            self.proc.terminate()
            self.proc.wait(timeout=10)
        finally:
            self.log.close()

def read_json(url):
    with urllib.request.urlopen(url, timeout=3) as response:
        return json.load(response)

def endpoint(profile, form_url):
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
        if not any(t.get("url") == form_url for t in targets): return None
        return path, base, targets
    result = wait_for("guest DevToolsActivePort and live fixture target", ready, timeout=60)
    _, base, targets = result
    shell_files = list((profile / "shell").rglob("DevToolsActivePort"))
    assert not shell_files, "trusted shell unexpectedly exposes CDP"
    assert all(t.get("url") == form_url or t.get("url", "").startswith("about:blank") for t in targets), "unexpected target in guest endpoint"
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

def run(advanced=False):
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
    binary = ROOT / "src-tauri/target/debug/iwxa-webview2-mcp-lab.exe"
    profile = Path(tempfile.mkdtemp(prefix="iwxa-wv2-")).resolve()
    clients = []
    hosts = []
    def start_host(name):
        log = (OUT/(name+".log")).open("wb")
        env = dict(os.environ, IWXA_LAB_PROFILE=str(profile), IWXA_LAB_URL=url)
        # No global remote-debugging flag; do not inherit an externally configured one.
        env.pop("WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS", None)
        env.pop("WEBVIEW2_USER_DATA_FOLDER", None)
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
    def click_guest(hwnd,x_css,y_css):
        x,y,w,h,d=geom(hwnd)
        gui.click(round(x+(w*38//100)+x_css*d),round(y+y_css*d))
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
        client=start_client(folder,"first")
        tools=client.request("tools/list",{})["tools"]
        required={"list_pages","take_snapshot","fill","click","take_screenshot","navigate_page"}
        assert required <= {t["name"] for t in tools}
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
        REPORT["status"]="PASS"
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
        with LOCK: REPORT["last_fixture_events"]=EVENTS[-30:]

if __name__ == "__main__":
    try: run(advanced="--advanced" in sys.argv)
    except BaseException as exc:
        REPORT["error"]=repr(exc)
        try:
            import pyautogui
            pyautogui.screenshot().save(OUT/"failure-desktop.png")
        except Exception: pass
        raise
    finally:
        (OUT/"report.json").write_text(json.dumps(REPORT,indent=2),encoding="utf-8")
