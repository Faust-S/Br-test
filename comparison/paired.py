"""Obscura vs Gecko on the *same* hosted runner and deterministic IWXA-like UI.
Not a GUI embedding test or a test of T2 Host/Kernel. Never export private data.
"""
import base64, hashlib, http.server, io, json, os, platform, shutil, statistics, subprocess, sys, threading, time, uuid
from pathlib import Path

import psutil
from PIL import Image, ImageChops, ImageStat

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
OUT = ROOT / 'artifacts' / 'paired'
OUT.mkdir(parents=True, exist_ok=True)
sys.path.insert(0, str(HERE))
import run as old
import obscura
import gecko

PNG_MAGIC = b'\x89PNG\r\n\x1a\n'

def record(fn):
    try: return {'status':'PASS', 'value':fn()}
    except Exception as exc: return {'status':'FAIL', 'error':repr(exc)[:800]}

def wait_truth(fn, seconds=6):
    until=time.monotonic()+seconds
    last=None
    while time.monotonic()<until:
        try:
            last=fn()
            if last: return last
        except Exception as exc: last=repr(exc)
        time.sleep(.12)
    raise TimeoutError('condition unmet: '+str(last)[:300])

class Engine:
    def __init__(self,engine,binary,driver,profile,logfile):
        self.name=engine;self.binary=binary;self.driver=driver;self.profile=profile;self.logfile=logfile
        self.port=old.port();self.process=None;self.client=None;self.sid=None;self.log=None
        self.sampled=[];self.stop_samples=threading.Event();self.sampler=None
    def start(self):
        self.log=open(self.logfile,'ab');start=time.monotonic()
        if self.name=='obscura':
            args=[str(self.binary),'serve','--host','127.0.0.1','--port',str(self.port),'--max-connections','6','--storage-dir',str(self.profile),'--allow-private-network']
        else:args=[str(self.driver),'--host','127.0.0.1','--port',str(self.port)]
        self.process=subprocess.Popen(args,cwd=self.binary.parent,stdout=self.log,stderr=subprocess.STDOUT)
        def sample():
            while not self.stop_samples.wait(.06):
                try:
                    p=psutil.Process(self.process.pid)
                    self.sampled.append(sum(q.memory_info().rss for q in [p]+p.children(recursive=True) if q.is_running()))
                except (psutil.Error,ProcessLookupError):pass
        self.sampler=threading.Thread(target=sample,daemon=True);self.sampler.start()
        if self.name=='obscura':
            until=time.monotonic()+25
            while True:
                if self.process.poll() is not None: raise RuntimeError('obscura process exited')
                try:
                    self.client=old.CDP(f'ws://127.0.0.1:{self.port}/');break
                except Exception:
                    if time.monotonic()>=until:raise
                    time.sleep(.3)
        else:
            base=f'http://127.0.0.1:{self.port}'
            old.servo.wait_webdriver(base,self.process,30)
            self.sid=old.servo.wd(base,'/session',{'capabilities':{'alwaysMatch':{'browserName':'firefox','moz:firefoxOptions':{
                'binary':str(self.binary),'args':['-headless','-profile',str(self.profile)]},'acceptInsecureCerts':False}}},timeout=40)['sessionId']
        return round((time.monotonic()-start)*1000,1)
    def wd(self,route,body=None,method=None):return old.servo.wd(f'http://127.0.0.1:{self.port}',f'/session/{self.sid}{route}',body,method=method or ('GET' if body is None else 'POST'),timeout=20)
    def visit(self,url):
        if self.name=='obscura':self.client.visit(url)
        else:self.wd('/url',{'url':url})
    def eval(self,js):
        if self.name=='obscura':return self.client.evaluate(js)
        return self.wd('/execute/sync',{'script':f'return ({js});','args':[]})
    def setsize(self,width,height):
        if self.name=='obscura':
            self.client.call('Emulation.setDeviceMetricsOverride',{'width':width,'height':height,'deviceScaleFactor':1,'mobile':width<500})
        else:
            # WebDriver window dimensions include platform/browser chrome. Firefox
            # headless also clamps width to 500. Match actual inner viewport.
            self.wd('/window/rect',{'width':width,'height':height})
            inner=self.eval('({width:innerWidth,height:innerHeight})')
            if inner['height']!=height or inner['width']!=width:
                self.wd('/window/rect',{'width':max(500,width+(width-inner['width'])),'height':height+(height-inner['height'])})
        return self.eval('({width:innerWidth,height:innerHeight})')
    def scroll_top(self):
        self.eval('(window.scrollTo(0,0),true)')
        wait_truth(lambda: self.eval('window.scrollY===0'),3)

    def click(self,x,y):
        if self.name=='obscura':
            for kind in ['mousePressed','mouseReleased']:
                self.client.call('Input.dispatchMouseEvent',{'type':kind,'x':x,'y':y,'button':'left','clickCount':1})
        else:
            payload={'actions':[{'type':'pointer','id':'mouse','parameters':{'pointerType':'mouse'},'actions':[
                {'type':'pointerMove','duration':0,'origin':'viewport','x':round(x),'y':round(y)},
                {'type':'pointerDown','button':0},{'type':'pointerUp','button':0}]}]}
            self.wd('/actions',payload);self.wd('/actions',method='DELETE')
    def type_text(self,text):
        if self.name=='obscura':self.client.call('Input.insertText',{'text':text})
        else:
            found=self.wd('/element',{'using':'css selector','value':'#entry'});
            element=found.get('element-6066-11e4-a52e-4f735466cecf') or found.get('ELEMENT')
            self.wd('/element/'+str(element)+'/value',{'text':text,'value':list(text)})
    def wheel(self,delta=650):
        if self.name=='obscura':return self.client.call('Input.dispatchMouseEvent',{'type':'mouseWheel','x':200,'y':330,'deltaX':0,'deltaY':delta})
        return self.wd('/actions',{'actions':[{'type':'wheel','id':'wheel','actions':[
            {'type':'scroll','x':200,'y':330,'deltaX':0,'deltaY':delta,'duration':200,'origin':'viewport'}]}]})
    def screenshot(self,filename):
        t=time.monotonic()
        if self.name=='obscura': data=self.client.call('Page.captureScreenshot',{'format':'png'}).get('data')
        else:data=self.wd('/screenshot')
        binary=base64.b64decode(data,validate=True)
        if not binary.startswith(PNG_MAGIC):raise RuntimeError('invalid screenshot (not PNG)')
        filename.write_bytes(binary)
        with Image.open(io.BytesIO(binary)) as img: dimensions=img.size
        return {'ms':round((time.monotonic()-t)*1000,1),'bytes':len(binary),'dimensions':dimensions,'sha256':hashlib.sha256(binary).hexdigest(),'file':filename.name}
    def extratabs(self,url,n=4):
        created=[]
        if self.name=='obscura':
            for _ in range(n):created.append(self.client.call('Target.createTarget',{'url':url}).get('targetId'))
        else:
            old_handle=self.wd('/window');
            for _ in range(n):
                new=self.wd('/window/new',{'type':'tab'});
                self.wd('/window',{'handle':new['handle']});self.visit(url);created.append(new['handle'])
            self.wd('/window',{'handle':old_handle})
        return created
    def memory(self):return round(max(self.sampled or [0])/1048576,1)
    def close(self):
        self.stop_samples.set()
        if self.sampler:self.sampler.join(timeout=1)
        if self.name=='obscura' and self.client:
            try:self.client.close()
            except Exception:pass
        if self.name=='firefox' and self.sid:
            try:self.wd('',method='DELETE')
            except Exception:pass
        found=[]
        if self.process:
            try:found=psutil.Process(self.process.pid).children(recursive=True)
            except psutil.Error:pass
            if self.process.poll() is None:
                self.process.terminate()
                try:self.process.wait(timeout=7)
                except subprocess.TimeoutExpired:self.process.kill()
            for child in found:
                try:
                    if child.is_running():child.terminate()
                except psutil.Error:pass
        if self.log:self.log.close()

