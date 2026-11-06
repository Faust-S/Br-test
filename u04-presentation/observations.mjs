// U04 produces observations for the existing U03 and IWXA_UI_INTEGRATION contracts.
// This is descriptive evidence, not an authorization or connection grant.
import {integrationObservation} from "../universal-install/host-bindings/ui-levels.mjs";

export const U03_STATUS_SCHEMA = "IWXA_CAPABILITY_STATUS/1.0.0";

export function presentationObservations({nativeResult=null,surfaceResult=null,
  discovered=false,dataAvailable=false,connected=false,capabilities=[]}={}) {
  const windowObserved=nativeResult?.status==="WINDOW_FOCUSED" &&
    nativeResult?.focused===true && typeof nativeResult?.binding?.windowRef==="string" &&
    nativeResult.binding.windowRef.length>0;
  const surfaceObserved=surfaceResult?.status==="SURFACE_LIVE" &&
    surfaceResult?.binding?.verified===true &&
    surfaceResult?.binding?.official===true &&
    surfaceResult?.binding?.mounted===true &&
    surfaceResult?.binding?.existingRuntime===true &&
    surfaceResult?.binding?.spawned===false;
  const level=integrationObservation({discovered,dataAvailable,connected,
    capabilities,windowObservation:windowObserved?{observed:true,native:true}:null,
    embeddedObservation:surfaceObserved?{verified:true,official:true}:null});
  const results=[
    {id:"presentation.native.window",available:windowObserved,
      reason:windowObserved?undefined:"NATIVE_WINDOW_NOT_VERIFIED",
      observationRef:windowObserved?nativeResult.binding.bindingRef:undefined},
    {id:"presentation.embedded.surface",available:surfaceObserved &&
      surfaceResult.binding.mode==="EMBEDDED_VIEW",
      reason:surfaceObserved?undefined:"OFFICIAL_EMBEDDED_SURFACE_NOT_VERIFIED",
      observationRef:surfaceObserved?surfaceResult.binding.bindingRef:undefined},
    {id:"presentation.shared.surface",available:surfaceObserved &&
      surfaceResult.binding.mode==="SHARED_LIVE_SURFACE",
      reason:surfaceObserved?undefined:"SHARED_LIVE_SURFACE_NOT_VERIFIED",
      observationRef:surfaceObserved?surfaceResult.binding.bindingRef:undefined}
  ];
  return {schema:"IWXA_U04_PRESENTATION_OBSERVATION/1.0.0",
    uiIntegration:level,
    u03Statuses:results.map(x=>({schema:U03_STATUS_SCHEMA,id:x.id,
      available:x.available,version:"1.0.0",
      ...(x.available?{observationRef:x.observationRef}:
        {reason:x.reason??"MODE_NOT_VERIFIED"})}))};
}
