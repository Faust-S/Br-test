// Public qualification only. Record shell initialization errors before native cleanup.
'use strict';
if(location.protocol==='http:'&&location.hostname==='127.0.0.1'&&location.pathname==='/desktop/index.html'){
  const report=(stage,message='')=>{
    const data=JSON.stringify({stage:String(stage).slice(0,100),message:String(message).slice(0,2000)});
    void fetch('/desktop/probe',{method:'POST',headers:{'Content-Type':'application/json'},body:data,keepalive:true}).catch(()=>{});
  };
  Object.defineProperty(window,'__IWXA_LAB_REPORT',{value:report});
  window.addEventListener('error',event=>report('script_error',event.message||'resource error'));
  window.addEventListener('unhandledrejection',event=>report('unhandled_rejection',String(event.reason)));
  document.addEventListener('securitypolicyviolation',event=>report('csp_violation',event.violatedDirective+' '+event.blockedURI));
  document.addEventListener('DOMContentLoaded',()=>report('dom_ready',typeof window.__TAURI__));
  report('probe_loaded',typeof window.__TAURI__);
}