def center(runner, selector):
    return runner.eval('(()=>{const r=document.querySelector('+json.dumps(selector)+').getBoundingClientRect();return {x:r.left+r.width/2,y:r.top+r.height/2}})()')
def check_features(runner):return runner.eval('window.__iwxaFeatureCheck()')
def collect(engine,binary,driver,identity,host):
    id=uuid.uuid4().hex[:8];profile=OUT/f'{engine}-profile-{id}';profile.mkdir(exist_ok=True)
    result={'engine':engine,'identity':identity,'platform':platform.platform(),'testid':id,'checks':{},'screenshots':[],
            'memory_note':'summed RSS/working set from own process descendants; potential shared-page double-counting, excludes GPU; host runner is paired'}
    url=f'{host}/comparison/iwxa-ui-fixture.html'
    runner=Engine(engine,binary,driver,profile,OUT/f'{engine}-first.log')
    try:
        result['startup_ms']=record(runner.start)
        if result['startup_ms']['status']!='PASS':return result
        runner.visit(url)
        wait_truth(lambda: runner.eval('typeof window.startPersist === "function"'))
        result['checks']['viewport_desktop']=record(lambda: runner.setsize(1280,850))
        result['checks']['features_desktop']=record(lambda: check_features(runner))
        runner.scroll_top()
        result['checks']['render_desktop']=record(lambda: runner.screenshot(OUT/f'{engine}-desktop.png'))
        if result['checks']['render_desktop']['status']=='PASS':result['screenshots'].append(result['checks']['render_desktop']['value'])
        def click_button():
            pt=center(runner,'#increment');runner.click(pt['x'],pt['y'])
            return wait_truth(lambda: runner.eval('window.__iwxaUi.clicked === 1'))
        result['checks']['pointer_click']=record(click_button)
        def keyboard():
            pt=center(runner,'#entry');runner.click(pt['x'],pt['y']);runner.type_text('IWXA42')
            return wait_truth(lambda: runner.eval('window.__iwxaUi.typed === "IWXA42"'))
        result['checks']['keyboard_input']=record(keyboard)
        def scroll():
            runner.wheel();return wait_truth(lambda: runner.eval('window.scrollY > 150'))
        result['checks']['wheel_scroll']=record(scroll)
        result['checks']['render_after_input']=record(lambda:runner.screenshot(OUT/f'{engine}-interacted.png'))
        # 500px is the smallest reliable Firefox WebDriver headless viewport.
        # Identical CSS breakpoint to 390px, without an unequal screenshot canvas.
        result['checks']['viewport_mobile']=record(lambda:runner.setsize(500,844))
        runner.scroll_top()
        result['checks']['features_mobile']=record(lambda:check_features(runner))
        result['checks']['render_mobile']=record(lambda:runner.screenshot(OUT/f'{engine}-mobile.png'))
        if result['checks']['render_mobile']['status']=='PASS':result['screenshots'].append(result['checks']['render_mobile']['value'])
        def persist():
            runner.eval('(window.startPersist(),true)')
            status=wait_truth(lambda:runner.eval('window.__persist.phase === "done" && window.__persist'),12)
            if status.get('status')!='PASS':raise AssertionError(status)
            return status
        result['checks']['persist_commit_abort']=record(persist)
        def functional_gpu():
            runner.eval('''(window.__iwxaGpu={done:false},(async()=>{try{const gpu=navigator.gpu;if(!gpu?.requestAdapter){window.__iwxaGpu={done:true,status:'UNAVAILABLE'};return;}const result=await Promise.race([gpu.requestAdapter(),new Promise((_,reject)=>setTimeout(()=>reject(Error('GPU adapter timeout')),3500))]);window.__iwxaGpu={done:true,status:result?'ADAPTER':'UNAVAILABLE'}}catch(e){window.__iwxaGpu={done:true,status:'ERROR',error:String(e)}}})(),true)''')
            return wait_truth(lambda:runner.eval('window.__iwxaGpu?.done&&window.__iwxaGpu'),6)
        result['checks']['webgpu_adapter_observation']=record(functional_gpu)

        result['memory_one_tab_mib']=runner.memory()
        result['checks']['open_five_tabs']=record(lambda:runner.extratabs(url))
        time.sleep(1)
        result['memory_five_tabs_mib']=runner.memory()
    except Exception as exc:result['first_process_error']=repr(exc)
    finally:runner.close()
    runner2=Engine(engine,binary,driver,profile,OUT/f'{engine}-restart.log')
    try:
        result['restart_ms']=record(runner2.start)
        if result['restart_ms']['status']=='PASS':
            runner2.visit(url);wait_truth(lambda:runner2.eval('typeof window.startReadback === "function"'))
            def readback():
                runner2.eval('(window.startReadback(),true)')
                status=wait_truth(lambda:runner2.eval('window.__persist.phase === "done" && window.__persist'),12)
                if status.get('status')!='PASS':raise AssertionError(status)
                return status
            result['checks']['persistence_after_process_restart']=record(readback)
    except Exception as exc:result['restart_process_error']=repr(exc)
    finally:runner2.close()
    result['passed']=sum(v['status']=='PASS' for v in result['checks'].values())
    result['failed']=sum(v['status']=='FAIL' for v in result['checks'].values())
    return result

