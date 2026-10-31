"""Firefox control run; generic fixtures only, no private code/accounts."""
import hashlib, http.server, json, os, platform, shutil, subprocess, sys, tarfile, threading, time, urllib.request, zipfile
from pathlib import Path
import psutil
sys.path.insert(0,str(Path(__file__).resolve().parent))
import run as bench
OUT=bench.OUT

def acquire_driver():
    release=bench.read_json('https://api.github.com/repos/mozilla/geckodriver/releases/latest')
    suffix='win64.zip' if sys.platform=='win32' else 'linux64.tar.gz'
    asset=next(x for x in release['assets'] if x['name'].endswith(suffix))
    digest=asset.get('digest')
    if not digest or not digest.startswith('sha256:'): raise RuntimeError('Official asset digest absent; not executing')
    archive=OUT/asset['name']
    with urllib.request.urlopen(asset['browser_download_url'],timeout=90) as r,archive.open('wb') as f: shutil.copyfileobj(r,f)
    actual=hashlib.sha256(archive.read_bytes()).hexdigest()
    if actual!=digest.split(':')[1]: raise RuntimeError('Checksum mismatch')
    dest=OUT/'geckodriver';dest.mkdir(exist_ok=True)
    if sys.platform=='win32':
        with zipfile.ZipFile(archive) as z: z.extractall(dest)
    else:
        with tarfile.open(archive) as t: t.extractall(dest,filter='data')
    path=dest/('geckodriver.exe' if sys.platform=='win32' else 'geckodriver');path.chmod(0o700)
    return path,{'tag':release['tag_name'],'sha256':actual}

def main():
    report={'engine':'firefox','platform':platform.platform(),'git_sha':os.environ.get('GITHUB_SHA'),'trials':[],'scope':'generic fixture, not T2 qualification'}
    server=None
    try:
        driver,report['driver']=acquire_driver()
        candidates=[shutil.which('firefox'),r'C:\Program Files\Mozilla Firefox\firefox.exe',r'C:\Program Files (x86)\Mozilla Firefox\firefox.exe']
        binary=next((x for x in candidates if x and Path(x).is_file()),None)
        if binary is None: raise RuntimeError('Firefox binary absent')
        class Server(http.server.SimpleHTTPRequestHandler):
            def __init__(self,*args,**kwargs):super().__init__(*args,directory=str(bench.ROOT),**kwargs)
            def log_message(self,*args):pass
        server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Server);threading.Thread(target=server.serve_forever,daemon=True).start()
        for i in range(3):
            row={'trial':i};log=(OUT/f'firefox-{i}.log').open('wb'); p=bench.port();proc=None;sid=None;stop=threading.Event();samples=[];sampler=None
            endpoint=f'http://127.0.0.1:{p}';t0=time.monotonic()
            try:
                proc=subprocess.Popen([str(driver),'--host','127.0.0.1','--port',str(p)],stdout=log,stderr=subprocess.STDOUT)
                def sample():
                    while not stop.wait(.05):
                        try:
                            root=psutil.Process(proc.pid);samples.append(sum(x.memory_info().rss for x in [root]+root.children(recursive=True)))
                        except psutil.Error:pass
                sampler=threading.Thread(target=sample,daemon=True);sampler.start()
                bench.servo.wait_webdriver(endpoint,proc,25)
                session=bench.servo.wd(endpoint,'/session',{'capabilities':{'alwaysMatch':{'browserName':'firefox','moz:firefoxOptions':{'binary':binary,'args':['-headless']}}}},timeout=40)
                sid=session['sessionId'];row['capabilities']=session.get('capabilities');row['startup_ms']=round((time.monotonic()-t0)*1000,1)
                class Client:
                    def evaluate(self,expr):return bench.servo.execute(endpoint,sid,expr)
                    def visit(self,url):return bench.servo.visit(endpoint,sid,url)
                c=Client();t1=time.monotonic();c.visit(f'http://127.0.0.1:{server.server_port}/comparison/fixture.html')
                row['checks']=bench.poll(c,'window.__probe',lambda x:isinstance(x,dict) and x.get('done'),55)
                row['react']=bench.poll(c,'window.__reactResult',lambda x:isinstance(x,dict) and x.get('done'),15)
                row['fixture_ms']=round((time.monotonic()-t1)*1000,1)
                row['peak_sum_rss_mib']=round(max(samples,default=0)/1048576,1);row['status']='OBSERVED'
                row['screenshot']=bench.servo.capture(endpoint,sid,f'firefox-{i}')
            except Exception as exc:row.update(status='INFRA_OR_ENGINE_ERROR',error=repr(exc))
            finally:
                stop.set()
                if sampler:sampler.join(timeout=1)
                if sid:
                    try:bench.servo.wd(endpoint,f'/session/{sid}',method='DELETE',timeout=8)
                    except Exception:pass
                if proc:
                    try:children=psutil.Process(proc.pid).children(recursive=True)
                    except psutil.Error:children=[]
                    if proc.poll() is None:
                        proc.terminate()
                        try:proc.wait(timeout=5)
                        except subprocess.TimeoutExpired:proc.kill()
                    for child in children:
                        try:child.terminate()
                        except psutil.Error:pass
                log.close();report['trials'].append(row)
    except Exception as exc:report['error']=repr(exc)
    finally:
        if server:server.shutdown()
        report['memory_note']='summed RSS/working-set, possible shared-page double counting; no full GPU measurement; separate runner, not paired performance ranking'
        (OUT/'firefox.json').write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(report,indent=2))
    return 0 if any(x.get('status')=='OBSERVED' for x in report['trials']) else 1
if __name__=='__main__':sys.exit(main())
