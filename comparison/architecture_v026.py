"""Public, reproducible IWXA engine study: Obscura 0.2.6 paired with Firefox.
No private IWXA source. The shared HTML fixture is NOT T2 acceptance.
"""
import hashlib, http.server, json, os, platform, shutil, statistics, sys
import tarfile, threading, time, urllib.request, zipfile
from pathlib import Path
import psutil
sys.path.insert(0,str(Path(__file__).resolve().parent))
import paired, obscura, gecko

OUT=paired.ROOT/'artifacts'/'architecture-v026'
OUT.mkdir(parents=True,exist_ok=True)
CACHE=None

def acquire_v026():
    global CACHE
    if CACHE: return CACHE
    tag='v0.2.6'
    obj=paired.old.read_json('https://api.github.com/repos/h4ckf0r0day/obscura/releases/tags/'+tag)
    filename='obscura-x86_64-windows.zip' if sys.platform=='win32' else 'obscura-x86_64-linux.tar.gz'
    asset=next(a for a in obj['assets'] if a['name']==filename)
    expected=asset.get('digest','')
    if not expected.startswith('sha256:'):raise RuntimeError('Missing official digest')
    archive=OUT/filename
    with urllib.request.urlopen(asset['browser_download_url'],timeout=120) as r,archive.open('wb') as f:shutil.copyfileobj(r,f)
    sha=hashlib.sha256(archive.read_bytes()).hexdigest()
    if sha!=expected.split(':')[1]:raise RuntimeError('Official asset hash mismatch')
    dest=OUT/'obscura-bin'
    dest.mkdir(exist_ok=True)
    if filename.endswith('.zip'):
        with zipfile.ZipFile(archive) as z:z.extractall(dest)
    else:
        with tarfile.open(archive) as t:t.extractall(dest,filter='data')
    executable=next(p for p in dest.rglob('obscura.exe' if sys.platform=='win32' else 'obscura') if p.is_file())
    executable.chmod(0o700)
    CACHE=(executable,{'tag':tag,'asset':filename,'sha256':sha})
    return CACHE

def current_mib(e):
    if e.process is None or e.process.poll() is not None:return 0.0
    try:
        p=psutil.Process(e.process.pid)
        return sum(x.memory_info().rss for x in [p]+p.children(recursive=True) if x.is_running())/1048576
    except psutil.Error:return 0.0

def sample_memory(ob,ff=None):
    values=[]
    for _ in range(8):
        o=current_mib(ob);g=current_mib(ff) if ff else 0.
        values.append({'obscura_mib':round(o,2),'gecko_mib':round(g,2),'sum_mib':round(o+g,2)})
        time.sleep(.15)
    return {'median_obscura_mib':round(statistics.median(x['obscura_mib'] for x in values),2),
            'median_gecko_mib':round(statistics.median(x['gecko_mib'] for x in values),2),
            'median_sum_mib':round(statistics.median(x['sum_mib'] for x in values),2),
            'samples':values}

