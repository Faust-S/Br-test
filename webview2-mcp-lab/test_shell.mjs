// Tests the trusted shell controller only. Native geometry and WebView2 are tested on Windows.
import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';

const source=readFileSync(new URL('./ui/shell.js',import.meta.url),'utf8');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function harness({deferSync=false,guestUnavailable=0,tabCount=1}={}){
  const calls=[], frames=[], nativeEvents=new Map(), resizeObservers=[], timers=[], elements=new Map(), reports=[];
  let rect={left:380,top:54,right:1000,bottom:700,width:620,height:646};
  let active=0,maxActive=0,activeTab=0;
  const pending=[];
  const el=id=>{
    if(!elements.has(id)){
      const handlers=new Map();
      elements.set(id,{value:id==='width'?'62':'',textContent:'',
        hidden:true,attributes:{},setAttribute(k,v){this.attributes[k]=v},
        addEventListener(name,cb){handlers.set(name,cb)},
        fire(name,event={preventDefault(){}}){handlers.get(name)?.(event)},
        getBoundingClientRect(){return {...rect}}});
    }
    return elements.get(id);
  };
  const body={dataset:{open:'true'},style:{setProperty(){}}};
  const document={getElementById:el,body,activeElement:null};
  const api={core:{invoke(name,args){
    calls.push({name,args:args===undefined?null:JSON.parse(JSON.stringify(args))});
    if(name==='browser_url')return Promise.resolve('http://127.0.0.1:1/form');
    if(name==='browser_tabs')return Promise.resolve([tabCount,activeTab]);
    if(name==='browser_select_tab'){activeTab=args.index;return Promise.resolve('http://127.0.0.1:1/tab-b')}
    if(name==='sync_pane'&&guestUnavailable>0){guestUnavailable--;return Promise.reject('browser_guest_unavailable')}
    if(name==='sync_pane'&&deferSync){
      active++;maxActive=Math.max(maxActive,active);
      return new Promise(resolve=>pending.push(()=>{active--;resolve()}));
    }
    return Promise.resolve();
  }},event:{listen(name,cb){nativeEvents.set(name,cb);return Promise.resolve(()=>{})}}};
  const win={__TAURI__:api,__IWXA_LAB_REPORT:(stage,message)=>reports.push({stage,message}),innerWidth:1000,innerHeight:700,addEventListener(){}};
  vm.runInNewContext(source,{window:win,document,ResizeObserver:class{
    constructor(cb){resizeObservers.push(cb)} observe(){}
  },requestAnimationFrame(cb){frames.push(cb);return frames.length},setTimeout(cb){timers.push(cb)}});
  return {calls,el,document,body,nativeEvents,pending,reports,
    get maxActive(){return maxActive},
    syncs(){return calls.filter(c=>c.name==='sync_pane').map(c=>c.args)},
    setRect(value){rect=value;resizeObservers.forEach(cb=>cb())},
    async frame(){assert.ok(frames.length);frames.shift()();await tick()},
    async timer(){assert.ok(timers.length);timers.shift()();await tick()},
    async settle(){await tick()}};
}

test('reports the actual DOM slot and viewport, not a hardcoded split',async()=>{
  const h=harness();await h.frame();
  assert.deepEqual(h.syncs()[0],{visible:true,left:380,top:54,right:1000,bottom:700,viewportWidth:1000,viewportHeight:700});
  assert.equal(h.el('address').value,'http://127.0.0.1:1/form');
});

test('hiding the preview sends visible false; reopening restores the same slot',async()=>{
  const h=harness();await h.frame();
  h.el('toggle').fire('click');await h.frame();
  assert.equal(h.body.dataset.open,'false');assert.equal(h.syncs().at(-1).visible,false);
  h.el('toggle').fire('click');await h.frame();
  assert.equal(h.body.dataset.open,'true');assert.equal(h.syncs().at(-1).visible,true);
});

test('resize while an IPC request is active applies the latest geometry serially',async()=>{
  const h=harness({deferSync:true});await h.frame();
  h.setRect({left:420,top:54,right:1000,bottom:700,width:580,height:646});await h.frame();
  h.setRect({left:510,top:54,right:1000,bottom:700,width:490,height:646});await h.frame();
  assert.equal(h.syncs().length,1);
  h.pending.shift()();await h.settle();
  assert.equal(h.syncs().length,2);assert.equal(h.syncs().at(-1).left,510);
  h.pending.shift()();await h.settle();
  assert.equal(h.maxActive,1);
});

test('zero-size native slot is hidden even when the pane is selected',async()=>{
  const h=harness();await h.frame();
  h.setRect({left:0,top:0,right:0,bottom:0,width:0,height:0});await h.frame();
  assert.equal(h.syncs().at(-1).visible,false);
});

test('address and native history buttons use the same browser command',async()=>{
  const h=harness();await h.frame();
  h.el('address').value='  http://127.0.0.1:1/second  ';
  h.el('toolbar').fire('submit');
  for(const op of ['back','forward','reload'])h.el(op).fire('click');
  await h.settle();
  assert.deepEqual(h.calls.filter(c=>c.name==='browser_command').map(c=>c.args),[
    {operation:'navigate',address:'http://127.0.0.1:1/second'},
    {operation:'back',address:null},{operation:'forward',address:null},{operation:'reload',address:null}]);
});

test('native URL changes update the trusted address except while the user edits it',async()=>{
  const h=harness();await h.frame();
  h.nativeEvents.get('lab-browser-url')({payload:'http://127.0.0.1:1/second'});
  assert.equal(h.el('address').value,'http://127.0.0.1:1/second');
  h.document.activeElement=h.el('address');h.el('address').value='editing';
  h.nativeEvents.get('lab-browser-url')({payload:'http://127.0.0.1:1/form'});
  assert.equal(h.el('address').value,'editing');
});

test('waits for the native guest before the first address lookup',async()=>{
  const h=harness({guestUnavailable:1});await h.frame();
  assert.equal(h.el('status').textContent,'Preparing browser');
  assert.equal(h.calls.filter(c=>c.name==='browser_url').length,0);
  assert.equal(h.reports.some(r=>r.stage==='native_layout_error'),false);
  await h.timer();await h.frame();
  assert.equal(h.el('status').textContent,'Browser visible');
  assert.equal(h.el('address').value,'http://127.0.0.1:1/form');
  assert.equal(h.calls.filter(c=>c.name==='browser_url').length,1);
});

test('native tab selection updates the same address and marks the active tab',async()=>{
  const h=harness({tabCount:2});await h.frame();
  assert.equal(h.el('browser-tabs').hidden,false);
  h.el('tab-b').fire('click');await h.settle();
  assert.equal(h.el('address').value,'http://127.0.0.1:1/tab-b');
  assert.equal(h.el('tab-a').attributes['aria-pressed'],'false');
  assert.equal(h.el('tab-b').attributes['aria-pressed'],'true');
  assert.deepEqual(h.calls.filter(c=>c.name==='browser_select_tab').map(c=>c.args),[{index:1}]);
});
