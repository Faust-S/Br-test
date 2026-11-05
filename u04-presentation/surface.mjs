// U04 host-neutral presentation of a vendor-provided existing live surface.
// The provider owns its runtime, session and exclusive input arbitration.
// U04 neither creates a second program runtime nor mounts an unapproved HWND.
import {randomUUID} from "node:crypto";

export const SURFACE_SCHEMA = "IWXA_PRESENTATION_SURFACE/1.0.0";
export const SURFACE_MODES = Object.freeze(["EMBEDDED_VIEW", "SHARED_LIVE_SURFACE"]);
const name = value => typeof value === "string" && value.length > 0 &&
  value.length <= 256 && !/[\u0000-\u001f\u007f]/u.test(value);
const fail = (status, reason) => ({schema:SURFACE_SCHEMA,status,...(reason?{reason}:{})});
const same = (a,b) => a.runtimeRef === b.runtimeRef &&
  a.sessionRef === b.sessionRef && a.surfaceRef === b.surfaceRef;
const call = async (fn,payload) => {
  try {return await fn(payload);} catch {return null;}
};
const viewportOK = v => v && typeof v === "object" && !Array.isArray(v) &&
  Object.keys(v).sort().join(",") === "deviceScaleFactor,height,width" &&
  Number.isInteger(v.width) && v.width >= 64 && v.width <= 8192 &&
  Number.isInteger(v.height) && v.height >= 64 && v.height <= 8192 &&
  typeof v.deviceScaleFactor === "number" && Number.isFinite(v.deviceScaleFactor) &&
  v.deviceScaleFactor >= .5 && v.deviceScaleFactor <= 4;

function presentationOK(p) {
  if (!p || typeof p !== "object" || Array.isArray(p)) return false;
  if (p.kind === "NATIVE_VIEW")
    return name(p.viewRef) && name(p.toolkit) && p.official === true;
  if (p.kind !== "WEB_URL" || typeof p.url !== "string" ||
      typeof p.origin !== "string" || p.url.length > 2048) return false;
  try {
    const u = new URL(p.url);
    const loopback = ["localhost","127.0.0.1","[::1]"].includes(u.hostname);
    return !u.username && !u.password && !u.hash && u.origin === p.origin &&
      (u.protocol === "https:" || (u.protocol === "http:" && loopback)) &&
      p.official === true;
  } catch {return false;}
}

const requiredProvider = [
  "attachSurface","observeSurface","resizeSurface",
  "transferSurfaceInput","detachSurface"
];
const requiredHost = ["mountSurface","resizeSurface","unmountSurface"];