def cross_compare(report):
    engines={r['engine']:r for r in report['results']}
    geo=engines.get('firefox',{}).get('checks',{}).get('features_desktop',{}).get('value',{}).get('layout',{})
    obs=engines.get('obscura',{}).get('checks',{}).get('features_desktop',{}).get('value',{}).get('layout',{})
    if geo and obs:
        report['geometry_compare']={'columns_firefox':geo.get('columns'),'columns_obscura':obs.get('columns'),
          'width_delta':round(abs(geo['a']['w']-obs['a']['w']),2),'height_delta':round(abs(geo['b']['h']-obs['b']['h']),2)}
    samples={}
    for engine in ['firefox','obscura']:
        p=OUT/f'{engine}-desktop.png'
        if not p.exists():continue
        with Image.open(p) as img:samples[engine]=img.convert('RGB')
    if len(samples)==2:
        a,b=samples['firefox'],samples['obscura'];size=(min(a.width,b.width),min(a.height,b.height))
        hist=ImageStat.Stat(ImageChops.difference(a.crop((0,0,*size)),b.crop((0,0,*size))))
        report['render_difference']={'shared_pixels':list(size),'mean_abs_rgb':round(sum(hist.mean)/3,2),
          'note':'Raw pixel difference includes fonts/viewport/compositing and is not itself a compatibility verdict'}

