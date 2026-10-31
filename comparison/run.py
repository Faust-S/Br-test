"""Public-only compatibility probes. Collection success is NOT product acceptance."""
import base64, hashlib, http.server, json, os, platform, shutil, socket, subprocess, sys, threading, time, urllib.request
from pathlib import Path
import psutil
import websocket
ROOT=Path(__file__).resolve().parent.parent
OUT=ROOT/'artifacts'; OUT.mkdir(exist_ok=True)
sys.path.insert(0,str(ROOT/'tests'))
import servo_probe as servo

def read_json(url):
    with urllib.request.urlopen(url,timeout=15) as r: return json.load(r)
def port():
    with socket.socket() as s:
        s.bind(('127.0.0.1',0)); return s.getsockname()[1]
class CDP:
    def __init__(self,endpoint):
        self.ws=websocket.create_connection(endpoint,timeout=15,suppress_origin=True); self.seq=0; self.session=None
        target=self.call('Target.createTarget',{'url':'about:blank'})['targetId']
        self.session=self.call('Target.attachToTarget',{'targetId':target,'flatten':True})['sessionId']
        self.call('Page.enable'); self.call('Runtime.enable')
    def call(self,method,params=None):
        self.seq+=1; packet={'id':self.seq,'method':method,'params':params or {}}
        if self.session: packet['sessionId']=self.session
        self.ws.send(json.dumps(packet)); deadline=time.monotonic()+20
        while time.monotonic()<deadline:
            msg=json.loads(self.ws.recv())
            if msg.get('id')==self.seq:
                if 'error' in msg: raise RuntimeError(str(msg['error']))
                return msg.get('result',{})
        raise TimeoutError(method)
    def visit(self,url): self.call('Page.navigate',{'url':url})
    def evaluate(self,expr):
        r=self.call('Runtime.evaluate',{'expression':expr,'returnByValue':True})
        if r.get('exceptionDetails'): raise RuntimeError(str(r['exceptionDetails']))
        return r.get('result',{}).get('value')
    def screenshot(self,name):
        r=self.call('Page.captureScreenshot',{'format':'png'}); (OUT/name).write_bytes(base64.b64decode(r['data']))
    def close(self): self.ws.close()
class WebDriver:
    def __init__(self,endpoint,proc):
        self.endpoint=endpoint; servo.wait_webdriver(endpoint,proc,30); self.sid=servo.try_session(endpoint)
    def evaluate(self,expr): return servo.execute(self.endpoint,self.sid,expr)
    def visit(self,url): return servo.visit(self.endpoint,self.sid,url)
    def screenshot(self,name):
        r=servo.wd(self.endpoint,f'/session/{self.sid}/screenshot',method='GET'); (OUT/name).write_bytes(base64.b64decode(r))
    def close(self):
        try: servo.wd(self.endpoint,f'/session/{self.sid}',method='DELETE',timeout=3)
        except Exception: pass

def poll(client,expression,predicate,seconds=25):
    deadline=time.monotonic()+seconds; last=None
    while time.monotonic()<deadline:
        try:
            last=client.evaluate(expression)
            if predicate(last): return last
        except Exception as exc: last={'poll_error':str(exc)}
        time.sleep(.1)
    raise TimeoutError(str(last)[:1500])
def chrome_path():
    candidates=[os.environ.get('CHROME_PATH'),shutil.which('google-chrome'),shutil.which('chromium'),r'C:\Program Files\Google\Chrome\Application\chrome.exe',r'C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe']
    return next((Path(x) for x in candidates if x and Path(x).is_file()),None)
def lightpanda():
    release=read_json('https://api.github.com/repos/lightpanda-io/browser/releases/tags/1.0.2')
    a=next(x for x in release['assets'] if x['name']=='lightpanda-x86_64-linux'); digest=a.get('digest')
    if not digest or not digest.startswith('sha256:'): raise RuntimeError('Official checksum missing; refusing execution')
    binary=OUT/a['name']
    with urllib.request.urlopen(a['browser_download_url'],timeout=120) as r,binary.open('wb') as f: shutil.copyfileobj(r,f)
    actual=hashlib.sha256(binary.read_bytes()).hexdigest()
    if actual!=digest.split(':')[1]: raise RuntimeError('Lightpanda checksum mismatch')
    binary.chmod(0o700); return binary,{'tag':'1.0.2','sha256':actual,'source':a['browser_download_url']}

