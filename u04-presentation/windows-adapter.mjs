import {spawn} from "node:child_process";
import {isAbsolute} from "node:path";

// A host-selected native executable; no shell and no U01 installation/connection.
export function createWindowsPresentationAdapter({observeWindow,focusWindow,platform=process.platform}={}) {
  if (typeof observeWindow !== "function" || typeof focusWindow !== "function")
    throw Error("WINDOW_PORT_REQUIRED");
  return Object.freeze({
    linkedWindow:platform==="win32",
    embeddedView:false,
    async launch({launchSpec}) {
      if(platform!=="win32") return {status:"PLATFORM_UNSUPPORTED"};
      const {executable,args=[]}=launchSpec;
      if(typeof executable!=="string"||!isAbsolute(executable)||!Array.isArray(args)||
          args.length>64 || args.some(x=>typeof x!=="string"||x.includes("\0")))
        return {status:"EXECUTABLE_INVALID"};
      try {
        const child=spawn(executable,args,{shell:false,windowsHide:false,detached:true,
          stdio:"ignore"});
        const result=await new Promise(resolve=>{
          child.once("error",()=>resolve({status:"LAUNCH_FAILED"}));
          child.once("spawn",()=>resolve({status:"LAUNCHED",pid:child.pid}));
        });
        child.unref();
        return result;
      } catch {return {status:"LAUNCH_FAILED"};}
    },
    async observe({pid}) {
      if(platform!=="win32") return {observed:false,status:"PLATFORM_UNSUPPORTED"};
      const outcome=await observeWindow(pid);
      const window=outcome?.windows?.find(w=>Number.isSafeInteger(w.handle)&&w.handle>0);
      return {observed:outcome?.observed===true&&!!window,
        windowRef:window?String(window.handle):null};
    },
    async focus({pid,windowRef}) {
      if(platform!=="win32") return {focused:false,status:"PLATFORM_UNSUPPORTED"};
      const id=Number(windowRef);
      if(!Number.isSafeInteger(id)||id<=0) return {focused:false,status:"WINDOW_INVALID"};
      return focusWindow(pid,id);
    }
  });
}
