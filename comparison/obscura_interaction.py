"""Public render/input continuity check, separate from full browser acceptance."""
import http.server,json,os,subprocess,sys,threading,time
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parent))
import run as bench
import obscura
OUT=bench.OUT

def main():
    report={'scope':'generic render/input continuity + read-only public homepages; NOT production/T2/GUI acceptance','git_sha':os.environ.get('GITHUB_SHA'),'checks':{},'platform':sys.platform}
    server=proc=client=log=None
    try:
        binary,report['build']=obscura.acquire()
        class Server(http.server.SimpleHTTPRequestHandler):
            def __init__(self,*args,**kwargs):super().__init__(*args,directory=str(bench.ROOT),**kwargs)
            def log_message(self,*args):pass
        server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Server);threading.Thread(target=server.serve_forever,daemon=True).start()
        p=bench.port();profile=OUT/'interaction-profile';profile.mkdir(exist_ok=True)
        log=(OUT/'obscura-interaction.log').open('wb')
        proc=subprocess.Popen([str(binary),'serve','--host','127.0.0.1','--port',str(p),'--max-connections','1','--storage-dir',str(profile),'--allow-private-network'],stdout=log,stderr=subprocess.STDOUT,cwd=binary.parent)
        deadline=time.monotonic()+25
        while True:
            if proc.poll() is not None:raise RuntimeError('Engine exited')
            try:client=bench.CDP(f'ws://127.0.0.1:{p}/');break
            except Exception:
                if time.monotonic()>deadline:raise
                time.sleep(.3)
        client.visit(f'http://127.0.0.1:{server.server_port}/comparison/fixture.html')
        report['fixture']=bench.poll(client,'window.__probe',lambda x:isinstance(x,dict) and x.get('done'),55)
        bench.poll(client,'window.__reactResult',lambda x:isinstance(x,dict) and x.get('done'),15)
        client.evaluate('window.__continuityMarker = 918273')
        client.screenshot('obscura-live-before.png')
        report['checks']['render_preserves_js']={'status':'PASS' if client.evaluate('window.__continuityMarker')==918273 else 'FAIL'}
        for selector,out in [('#native','#count'),('#react-btn','#react-value')]:
            try:
                point=client.evaluate('(()=>{const r=document.querySelector('+json.dumps(selector)+').getBoundingClientRect();return {x:r.left+r.width/2,y:r.top+r.height/2}})()')
                for kind in ['mousePressed','mouseReleased']:
                    client.call('Input.dispatchMouseEvent',{'type':kind,'x':point['x'],'y':point['y'],'button':'left','clickCount':1})
                value=bench.poll(client,'document.querySelector('+json.dumps(out)+').textContent',lambda x:x=='2',4)
                report['checks']['pointer_'+selector]={'status':'PASS','value':value,'point':point}
            except Exception as exc:report['checks']['pointer_'+selector]={'status':'FAIL','error':str(exc)}
        client.screenshot('obscura-live-after.png')
        report['checks']['same_session_after_input']={'status':'PASS' if client.evaluate('window.__continuityMarker')==918273 else 'FAIL'}
        report['public_read']=[]
        for url in ['https://learn.iwxa.ru/','https://artbazi.ru/']:
            try:
                client.visit(url)
                item=bench.poll(client,'({url:location.href,title:document.title,h1:document.querySelector("h1")?.textContent,textLength:document.body?.innerText?.length})',lambda x:isinstance(x,dict) and x.get('textLength',0)>100,25)
                report['public_read'].append({'requested':url,'status':'CONTENT_PRESENT_NOT_ACCEPTANCE','data':item})
            except Exception as exc:report['public_read'].append({'requested':url,'status':'UNCONFIRMED','error':str(exc)[:700]})
        report['status']='OBSERVED'
    except Exception as exc:report.update(status='INFRA_OR_ENGINE_ERROR',error=repr(exc))
    finally:
        if client:
            try:client.close()
            except Exception:pass
        if proc and proc.poll() is None:
            proc.terminate()
            try:proc.wait(timeout=5)
            except subprocess.TimeoutExpired:proc.kill()
        if log:log.close()
        if server:server.shutdown()
        (OUT/'obscura-interaction.json').write_text(json.dumps(report,indent=2),encoding='utf-8');print(json.dumps(report,indent=2))
    return 0 if report.get('status')=='OBSERVED' else 1
if __name__=='__main__':sys.exit(main())