def run_engine(name,binary,identity,origin):
    result={'engine':name,'build':identity,'trials':[],'classification':'NOT_RUN'}
    for trial in range(3):
        log=(OUT/f'{name}-{trial}.log').open('wb'); proc=client=sampler=None
        stop=threading.Event(); samples=[]; profile=OUT/f'profile-{name}-{trial}'; profile.mkdir(exist_ok=True); p=port()
        if name=='servo': args=[str(binary),'--headless','--webdriver',str(p),'--config-dir',str(profile),'--enable-experimental-web-platform-features','about:blank']
        elif name=='lightpanda': args=[str(binary),'serve','--host','127.0.0.1','--port',str(p)]
        else: args=[str(binary),'--headless=new','--remote-debugging-port='+str(p),'--user-data-dir='+str(profile),'--no-first-run','--no-default-browser-check','--disable-extensions','about:blank']
        t0=time.monotonic(); row={'trial':trial,'args':args}
        try:
            env=dict(os.environ,LIGHTPANDA_DISABLE_TELEMETRY='1',LIGHTPANDA_DISABLE_CORE_DUMP='1')
            proc=subprocess.Popen(args,stdout=log,stderr=subprocess.STDOUT,cwd=binary.parent,env=env)
            def sample():
                while not stop.wait(.05):
                    try:
                        root=psutil.Process(proc.pid); samples.append(sum(x.memory_info().rss for x in [root]+root.children(recursive=True) if x.is_running()))
                    except (psutil.Error,ProcessLookupError): pass
            sampler=threading.Thread(target=sample,daemon=True); sampler.start()
            if name=='servo': client=WebDriver(f'http://127.0.0.1:{p}',proc)
            else:
                deadline=time.monotonic()+30
                while True:
                    if proc.poll() is not None: raise RuntimeError('Process exited before protocol ready')
                    try:
                        endpoint=read_json(f'http://127.0.0.1:{p}/json/version')['webSocketDebuggerUrl'] if name=='chromium' else f'ws://127.0.0.1:{p}/'
                        client=CDP(endpoint); break
                    except Exception:
                        if time.monotonic()>deadline: raise
                        time.sleep(.3)
            row['startup_ms']=round((time.monotonic()-t0)*1000,1); t1=time.monotonic()
            client.visit(origin+'/comparison/fixture.html')
            row['checks']=poll(client,'window.__probe',lambda x:isinstance(x,dict) and x.get('done'),55)
            row['fixture_ms']=round((time.monotonic()-t1)*1000,1); row['userAgent']=client.evaluate('navigator.userAgent')
            row['react']=poll(client,'window.__reactResult',lambda x:isinstance(x,dict) and x.get('done'),15)
            row['fixture_peak_sum_rss_mib']=round(max(samples,default=0)/1048576,1)
            try: client.screenshot(f'{name}-{trial}.png'); row['screenshot']='CAPTURED_NOT_VISUALLY_QUALIFIED'
            except Exception as exc: row['screenshot']=str(exc)
            if trial==0:
                row['public_smoke']=[]
                for url in ['https://learn.iwxa.ru/','https://artbazi.ru/']:
                    try:
                        client.visit(url)
                        item=poll(client,'({url:location.href,title:document.title,h1:document.querySelector("h1")?.textContent,textLength:document.body?.innerText?.length})',lambda x:isinstance(x,dict) and x.get('textLength',0)>100,20)
                        row['public_smoke'].append({'requested':url,'status':'CONTENT_ONLY_NOT_APP_ACCEPTANCE','result':item})
                    except Exception as exc: row['public_smoke'].append({'requested':url,'status':'UNCONFIRMED','error':str(exc)[:600]})
            row['status']='OBSERVED'
        except Exception as exc: row.update(status='INFRA_OR_ENGINE_ERROR',error=repr(exc))
        finally:
            stop.set()
            if sampler: sampler.join(timeout=1)
            if client:
                try: client.close()
                except Exception: pass
            if proc:
                try: children=psutil.Process(proc.pid).children(recursive=True)
                except psutil.Error: children=[]
                if proc.poll() is None:
                    proc.terminate()
                    try: proc.wait(timeout=5)
                    except subprocess.TimeoutExpired: proc.kill()
                for child in children:
                    try: child.terminate()
                    except psutil.Error: pass
            log.close(); row['peak_sum_rss_mib']=round(max(samples,default=0)/1048576,1)
            row['memory_note']='summed RSS/working set; may double-count shared pages; excludes OS GPU allocations; overall includes public smoke on trial 0'
            result['trials'].append(row)
    result['classification']='OBSERVED' if any(t['status']=='OBSERVED' for t in result['trials']) else 'INFRA_OR_ENGINE_ERROR'
    return result
if __name__=='__main__':
    class Server(http.server.SimpleHTTPRequestHandler):
        def __init__(self,*args,**kwargs): super().__init__(*args,directory=str(ROOT),**kwargs)
        def log_message(self,*args): pass
    server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Server); threading.Thread(target=server.serve_forever,daemon=True).start()
    report={'platform':platform.platform(),'python':sys.version,'utc':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),'git_sha':os.environ.get('GITHUB_SHA'),'engines':[],'scope':'generic fixture + public read-only smoke, NOT T2 acceptance'}
    try:
        chrome=chrome_path()
        if chrome: report['engines'].append(run_engine('chromium',chrome,{'path':str(chrome)},f'http://127.0.0.1:{server.server_port}'))
        else: report['engines'].append({'engine':'chromium','classification':'NO_BINARY'})
        for name,acquire in [('servo',servo.acquire),('lightpanda',lightpanda)]:
            if name=='lightpanda' and sys.platform=='win32':
                report['engines'].append({'engine':name,'classification':'NO_OFFICIAL_NATIVE_WINDOWS_BINARY'}); continue
            try:
                binary,identity=acquire(); report['engines'].append(run_engine(name,binary,identity,f'http://127.0.0.1:{server.server_port}'))
            except Exception as exc: report['engines'].append({'engine':name,'classification':'ACQUISITION_FAILED','error':repr(exc)})
    finally:
        server.shutdown(); (OUT/'comparison.json').write_text(json.dumps(report,indent=2),encoding='utf-8'); print(json.dumps(report,indent=2))
    sys.exit(0 if any(x.get('classification')=='OBSERVED' for x in report['engines']) else 1)
