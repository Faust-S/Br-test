'use strict';
const api=window.__TAURI__, invoke=api.core.invoke;
const toggle=document.getElementById('toggle'), slot=document.getElementById('guest-slot');
const status=document.getElementById('status'), address=document.getElementById('address'), width=document.getElementById('width');
let visible=true, running=false, dirty=false, frame=0, retries=0;
function schedule(){
  dirty=true;
  if(!frame) frame=requestAnimationFrame(()=>{frame=0;void flush()});
}
async function flush(){
  if(running)return;
  running=true;
  try{
    while(dirty){
      dirty=false;
      const r=slot.getBoundingClientRect();
      try{
        await invoke('sync_pane',{visible:visible&&r.width>0&&r.height>0,left:r.left,top:r.top,right:r.right,bottom:r.bottom,
          viewportWidth:window.innerWidth,viewportHeight:window.innerHeight});
        retries=0;
        status.textContent=visible?'Browser visible':'Browser hidden';
      }catch(error){
        status.textContent='ERROR: '+String(error);
        if(retries++<10)setTimeout(schedule,100);
      }
    }
  }finally{running=false;if(dirty)schedule()}
}
toggle.addEventListener('click',()=>{
  visible=!visible;document.body.dataset.open=String(visible);
  toggle.textContent=visible?'Hide browser pane':'Show browser pane';schedule();
});
width.addEventListener('input',()=>{
  document.body.style.setProperty('--dock-width',width.value+'%');schedule();
});
async function browser(operation,url){
  try{await invoke('browser_command',{operation,address:url??null})}
  catch(error){status.textContent='ERROR: '+String(error)}
}
for(const op of ['back','forward','reload'])document.getElementById(op).addEventListener('click',()=>void browser(op));
document.getElementById('toolbar').addEventListener('submit',event=>{event.preventDefault();void browser('navigate',address.value.trim())});
function updateUrl(url){if(document.activeElement!==address)address.value=url}
void api.event.listen('lab-browser-url',event=>updateUrl(event.payload));
void api.event.listen('lab-browser-error',event=>{status.textContent='ERROR: '+String(event.payload)});
void invoke('browser_url').then(updateUrl).catch(()=>{});
new ResizeObserver(schedule).observe(slot);
window.addEventListener('resize',schedule);window.addEventListener('load',schedule);schedule();
