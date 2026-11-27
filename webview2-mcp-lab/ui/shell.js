'use strict';
const report=(stage,message='')=>window.__IWXA_LAB_REPORT?.(stage,message);
const api=window.__TAURI__;
if(typeof api?.core?.invoke!=='function'){
  report('bridge_missing',typeof api);
  throw new Error('Tauri native bridge unavailable in trusted shell');
}
const invoke=api.core.invoke;
const toggle=document.getElementById('toggle'), slot=document.getElementById('guest-slot');
const status=document.getElementById('status'), address=document.getElementById('address'), width=document.getElementById('width');
const list=document.getElementById('tab-list'), addTab=document.getElementById('tab-new'), closeTab=document.getElementById('tab-close');
let visible=true, running=false, dirty=false, frame=0, retries=0, addressReady=false, addressRetries=0;
let tabIds=[], activeTab=null, mutating=false, refreshSequence=0, operationError='';
const tabButtons=new Map();
function schedule(){
  dirty=true;
  if(!frame) frame=requestAnimationFrame(()=>{frame=0;void flush()});
}
async function flush(){
  if(running||mutating)return;
  running=true;
  try{
    while(dirty&&!mutating){
      dirty=false;
      const r=slot.getBoundingClientRect();
      try{
        await invoke('sync_pane',{visible:visible&&r.width>0&&r.height>0,left:r.left,top:r.top,right:r.right,bottom:r.bottom,
          viewportWidth:window.innerWidth,viewportHeight:window.innerHeight});
        retries=0;
        status.textContent=operationError||(visible?'Browser visible':'Browser hidden');
        report('native_layout_ack',String(visible));
        if(!addressReady){
          try{await refreshTabs();updateUrl(await invoke('browser_url'));addressReady=true}
          catch(error){report('browser_url_error',String(error));if(addressRetries++<10)setTimeout(schedule,250)}
        }
      }catch(error){
        const code=String(error);
        if(code==='browser_guest_unavailable'&&retries++<60){
          status.textContent='Preparing browser';
          report('native_layout_wait',code);
          setTimeout(schedule,250);
        }else{
          status.textContent='ERROR: '+code;
          report('native_layout_error',code);
          if(code!=='browser_guest_unavailable'&&retries++<10)setTimeout(schedule,100);
        }
      }
    }
  }finally{running=false;if(dirty&&!mutating)schedule()}
}
toggle.addEventListener('click',()=>{
  visible=!visible;document.body.dataset.open=String(visible);
  toggle.textContent=visible?'Hide browser pane':'Show browser pane';schedule();
});
width.addEventListener('input',()=>{
  document.body.style.setProperty('--dock-width',width.value+'%');schedule();
});
function showError(error){operationError='ERROR: '+String(error);status.textContent=operationError}
async function browser(operation,url){
  if(mutating||activeTab===null)return;
  operationError='';
  try{await invoke('browser_command',{index:activeTab,operation,address:url??null});schedule()}
  catch(error){showError(error)}
}
for(const op of ['back','forward','reload'])document.getElementById(op).addEventListener('click',()=>void browser(op));
document.getElementById('toolbar').addEventListener('submit',event=>{
  event.preventDefault();const url=address.value.trim();address.blur();void browser('navigate',url);
});
function updateUrl(url){if(document.activeElement!==address)address.value=url}
function setControls(){
  addTab.disabled=mutating||activeTab===null||tabIds.length>=12;
  closeTab.disabled=mutating||tabIds.length<2;
  tabButtons.forEach((button,id)=>{
    button.disabled=mutating;
    button.setAttribute('aria-pressed',String(id===activeTab));
  });
}
async function refreshTabs(){
  const request=++refreshSequence;
  try{
    const [ids,index]=await invoke('browser_tabs');
    if(request!==refreshSequence)return;
    if(!Array.isArray(ids)||!ids.includes(index)||new Set(ids).size!==ids.length||
       !ids.every(id=>Number.isSafeInteger(id)&&id>=0))throw new Error('invalid native tab catalog');
    activeTab=index;tabIds=ids;
    for(const [id,button] of tabButtons)if(!ids.includes(id)){button.remove();tabButtons.delete(id)}
    for(const id of ids){
      if(!tabButtons.has(id)){
        const button=document.createElement('button');
        button.type='button';button.id='tab-'+id;button.textContent='Tab '+(id+1);
        button.addEventListener('click',()=>void mutate('browser_select_tab',id));
        tabButtons.set(id,button);list.appendChild(button);
      }
    }
    setControls();schedule();
  }catch(error){report('browser_tabs_error',String(error));throw error}
}
async function mutate(command,index){
  if(mutating||activeTab===null)return;
  if(command==='browser_create_tab'&&tabIds.length>=12)return;
  if(command==='browser_close_tab'&&tabIds.length<2)return;
  if(index!==undefined&&!tabIds.includes(index))return;
  mutating=true;++refreshSequence;setControls();operationError='';
  try{
    const result=await invoke(command,index===undefined?undefined:{index});
    if(command==='browser_select_tab'){activeTab=index;address.value=result}
    else{activeTab=result[0];address.value=result[1]}
    await refreshTabs();
    address.value=await invoke('browser_url');
    tabButtons.get(activeTab)?.scrollIntoView({block:'nearest',inline:'nearest'});
  }catch(error){
    showError(error);
    try{await refreshTabs();address.value=await invoke('browser_url')}catch(recovery){report('browser_tabs_recovery_error',String(recovery))}
    report('browser_tab_mutation_error',String(error));
  }finally{mutating=false;setControls();schedule()}
}
addTab.addEventListener('click',()=>void mutate('browser_create_tab'));
closeTab.addEventListener('click',()=>void mutate('browser_close_tab',activeTab));
new ResizeObserver(schedule).observe(slot);
window.addEventListener('resize',schedule);window.addEventListener('load',schedule);schedule();
if(api.event?.listen){
  void api.event.listen('lab-browser-tabs-ready',()=>{if(!mutating)void refreshTabs().catch(()=>{})}).catch(error=>report('event_listen_error',String(error)));
  void api.event.listen('lab-browser-url',event=>{
    if(!Array.isArray(event.payload))return;
    const [id,url]=event.payload;
    if(!mutating&&id===activeTab&&typeof url==='string')updateUrl(url);
  }).catch(error=>report('event_listen_error',String(error)));
  void api.event.listen('lab-browser-error',event=>showError(event.payload)).catch(error=>report('event_listen_error',String(error)));
}
report('shell_controller_ready');
