import {presentationBindingId as randomUUID} from "./id.mjs";
import {createPresentationSurfaceController} from "./surface.mjs";

export const SCHEMA = "IWXA_PRESENTATION_BRIDGE/1.0.0";
export const MODES = Object.freeze(["LAUNCH","FOCUS","LINKED_WINDOW","EMBEDDED_VIEW","SHARED_LIVE_SURFACE"]);
export const PRESENTATION_PATHS=Object.freeze(["LINKED_NATIVE_APPLICATION","EMBEDDED_APPLICATION_SURFACE","SHARED_LIVE_SURFACE"]);
const token = v => typeof v === "string" && v.length > 0 && v.length <= 256 && !/[\u0000-\u001f]/.test(v);
const ok = (supported, reason) => ({supported, ...(supported ? {} : {reason})});
export function createPresentationBridge(adapter,{surfaceProvider=null,surfaceHost=null}={}) {
  const nativeReady=!!adapter && typeof adapter.launch === "function" &&
      typeof adapter.observe === "function" && typeof adapter.focus === "function";
  if(!nativeReady && !surfaceProvider)throw Error("PRESENTATION_ADAPTER_INVALID");
  const surfaces=surfaceProvider?
      createPresentationSurfaceController(surfaceProvider,surfaceHost):null;
  const bindings = new Map();
  const view = (binding, status, extra = {}) => ({
    schema:SCHEMA, status, binding:{
      appRef:binding.appRef,hostRef:binding.hostRef,windowRef:binding.windowRef ?? null,
      bindingRef:binding.bindingRef,processId:binding.pid,
      processStamp:binding.processStamp??null
    },...extra
  });
  function capabilities() {
    const linked = nativeReady && adapter.linkedWindow === true;
    const canLaunch = nativeReady && adapter.launchAvailable !== false;
    const canFocus = nativeReady && adapter.focusAvailable !== false;
    const surfaceCaps=surfaces?.capabilities()??[];
    const embedded=surfaceCaps.find(c=>c.mode==="EMBEDDED_VIEW")?.supported===true;
    const shared=surfaceCaps.find(c=>c.mode==="SHARED_LIVE_SURFACE")?.supported===true;
    const paths=[
      {path:"LINKED_NATIVE_APPLICATION",...ok(linked&&canFocus,"NATIVE_WINDOW_OBSERVATION_UNAVAILABLE")},
      {path:"EMBEDDED_APPLICATION_SURFACE",...ok(embedded,"NO_OFFICIAL_TOOLKIT_EMBED_API")},
      {path:"SHARED_LIVE_SURFACE",...ok(shared,"SHARED_RUNTIME_SURFACE_UNAVAILABLE")}
    ];
    return {schema:SCHEMA, capabilities:[
      {mode:"LAUNCH",...ok(canLaunch,nativeReady?adapter.launchReason??"NATIVE_LAUNCH_UNAVAILABLE":"NATIVE_ADAPTER_UNAVAILABLE")},
      {mode:"FOCUS",...ok(canFocus,"NATIVE_FOCUS_UNAVAILABLE")},
      {mode:"LINKED_WINDOW",...ok(linked,"NATIVE_WINDOW_OBSERVATION_UNAVAILABLE")},
      {mode:"EMBEDDED_VIEW",...ok(embedded,"NO_OFFICIAL_TOOLKIT_EMBED_API")},
      {mode:"SHARED_LIVE_SURFACE",...ok(shared,"SHARED_RUNTIME_SURFACE_UNAVAILABLE")}
    ],paths};
  }
  async function open({appRef,hostRef,launchSpec}) {
    if (!token(appRef)||!token(hostRef)||!launchSpec || typeof launchSpec !== "object" ||
        Array.isArray(launchSpec)) throw Error("PRESENTATION_BINDING_INVALID");
    // Only the owning host may approve an executable. Never infer an executable from appRef.
    if(!nativeReady)return {schema:SCHEMA,status:"LAUNCH_UNAVAILABLE",reason:"NATIVE_ADAPTER_UNAVAILABLE"};
    let bindingRef;
    try {bindingRef=randomUUID();} catch {return {schema:SCHEMA,status:"BINDING_ID_UNAVAILABLE"};}
    let launched;
    try {launched = await adapter.launch({appRef,hostRef,launchSpec});}
    catch {return {schema:SCHEMA,status:"LAUNCH_UNAVAILABLE",reason:"NATIVE_LAUNCH_FAILED"};}
    if (!launched || launched.status !== "LAUNCHED" || !Number.isSafeInteger(launched.pid) ||
        launched.pid <= 0) return {schema:SCHEMA,status:"LAUNCH_UNAVAILABLE",reason:launched?.status??"NO_PROCESS"};
    const binding = {bindingRef,appRef,hostRef,pid:launched.pid,
      processStamp:token(launched.processStamp)?launched.processStamp:null,windowRef:null};
    bindings.set(binding.bindingRef,binding);
    let observed;
    try {observed = await adapter.observe({pid:binding.pid,appRef});}
    catch {return view(binding,"LAUNCHED_NO_WINDOW",{
      processId:binding.pid,reason:"WINDOW_OBSERVATION_FAILED",
      capabilities:capabilities().capabilities
    });}
    if (observed?.observed === true && token(observed.windowRef)) binding.windowRef=observed.windowRef;
    return view(binding,binding.windowRef?"WINDOW_OBSERVED":"LAUNCHED_NO_WINDOW",{
      processId:binding.pid,capabilities:capabilities().capabilities
    });
  }
  async function reattach({appRef,hostRef,pid,processStamp}) {
    if(!token(appRef)||!token(hostRef)||!Number.isSafeInteger(pid)||pid<=0||
       !token(processStamp))return {schema:SCHEMA,status:"PRESENTATION_BINDING_INVALID"};
    if(!nativeReady||typeof adapter.reattach!=="function")
      return {schema:SCHEMA,status:"REATTACH_UNSUPPORTED"};
    let bindingRef;
    try {bindingRef=randomUUID();} catch {return {schema:SCHEMA,status:"BINDING_ID_UNAVAILABLE"};}
    let result;
    try {result=await adapter.reattach({appRef,hostRef,pid,processStamp});}
    catch {return {schema:SCHEMA,status:"REATTACH_FAILED"};}
    if(result?.status!=="REATTACHED"||result.pid!==pid ||
       result.processStamp!==processStamp || result.spawned!==false)
      return {schema:SCHEMA,status:"PROCESS_IDENTITY_UNVERIFIED"};
    const binding={bindingRef,appRef,hostRef,pid,
      processStamp,windowRef:null};
    bindings.set(binding.bindingRef,binding);
    let observed;
    try{observed=await adapter.observe({pid,appRef,processStamp});}
    catch{return view(binding,"BOUND_NO_WINDOW",{spawned:false,
      reason:"WINDOW_OBSERVATION_FAILED"});}
    if(observed?.observed===true && token(observed.windowRef))
      binding.windowRef=observed.windowRef;
    return view(binding,binding.windowRef?"WINDOW_OBSERVED":"BOUND_NO_WINDOW",
      {spawned:false});
  }
  async function activate({bindingRef,hostRef}) {
    const binding=bindings.get(bindingRef);
    if (!binding || binding.hostRef !== hostRef) return {schema:SCHEMA,status:"BINDING_NOT_FOUND",focused:false};
    if(binding.processStamp && typeof adapter.verifyProcessIdentity==="function") {
      const proof=await (async()=>{try{return await adapter.verifyProcessIdentity({
        pid:binding.pid,processStamp:binding.processStamp});}catch{return null}})();
      if(proof?.verified!==true) return view(binding,"PROCESS_IDENTITY_CHANGED",{focused:false});
    }
    let observed;
    try {observed=await adapter.observe({pid:binding.pid,appRef:binding.appRef,
      processStamp:binding.processStamp});}
    catch {return view(binding,"WINDOW_OBSERVATION_FAILED",{focused:false});}
    if (bindings.get(bindingRef)!==binding) return {schema:SCHEMA,status:"BINDING_NOT_FOUND",focused:false};
    if (observed?.observed !== true || !token(observed.windowRef)) {
      binding.windowRef=null;
      return view(binding,"WINDOW_NOT_FOUND",{focused:false});
    }
    // Re-observe ownership at each activation: never use a stale HWND or guessed identifier.
    binding.windowRef=observed.windowRef;
    let focused;
    try {focused=await adapter.focus({pid:binding.pid,windowRef:binding.windowRef});}
    catch {return view(binding,"WINDOW_FOCUS_FAILED",{focused:false});}
    if (bindings.get(bindingRef)!==binding) return {schema:SCHEMA,status:"BINDING_NOT_FOUND",focused:false};
    return view(binding,focused?.focused===true?"WINDOW_FOCUSED":"WINDOW_FOCUS_DENIED",
      {focused:focused?.focused===true});
  }
  async function closeBinding({bindingRef,hostRef}) {
    const binding=bindings.get(bindingRef);
    if (!binding || binding.hostRef!==hostRef) return {schema:SCHEMA,status:"BINDING_NOT_FOUND"};
    bindings.delete(bindingRef);
    // Detach only. Do not terminate processes, kill windows or destroy user documents.
    return view(binding,"BINDING_CLOSED",{processTerminated:false});
  }
  function getBinding({bindingRef,hostRef}) {
    const binding=bindings.get(bindingRef);
    if (!binding || binding.hostRef!==hostRef) return {schema:SCHEMA,status:"BINDING_NOT_FOUND"};
    return view(binding,"BOUND");
  }
  const unavailable=async()=>({schema:SCHEMA,status:"SURFACE_UNSUPPORTED",
    reason:"NO_OFFICIAL_TOOLKIT_EMBED_API"});
  return Object.freeze({capabilities,open,reattach,activate,closeBinding,getBinding,
    attachSurface:surfaces?.attach??unavailable,
    observeSurface:surfaces?.observe??unavailable,
    updateViewport:surfaces?.updateViewport??unavailable,
    transferSurfaceInput:surfaces?.transferInput??unavailable,
    detachSurface:surfaces?.detach??unavailable,
    getSurface:surfaces?.get??unavailable});
}
