"""Additional public candidate, same fixture. Not full application acceptance."""
import hashlib,http.server,json,os,platform,shutil,subprocess,sys,tarfile,threading,time,urllib.request,zipfile
from pathlib import Path
import psutil
sys.path.insert(0,str(Path(__file__).resolve().parent))
import run as bench
OUT=bench.OUT

def acquire():
    release=bench.read_json('https://api.github.com/repos/h4ckf0r0day/obscura/releases/tags/v0.2.5')
    suffix='windows.zip' if sys.platform=='win32' else 'linux.tar.gz'
    choices=[x for x in release['assets'] if 'x86_64' in x['name'] and x['name'].endswith(suffix) and 'stealth' not in x['name'] and 'no-render' not in x['name']]
    if len(choices)!=1:raise RuntimeError('No unique native render archive: '+str([x['name'] for x in release['assets']]))
    a=choices[0];digest=a.get('digest')
    if not digest or not digest.startswith('sha256:'):raise RuntimeError('Official checksum absent')
    archive=OUT/a['name']
    with urllib.request.urlopen(a['browser_download_url'],timeout=120) as r,archive.open('wb') as f:shutil.copyfileobj(r,f)
    actual=hashlib.sha256(archive.read_bytes()).hexdigest()
    if actual!=digest.split(':')[1]:raise RuntimeError('Checksum mismatch')
    dest=OUT/'obscura';dest.mkdir(exist_ok=True)
    if sys.platform=='win32':
        with zipfile.ZipFile(archive) as z:z.extractall(dest)
    else:
        with tarfile.open(archive) as t:t.extractall(dest,filter='data')
    binary=next(dest.rglob('obscura.exe' if sys.platform=='win32' else 'obscura'))
    if binary.is_dir():binary=next(p for p in dest.rglob('obscura') if p.is_file())
    binary.chmod(0o700)
    return binary,{'tag':'v0.2.5','asset':a['name'],'sha256':actual,'source':a['browser_download_url']}

def main():
    report={'engine':'obscura','platform':platform.platform(),'source_commit':os.environ.get('GITHUB_SHA'),'trials':[],'scope':'generic fixture; render build without stealth; NOT T2 qualification'}
    server=None
    try:
        binary,report['build']=acquire()
        help_=subprocess.run([str(binary),'serve','--help'],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=15)
        (OUT/'obscura-help.log').write_text(help_.stdout+help_.stderr,encoding='utf-8')
        class Server(http.server.SimpleHTTPRequestHandler):
            def __init__(self,*args,**kwargs):super().__init__(*args,directory=str(bench.ROOT),**kwargs)
            def log_message(self,*args):pass
        server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Server);threading.Thread(target=server.serve_forever,daemon=True).start()
        for i in range(3):
            port=bench.port();args=[str(binary),'serve','--port',str(port),'--allow-private-network']
            if '--host' in help_.stdout:args+=['--host','127.0.0.1']
            row={'trial':i,'args':args};log=(OUT/f'obscura-{i}.log').open('wb');proc=None;client=None;stop=threading.Event();samples=[];sampler=None;t0=time.monotonic()
            try:
                proc=subprocess.Popen(args,cwd=binary.parent,stdout=log,stderr=subprocess.STDOUT)
                def sample():
                    while not stop.wait(.05):
                        try:
                            root=psutil.Process(proc.pid);samples.append(sum(x.memory_info().rss for x in [root]+root.children(recursive=True)))
                        except psutil.Error:pass
                sampler=threading.Thread(target=sample,daemon=True);sampler.start();deadline=time.monotonic()+30
                while True:
                    if proc.poll() is not None:raise RuntimeError('Engine exited before CDP ready')
                    try:
                        client=bench.CDP(f'ws://127.0.0.1:{port}/');break
                    except Exception:
                        if time.monotonic()>deadline:raise
                        time.sleep(.3)
                row['startup_ms']=round((time.monotonic()-t0)*1000,1);t1=time.monotonic()
                client.visit(f'http://127.0.0.1:{server.server_port}/comparison/fixture.html')
                row['checks']=bench.poll(client,'window.__probe',lambda x:isinstance(x,dict) and x.get('done'),55)
                row['react']=bench.poll(client,'window.__reactResult',lambda x:isinstance(x,dict) and x.get('done'),15)
                row['fixture_ms']=round((time.monotonic()-t1)*1000,1)
                row['fixture_peak_sum_rss_mib']=round(max(samples,default=0)/1048576,1)
                row['userAgent']=client.evaluate('navigator.userAgent')
                try:client.screenshot(f'obscura-{i}.png');row['screenshot']='CAPTURED_NOT_VISUALLY_QUALIFIED'
                except Exception as exc:row['screenshot']=str(exc)
                row['status']='OBSERVED'
            except Exception as exc:row.update(status='INFRA_OR_ENGINE_ERROR',error=repr(exc))
            finally:
                stop.set()
                if sampler:sampler.join(timeout=1)
                if client:
                    try:client.close()
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
        report['memory_note']='sum RSS/working set, shared-page duplication possible, GPU incomplete; separate runner, no hardware-normalized ranking'
        (OUT/'obscura.json').write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(report,indent=2))
    return 0 if any(x.get('status')=='OBSERVED' for x in report['trials']) else 1
if __name__=='__main__':sys.exit(main())