def dual_cost():
    ob_bin,identity=acquire_v026()
    driver,driver_id=gecko.acquire_driver()
    candidates=[shutil.which('firefox'),r'C:\Program Files\Mozilla Firefox\firefox.exe',r'C:\Program Files (x86)\Mozilla Firefox\firefox.exe']
    firefox=next((Path(p) for p in candidates if p and Path(p).is_file()),None)
    if not firefox:raise RuntimeError('Firefox binary not installed')
    class Quiet(http.server.SimpleHTTPRequestHandler):
        def __init__(self,*a,**kw):super().__init__(*a,directory=str(paired.ROOT),**kw)
        def log_message(self,*a):pass
    server=http.server.ThreadingHTTPServer(('127.0.0.1',0),Quiet)
    threading.Thread(target=server.serve_forever,daemon=True).start()
    ob=paired.Engine('obscura',ob_bin,None,OUT/'obscura-profile',OUT/'obscura.log')
    ff=paired.Engine('firefox',firefox,driver,OUT/'gecko-profile',OUT/'gecko.log')
    ob.profile.mkdir(exist_ok=True);ff.profile.mkdir(exist_ok=True)
    report={'status':'NOT_RUN','engines':['obscura','gecko'],'identity':identity,
            'scope':'public local fixture; same runner, unrelated browser profiles'}
    url='http://127.0.0.1:'+str(server.server_port)+'/comparison/iwxa-ui-fixture.html'
    try:
        report['obscura_launch_ms']=ob.start()
        ob.visit(url)
        paired.wait_truth(lambda:ob.eval('document.readyState === "complete"'),15)
        report['obscura_cookie']=ob.eval('(()=>{localStorage.setItem("iwxa_architecture_test","obscura");window.__iwxa_session_marker=273511;return "ok"})()')
        report['single']=sample_memory(ob)
        report['gecko_launch_ms']=ff.start()
        ff.visit(url)
        paired.wait_truth(lambda:ff.eval('document.readyState === "complete"'),15)
        report['gecko_sees_obscura_storage']=ff.eval('localStorage.getItem("iwxa_architecture_test")')
        report['gecko_own_storage']=ff.eval('(()=>{localStorage.setItem("iwxa_architecture_test","gecko");return localStorage.getItem("iwxa_architecture_test")})()')
        report['obscura_storage_unchanged']=ob.eval('localStorage.getItem("iwxa_architecture_test")')
        report['both']=sample_memory(ob,ff)
        ff.close()
        report['session_after_gecko_close']=ob.eval('window.__iwxa_session_marker')
        report['after_gecko_close']=sample_memory(ob)
        correct=(report['gecko_sees_obscura_storage'] is None
                 and report['gecko_own_storage']=='gecko'
                 and report['obscura_storage_unchanged']=='obscura'
                 and report['session_after_gecko_close']==273511
                 and report['both']['median_gecko_mib']>0)
        report['status']='PASS' if correct else 'FAIL'
    except Exception as e:report.update(status='ERROR',error=repr(e))
    finally:
        try:ff.close()
        except Exception as e:report['gecko_close_error']=repr(e)
        try:ob.close()
        except Exception as e:report['obscura_close_error']=repr(e)
        server.shutdown()
    (OUT/'dual-cost.json').write_text(json.dumps(report,indent=2),encoding='utf-8')
    return report

def main():
    obscura.acquire=acquire_v026
    rc=paired.main()
    base=json.loads((paired.OUT/'report.json').read_text(encoding='utf-8'))
    required=['pointer_click','keyboard_input','wheel_scroll','render_desktop',
              'render_mobile','persist_commit_abort','open_five_tabs']
    summary=[]
    for engine in base.get('results',[]):
        row={'engine':engine['engine'],'memory_one_mib':engine.get('memory_one_tab_mib'),
             'memory_five_mib':engine.get('memory_five_tabs_mib'),
             'restart_persistence':engine.get('checks',{}).get('persistence_after_process_restart'),
             'checks':{k:engine.get('checks',{}).get(k,{}).get('status','MISSING') for k in required},
             'errors':[engine.get('first_process_error'),engine.get('restart_process_error')]}
        summary.append(row)
    dual=dual_cost()
    report={'platform':platform.platform(),'sha':os.environ.get('GITHUB_SHA'),
            'versions':{'obscura':'0.2.6','gecko':'installed Firefox'},
            'paired_collected':rc==0,'functional_summary':summary,'dual_cost':dual,
            'not_qualified':['Tauri UI','T2 Kernel','hostile site OS sandbox','WebGPU real GPU','human UX study']}
    good=rc==0 and dual.get('status')=='PASS' and all(
        all(v=='PASS' for v in row['checks'].values()) for row in summary
    ) and len(summary)==2
    report['status']='PASS_PUBLIC_ENGINE_STUDY' if good else 'FAIL_OR_INCOMPLETE'
    (OUT/'summary.json').write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps({'status':report['status'],'functional_summary':summary,
                      'dual_memory':{k:dual.get(k) for k in ['single','both','after_gecko_close']}},indent=2))
    return 0 if good else 1

if __name__=='__main__':sys.exit(main())
