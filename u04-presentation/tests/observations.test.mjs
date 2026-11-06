import test from "node:test";
import assert from "node:assert/strict";
import {presentationObservations,U03_STATUS_SCHEMA} from "../observations.mjs";

test("native I3 and U03 status require a verified focused native window",()=>{
 const no=presentationObservations({nativeResult:{
   status:"WINDOW_FOCUS_DENIED",focused:false,binding:{windowRef:"123",bindingRef:"b"}
 }});
 assert.equal(no.uiIntegration.levels.find(x=>x.level==="I3").verified,false);
 assert.equal(no.u03Statuses[0].available,false);
 const yes=presentationObservations({nativeResult:{
   status:"WINDOW_FOCUSED",focused:true,binding:{windowRef:"123",bindingRef:"native-ref"}
 }});
 assert.equal(yes.uiIntegration.schema,"IWXA_UI_INTEGRATION/1.0.0");
 assert.equal(yes.uiIntegration.levels.find(x=>x.level==="I3").verified,true);
 assert.equal(yes.u03Statuses[0].schema,U03_STATUS_SCHEMA);
 assert.equal(yes.u03Statuses[0].observationRef,"native-ref");
 assert.equal(yes.u03Statuses[1].available,false);
});
test("surface I4 observation does not invent I5 or a native HWND",()=>{
 const surface={status:"SURFACE_LIVE",binding:{
   verified:true,official:true,mounted:true,existingRuntime:true,spawned:false,
   mode:"SHARED_LIVE_SURFACE",bindingRef:"surface-ref"
 }};
 const evidence=presentationObservations({surfaceResult:surface,discovered:true});
 assert.equal(evidence.uiIntegration.levels.find(x=>x.level==="I0").verified,true);
 assert.equal(evidence.uiIntegration.levels.find(x=>x.level==="I3").verified,false);
 assert.equal(evidence.uiIntegration.levels.find(x=>x.level==="I4").verified,true);
 assert.equal(evidence.uiIntegration.levels.find(x=>x.level==="I5").verified,false);
 assert.equal(evidence.u03Statuses[1].available,false);
 assert.equal(evidence.u03Statuses[2].available,true);
 assert.equal(evidence.u03Statuses[2].observationRef,"surface-ref");
});
test("unverified, unmounted and newly spawned surfaces never get I4/U03 credit",()=>{
 const base={status:"SURFACE_LIVE",binding:{
   verified:true,official:true,mounted:true,existingRuntime:true,spawned:false,
   mode:"EMBEDDED_VIEW",bindingRef:"id"
 }};
 for(const key of ["verified","official","mounted","existingRuntime"]){
   const e=presentationObservations({surfaceResult:{...base,binding:{...base.binding,[key]:false}}});
   assert.equal(e.uiIntegration.levels.find(x=>x.level==="I4").verified,false);
   assert.equal(e.u03Statuses[1].available,false);
 }
 const created=presentationObservations({surfaceResult:{...base,binding:{...base.binding,spawned:true}}});
 assert.equal(created.uiIntegration.levels.find(x=>x.level==="I4").verified,false);
 assert.equal(created.u03Statuses[1].available,false);
});