export function createPresentationSurfaceController(provider, host) {
  if (!provider || !host ||
      !requiredProvider.every(k=>typeof provider[k] === "function") ||
      !requiredHost.every(k=>typeof host[k] === "function")) {
    throw new TypeError("SURFACE_PORT_INVALID");
  }
  const bindings = new Map();
  const supported = mode => provider.official === true &&
    host.officialPlacement === true &&
    (mode === "EMBEDDED_VIEW" ? provider.embeddedView === true :
     mode === "SHARED_LIVE_SURFACE" && provider.sharedLiveSurface === true);
  const capabilities = () => SURFACE_MODES.map(mode=>({
    mode, supported:supported(mode),
    ...(!supported(mode)?{reason:mode==="EMBEDDED_VIEW"?
      "NO_OFFICIAL_TOOLKIT_EMBED_API":"SHARED_RUNTIME_SURFACE_UNAVAILABLE"}:{})
  }));
  const publicBinding = b => ({
    schema:SURFACE_SCHEMA,mode:b.mode,appRef:b.appRef,hostRef:b.hostRef,
    bindingRef:b.bindingRef,runtimeRef:b.runtimeRef,sessionRef:b.sessionRef,
    surfaceRef:b.surfaceRef,mountRef:b.mountRef,
    presentation:structuredClone(b.presentation),
    official:true,verified:b.live,existingRuntime:true,spawned:false,mounted:b.mounted
  });
  function get({bindingRef,hostRef}) {
    const b=bindings.get(bindingRef);
    return b && b.hostRef === hostRef ? {status:"BOUND",...publicBinding(b)} :
      fail("BINDING_NOT_FOUND");
  }
  async function observeActive(b) {
    const o=await call(provider.observeSurface.bind(provider),{
      connectionRef:b.connectionRef,appRef:b.appRef,hostRef:b.hostRef,
      runtimeRef:b.runtimeRef,sessionRef:b.sessionRef,surfaceRef:b.surfaceRef
    });
    if (!o || o.status !== "LIVE" || o.verified !== true) {
      b.live=false;
      return fail("SURFACE_LOST","PROVIDER_LIVE_OBSERVATION_UNAVAILABLE");
    }
    if (!same(o,b)) {
      b.live=false;
      return fail("SURFACE_IDENTITY_CHANGED");
    }
    b.live=true;
    return {schema:SURFACE_SCHEMA,status:"SURFACE_LIVE",
      binding:publicBinding(b),inputOwnerRef:name(o.inputOwnerRef)?o.inputOwnerRef:null,
      inputEpoch:Number.isSafeInteger(o.inputEpoch)&&o.inputEpoch>=0?o.inputEpoch:null};
  }
  async function attach({appRef,hostRef,mode,surfaceRef=null,sessionRef=null}={}) {
    if (!name(appRef)||!name(hostRef)||!SURFACE_MODES.includes(mode) ||
        (surfaceRef!==null&&!name(surfaceRef)) ||
        (sessionRef!==null&&!name(sessionRef))) return fail("SURFACE_REQUEST_INVALID");
    if (!supported(mode)) return fail("SURFACE_UNSUPPORTED",capabilities().find(c=>c.mode===mode)?.reason);
    const receipt=await call(provider.attachSurface.bind(provider),{
      appRef,hostRef,mode,surfaceRef,sessionRef
    });
    if (!receipt || receipt.status!=="ATTACHED" ||
        receipt.official!==true || receipt.verified!==true ||
        receipt.existingRuntime!==true || receipt.spawned!==false ||
        receipt.mode!==mode || !name(receipt.connectionRef) ||
        !name(receipt.runtimeRef) || !name(receipt.sessionRef) ||
        !name(receipt.surfaceRef) || !presentationOK(receipt.presentation) ||
        (sessionRef!==null && receipt.sessionRef!==sessionRef) ||
        (surfaceRef!==null && receipt.surfaceRef!==surfaceRef)) {
      // Invalid or fabricated attachment is NOT an embedded I4 verification.
      if (name(receipt?.connectionRef)) await call(provider.detachSurface.bind(provider),{
        connectionRef:receipt.connectionRef,hostRef,reason:"UNVERIFIED_ATTACHMENT"
      });
      return fail("SURFACE_PROOF_REJECTED");
    }
    const b={bindingRef:randomUUID(),connectionRef:receipt.connectionRef,
      appRef,hostRef,mode,runtimeRef:receipt.runtimeRef,
      sessionRef:receipt.sessionRef,surfaceRef:receipt.surfaceRef,
      presentation:structuredClone(receipt.presentation),mountRef:null,
      mounted:false,live:false};
    const observed=await observeActive(b);
    if(observed.status!=="SURFACE_LIVE") {
      await call(provider.detachSurface.bind(provider),{
        connectionRef:b.connectionRef,hostRef,reason:observed.status
      });
      return observed;
    }
    const mounted=await call(host.mountSurface.bind(host),{
      bindingRef:b.bindingRef,hostRef,mode,
      presentation:structuredClone(b.presentation),
      runtimeRef:b.runtimeRef,sessionRef:b.sessionRef,surfaceRef:b.surfaceRef
    });
    if(mounted?.mounted!==true || !name(mounted.mountRef)) {
      await call(provider.detachSurface.bind(provider),{
        connectionRef:b.connectionRef,hostRef,reason:"MOUNT_FAILED"
      });
      return fail("SURFACE_MOUNT_FAILED");
    }
    b.mountRef=mounted.mountRef;
    b.mounted=true;
    bindings.set(b.bindingRef,b);
    return {schema:SURFACE_SCHEMA,status:"SURFACE_MOUNTED",
      binding:publicBinding(b),inputOwnerRef:observed.inputOwnerRef,
      inputEpoch:observed.inputEpoch};
  }
  async function observe({bindingRef,hostRef}) {
    const b=bindings.get(bindingRef);
    if(!b || b.hostRef!==hostRef) return fail("BINDING_NOT_FOUND");
    return observeActive(b);
  }
  async function updateViewport({bindingRef,hostRef,viewport}) {
    const b=bindings.get(bindingRef);
    if(!b || b.hostRef!==hostRef) return fail("BINDING_NOT_FOUND");
    if(!viewportOK(viewport)) return fail("VIEWPORT_INVALID");
    const live=await observeActive(b);
    if(live.status!=="SURFACE_LIVE") return live;
    const approved=await call(provider.resizeSurface.bind(provider),{
      connectionRef:b.connectionRef,hostRef,sessionRef:b.sessionRef,
      runtimeRef:b.runtimeRef,surfaceRef:b.surfaceRef,viewport:{...viewport}
    });
    if(approved?.status!=="VIEWPORT_APPLIED" ||
       !viewportOK(approved.viewport) ||
       JSON.stringify(approved.viewport)!==JSON.stringify(viewport))
      return fail("VIEWPORT_PROVIDER_UNCONFIRMED");
    const placed=await call(host.resizeSurface.bind(host),{
      bindingRef:b.bindingRef,hostRef,mountRef:b.mountRef,viewport:{...viewport}
    });
    if(placed?.resized!==true) return fail("VIEWPORT_HOST_UNCONFIRMED");
    return {schema:SURFACE_SCHEMA,status:"VIEWPORT_APPLIED",
      bindingRef,viewport:{...viewport},runtimeRef:b.runtimeRef,
      sessionRef:b.sessionRef};
  }
  async function transferInput({bindingRef,hostRef,toOwnerRef,expectedEpoch}) {
    const b=bindings.get(bindingRef);
    if(!b || b.hostRef!==hostRef) return fail("BINDING_NOT_FOUND");
    if(!name(toOwnerRef) || !Number.isSafeInteger(expectedEpoch) ||
       expectedEpoch<0) return fail("INPUT_REQUEST_INVALID");
    const live=await observeActive(b);
    if(live.status!=="SURFACE_LIVE") return live;
    const result=await call(provider.transferSurfaceInput.bind(provider),{
      connectionRef:b.connectionRef,hostRef,sessionRef:b.sessionRef,
      runtimeRef:b.runtimeRef,surfaceRef:b.surfaceRef,
      toOwnerRef,expectedEpoch
    });
    if(result?.status==="INPUT_DENIED") return {
      schema:SURFACE_SCHEMA,status:"INPUT_DENIED",inputEpoch:result.inputEpoch??null,
      inputOwnerRef:result.inputOwnerRef??null};
    if(result?.status!=="INPUT_TRANSFERRED" ||
       result.inputOwnerRef!==toOwnerRef ||
       result.inputEpoch!==expectedEpoch+1) return fail("INPUT_TRANSFER_UNVERIFIED");
    return {schema:SURFACE_SCHEMA,status:"INPUT_TRANSFERRED",
      bindingRef, inputOwnerRef:toOwnerRef,inputEpoch:result.inputEpoch};
  }
  async function detach({bindingRef,hostRef}) {
    const b=bindings.get(bindingRef);
    if(!b || b.hostRef!==hostRef) return fail("BINDING_NOT_FOUND");
    if(b.mounted) {
      const unmounted=await call(host.unmountSurface.bind(host),{
        bindingRef,hostRef,mountRef:b.mountRef
      });
      if(unmounted?.unmounted!==true) return fail("SURFACE_UNMOUNT_FAILED");
      b.mounted=false;
    }
    const detached=await call(provider.detachSurface.bind(provider),{
      connectionRef:b.connectionRef,appRef:b.appRef,hostRef,
      runtimeRef:b.runtimeRef,sessionRef:b.sessionRef,
      surfaceRef:b.surfaceRef,reason:"HOST_DETACHED"
    });
    if(detached?.status!=="DETACHED" ||
       detached.runtimePreserved!==true ||
       detached.sessionPreserved!==true ||
       detached.inputReleased!==true) return fail("SURFACE_DETACH_UNCONFIRMED");
    bindings.delete(bindingRef);
    return {schema:SURFACE_SCHEMA,status:"SURFACE_DETACHED",bindingRef,
      runtimePreserved:true,sessionPreserved:true,processTerminated:false,
      inputReleased:true};
  }
  return Object.freeze({capabilities,attach,observe,updateViewport,
    transferInput,detach,get});
}
