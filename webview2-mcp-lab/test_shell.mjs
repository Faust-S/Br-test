// Tests the trusted shell controller only. Native geometry and WebView2 are tested on Windows.
import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {readFileSync} from 'node:fs';

const source=readFileSync(new URL('./ui/shell.js',import.meta.url),'utf8');
const tick=()=>new Promise(resolve=>setImmediate(resolve));
function harness({deferSync=false,guestUnavailable=0,tabCount=1,failCreate=false}={}){
  const calls=[], frames=[], nativeEvents=new Map(), resizeObservers=[], timers=[], elements=new Map(), reports=[];
  let rect={left:380,top:90,right:1000,bottom:700,width:620,height:610};
  let active=0,maxActive=0,activeTab=0,ids=Array.from({length:tabCount},(_,i)=>i),next=tabCount;
  const urls=new Map(ids.map(id=>[id,id===0?'http://127.0.0.1:1/form':'http://127.0.0.1:1/tab-b']));
  const pending=[];
  const el=id=>{
    if(!elements.has(id)){
      const handlers=new Map();
      elements.set(id,{value:id==='width'?'62':'',textContent:'',
        hidden:true,attributes:{},disabled:false,children:[],setAttribute(k,v){this.attributes[k]=v},
        appendChild(child){this.children.push(child);elements.set(child.id,child);child.parent=this},
        remove(){this.parent.children=this.parent.children.filter(c=>c!==this);elements.delete(this.id)},
        scrollIntoView(){},blur(){document.activeElement=null},
        addEventListener(name,cb){handlers.set(name,cb)},
        fire(name,event={preventDefault(){}}){handlers.get(name)?.(event)},
        getBoundingClientRect(){return {...rect}}});
    }
    return elements.get(id);
  };
  const body={dataset:{open:'true'},style:{setProperty(){}}};
  let created=0;
  const document={getElementById:el,createElement(){return el('created-'+created++)},body,activeElement:null};
  const api={core:{invoke(name,args){
    calls.push({name,args:args===undefined?null:JSON.parse(JSON.stringify(args))});
    if(name==='browser_url')return Promise.resolve(urls.get(activeTab));
    if(name==='browser_tabs')return Promise.resolve([[...ids],activeTab]);
    if(name==='browser_select_tab'){activeTab=args.index;return Promise.resolve(urls.get(activeTab))}
    if(name==='browser_create_tab'){
      if(failCreate)return Promise.reject('native_create_failed');
      activeTab=next++;ids.push(activeTab);urls.set(activeTab,'http://127.0.0.1:1/tab-new/'+activeTab);return Promise.resolve([activeTab,urls.get(activeTab)]);
    }
    if(name==='browser_close_tab'){
      const i=ids.indexOf(args.index);ids.splice(i,1);urls.delete(args.index);activeTab=ids[Math.max(0,i-1)];return Promise.resolve([activeTab,urls.get(activeTab)]);
    }
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
  assert.deepEqual(h.syncs()[0],{visible:true,left:380,top:90,right:1000,bottom:700,viewportWidth:1000,viewportHeight:700});
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
  h.setRect({left:420,top:90,right:1000,bottom:700,width:580,height:610});await h.frame();
  h.setRect({left:510,top:90,right:1000,bottom:700,width:490,height:610});await h.frame();
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
test('address and native history buttons use the selected native index',async()=>{
  const h=harness();await h.frame();
  h.el('address').value='  http://127.0.0.1:1/second  ';
  h.el('toolbar').fire('submit');
  for(const op of ['back','forward','reload'])h.el(op).fire('click');
  await h.settle();
  assert.deepEqual(h.calls.filter(c=>c.name==='browser_command').map(c=>c.args),[
    {index:0,operation:'navigate',address:'http://127.0.0.1:1/second'},
    {index:0,operation:'back',address:null},{index:0,operation:'forward',address:null},{index:0,operation:'reload',address:null}]);
});
test('native URL changes update the trusted address except while the user edits it',async()=>{
  const h=harness();await h.frame();
  h.nativeEvents.get('lab-browser-url')({payload:[0,'http://127.0.0.1:1/second']});
  assert.equal(h.el('address').value,'http://127.0.0.1:1/second');
  h.document.activeElement=h.el('address');h.el('address').value='editing';
  h.nativeEvents.get('lab-browser-url')({payload:[0,'http://127.0.0.1:1/form']});
  assert.equal(h.el('address').value,'editing');
});
test('waits for the native guest before the first address lookup',async()=>{
  const h=harness({guestUnavailable:1});await h.frame();
  assert.equal(h.el('status').textContent,'Preparing browser');
  assert.equal(h.calls.filter(c=>c.name==='browser_url').length,0);
  await h.timer();await h.frame();
  assert.equal(h.el('status').textContent,'Browser visible');
  assert.equal(h.el('address').value,'http://127.0.0.1:1/form');
  assert.equal(h.calls.filter(c=>c.name==='browser_url').length,1);
});
test('native tab selection updates the same address and marks the active tab',async()=>{
  const h=harness({tabCount:2});await h.frame();
  assert.equal(h.el('tab-list').children.length,2);
  h.el('tab-1').fire('click');await h.settle();
  assert.equal(h.el('address').value,'http://127.0.0.1:1/tab-b');
  assert.equal(h.el('tab-0').attributes['aria-pressed'],'false');
  assert.equal(h.el('tab-1').attributes['aria-pressed'],'true');
  assert.deepEqual(h.calls.filter(c=>c.name==='browser_select_tab').map(c=>c.args),[{index:1}]);
});
test('creates and closes catalog entries without reusing a native index',async()=>{
  const h=harness({tabCount:2});await h.frame();
  h.el('tab-new').fire('click');await h.settle();
  assert.equal(h.el('tab-2').attributes['aria-pressed'],'true');
  assert.equal(h.el('address').value,'http://127.0.0.1:1/tab-new/2');
  h.el('tab-close').fire('click');await h.settle();
  assert.deepEqual(h.el('tab-list').children.map(b=>b.id),['tab-0','tab-1']);
  assert.equal(h.el('tab-1').attributes['aria-pressed'],'true');
  h.el('tab-new').fire('click');await h.settle();
  assert.deepEqual(h.el('tab-list').children.map(b=>b.id),['tab-0','tab-1','tab-3']);
});
test('ignores late URL events for an inactive or closed tab',async()=>{
  const h=harness({tabCount:2});await h.frame();
  h.el('tab-1').fire('click');await h.settle();
  h.nativeEvents.get('lab-browser-url')({payload:[0,'http://wrong']});
  assert.equal(h.el('address').value,'http://127.0.0.1:1/tab-b');
  h.el('tab-close').fire('click');await h.settle();
  h.nativeEvents.get('lab-browser-url')({payload:[1,'http://closed']});
  assert.equal(h.el('address').value,'http://127.0.0.1:1/form');
});
test('prevents duplicate creation while the first operation is in flight',async()=>{
  const h=harness();await h.frame();
  h.el('tab-new').fire('click');h.el('tab-new').fire('click');await h.settle();
  assert.equal(h.calls.filter(c=>c.name==='browser_create_tab').length,1);
  assert.equal(h.el('tab-list').children.length,2);
});
test('last tab cannot be closed through the UI',async()=>{
  const h=harness();await h.frame();
  assert.equal(h.el('tab-close').disabled,true);
  h.el('tab-close').fire('click');await h.settle();
  assert.equal(h.calls.filter(c=>c.name==='browser_close_tab').length,0);
});
test('failure reconciles native catalog without automatically retrying creation',async()=>{
  const h=harness({failCreate:true});await h.frame();
  h.el('tab-new').fire('click');await h.settle();await h.frame();
  assert.equal(h.calls.filter(c=>c.name==='browser_create_tab').length,1);
  assert.equal(h.el('tab-list').children.length,1);
  assert.equal(h.el('status').textContent,'ERROR: native_create_failed');
  assert.equal(h.el('tab-new').disabled,false);
});
