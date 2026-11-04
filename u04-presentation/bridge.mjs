import {randomUUID} from "node:crypto";

export const SCHEMA = "IWXA_PRESENTATION_BRIDGE/1.0.0";
export const MODES = Object.freeze(["LAUNCH","FOCUS","LINKED_WINDOW","EMBEDDED_VIEW"]);
const token = v => typeof v === "string" && v.length > 0 && v.length <= 256 && !/[\u0000-\u001f]/.test(v);
const ok = (supported, reason) => ({supported, ...(supported ? {} : {reason})});
export function createPresentationBridge(adapter) {
  if (!adapter || typeof adapter.launch !== "function" || typeof adapter.observe !== "function" ||
      typeof adapter.focus !== "function") throw Error("PRESENTATION_ADAPTER_INVALID");
  const bindings = new Map();
  const view = (binding, status, extra = {}) => ({
    schema:SCHEMA, status, binding:{
      appRef:binding.appRef,hostRef:binding.hostRef,windowRef:binding.windowRef ?? null,
      bindingRef:binding.bindingRef
    },...extra
  });
  function capabilities() {
    const linked = adapter.linkedWindow === true;
    const embedded = adapter.embeddedView === true && typeof adapter.embed === "function";
    return {schema:SCHEMA, capabilities:[
      {mode:"LAUNCH",...ok(true)},
      {mode:"FOCUS",...ok(true)},
      {mode:"LINKED_WINDOW",...ok(linked,"NATIVE_WINDOW_OBSERVATION_UNAVAILABLE")},
      {mode:"EMBEDDED_VIEW",...ok(embedded,"NO_OFFICIAL_TOOLKIT_EMBED_API")}
    ]};
  }
  async function open({appRef,hostRef,launchSpec}) {
    if (!token(appRef)||!token(hostRef)||!launchSpec || typeof launchSpec !== "object" ||
        Array.isArray(launchSpec)) throw Error("PRESENTATION_BINDING_INVALID");
    // Only the owning host may approve an executable. Never infer an executable from appRef.
    const launched = await adapter.launch({appRef,hostRef,launchSpec});
    if (!launched || launched.status !== "LAUNCHED" || !Number.isSafeInteger(launched.pid) ||
        launched.pid <= 0) return {schema:SCHEMA,status:"LAUNCH_UNAVAILABLE",reason:launched?.status??"NO_PROCESS"};
    const binding = {bindingRef:randomUUID(),appRef,hostRef,pid:launched.pid,windowRef:null};
    bindings.set(binding.bindingRef,binding);
    const observed = await adapter.observe({pid:binding.pid,appRef});
    if (observed?.observed === true && token(observed.windowRef)) binding.windowRef=observed.windowRef;
    return view(binding,binding.windowRef?"WINDOW_OBSERVED":"LAUNCHED_NO_WINDOW",{
      processId:binding.pid,capabilities:capabilities().capabilities
    });
  }
  async function activate({bindingRef,hostRef}) {
    const binding=bindings.get(bindingRef);
    if (!binding || binding.hostRef !== hostRef) return {schema:SCHEMA,status:"BINDING_NOT_FOUND",focused:false};
    const observed=await adapter.observe({pid:binding.pid,appRef:binding.appRef});
    if (observed?.observed !== true || !token(observed.windowRef)) {
      binding.windowRef=null;
      return view(binding,"WINDOW_NOT_FOUND",{focused:false});
    }
    // Re-observe ownership at each activation: never use a stale HWND or guessed identifier.
    binding.windowRef=observed.windowRef;
    const focused=await adapter.focus({pid:binding.pid,windowRef:binding.windowRef});
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
  return Object.freeze({capabilities,open,activate,closeBinding,getBinding});
}
