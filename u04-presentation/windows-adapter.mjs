import {spawn} from "node:child_process";
import {isAbsolute} from "node:path";

// A host-selected native executable; no shell and no U01 installation/connection.
export function createWindowsPresentationAdapter({
  observeWindow,focusWindow,resolveApprovedProgram,approveExistingProcess,
  verifyNativeProcessIdentity,captureProcessStamp,platform=process.platform
}={}) {
  if (typeof observeWindow !== "function" || typeof focusWindow !== "function")
    throw Error("WINDOW_PORT_REQUIRED");
  return Object.freeze({
    linkedWindow:platform==="win32",
    launchAvailable:platform==="win32"&&typeof resolveApprovedProgram==="function",
    launchReason:platform!=="win32"?"PLATFORM_UNSUPPORTED":"HOST_APPROVAL_REQUIRED",
    focusAvailable:platform==="win32",
    reattachAvailable:platform==="win32" &&
      typeof approveExistingProcess==="function" &&
      typeof verifyNativeProcessIdentity==="function",
    embeddedView:false,
    async launch({appRef,hostRef,launchSpec}) {
      if(platform!=="win32") return {status:"PLATFORM_UNSUPPORTED"};
      if(typeof resolveApprovedProgram!=="function") return {status:"HOST_APPROVAL_REQUIRED"};
      let approved;
      try {approved=await resolveApprovedProgram({appRef,hostRef,launchSpec});}
      catch {return {status:"HOST_APPROVAL_FAILED"};}
      if(approved?.approved!==true) return {status:"HOST_APPROVAL_REQUIRED"};
      // The host resolves an approved program. Never execute caller-provided paths.
      const {executable,args=[]}=approved;
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
        if (result.status==="LAUNCHED" && typeof captureProcessStamp==="function") {
          try {
            const stamp=await captureProcessStamp(result.pid);
            if(typeof stamp==="string" && stamp.length>0 && stamp.length<=256)
              return {...result,processStamp:stamp};
          } catch {/* PID observation is optional, not a fabricated identity. */}
        }
        return result;
      } catch {return {status:"LAUNCH_FAILED"};}
    },
    async verifyProcessIdentity({pid,processStamp}) {
      if(platform!=="win32" || typeof verifyNativeProcessIdentity!=="function")
        return {verified:false,status:"PROCESS_IDENTITY_UNAVAILABLE"};
      if(!Number.isSafeInteger(pid)||pid<=0||typeof processStamp!=="string"||
         !processStamp || processStamp.length>256)
        return {verified:false,status:"PROCESS_IDENTITY_INVALID"};
      try {
        const observation=await verifyNativeProcessIdentity({pid,processStamp});
        return {verified:observation?.verified===true};
      } catch {return {verified:false,status:"PROCESS_IDENTITY_UNAVAILABLE"};}
    },
    async reattach({appRef,hostRef,pid,processStamp}) {
      if(platform!=="win32")return {status:"PLATFORM_UNSUPPORTED"};
      if(typeof approveExistingProcess!=="function" ||
         typeof verifyNativeProcessIdentity!=="function")
        return {status:"REATTACH_UNSUPPORTED"};
      if(!Number.isSafeInteger(pid)||pid<=0||typeof processStamp!=="string"||
         !processStamp||processStamp.length>256)return {status:"PROCESS_IDENTITY_INVALID"};
      try {
        const approved=await approveExistingProcess({appRef,hostRef,pid,processStamp});
        if(approved?.approved!==true||approved.pid!==pid||
           approved.processStamp!==processStamp)
          return {status:"HOST_APPROVAL_REQUIRED"};
        const native=await verifyNativeProcessIdentity({pid,processStamp});
        if(native?.verified!==true)return {status:"PROCESS_IDENTITY_CHANGED"};
        return {status:"REATTACHED",pid,processStamp,spawned:false};
      } catch {return {status:"REATTACH_FAILED"};}
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