def main():
    report={'kind':'IWXA_OBSCURA_GECKO_PAIRED_EVALUATION','date':time.strftime('%Y-%m-%dT%H:%M:%SZ',time.gmtime()),
       'scope':'untrusted public fixture / same runner, no IWXA private code, no T2 Host/Kernel acceptance, no embedded native shell',
       'platform':platform.platform(),'results':[],'source_sha':os.getenv('GITHUB_SHA')}
    server=None
    try:
        bin_ob,id_ob=obscura.acquire()
        drv,id_drv=gecko.acquire_driver()
        browser_candidates=[shutil.which('firefox'),r'C:\Program Files\Mozilla Firefox\firefox.exe',r'C:\Program Files (x86)\Mozilla Firefox\firefox.exe']
        ff=next((Path(v) for v in browser_candidates if v and Path(v).is_file()),None)
        if ff is None:raise RuntimeError('Firefox browser unavailable')
        class Quiet(http.server.SimpleHTTPRequestHandler):
            def __init__(self,*a,**kw):super().__init__(*a,directory=str(ROOT),**kw)
            def log_message(self,*args):pass
        server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Quiet);threading.Thread(target=server.serve_forever,daemon=True).start()
        host=f'http://127.0.0.1:{server.server_port}'
        # Both engines are tested in the same VM. Order is recorded, not randomized.
        for e,b,d,ident in [('firefox',ff,drv,{'firefox':str(ff),'geckodriver':id_drv}),('obscura',bin_ob,None,id_ob)]:
            report['results'].append(collect(e,b,d,ident,host))
        cross_compare(report)
        report['status']='COLLECTED';report['decision_gate']='NOT_QUALIFIED_FOR_GUI_OR_T2'
    except Exception as exc:report.update(status='INFRA_ERROR',error=repr(exc))
    finally:
        if server:server.shutdown()
        (OUT/'report.json').write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
        print(json.dumps({'status':report['status'],'results':[{k:v for k,v in x.items() if k in ('engine','passed','failed','memory_one_tab_mib','memory_five_tabs_mib','first_process_error','restart_process_error')} for x in report['results']]},indent=2))
    return 0 if report['status']=='COLLECTED' else 1
if __name__=='__main__':sys.exit(main())
