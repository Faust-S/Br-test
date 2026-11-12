// Real public U04 owner's bridge + surface controller into U03 Programs Center.
// Host-neutral simulated OS adapter; live loopback HTML surface; NO installed
// Windows GUI or native Obsidian accepted on the strength of this test.
import assert from 'node:assert/strict';
import {createServer} from 'node:http';
import {once} from 'node:events';
import {spawnSync} from 'node:child_process';
import {resolve} from 'node:path';
import {fileURLToPath,pathToFileURL} from 'node:url';
import {test} from 'node:test';

import {projectProgramCard} from '../src/program-card.mjs';

const projectRoot=resolve(fileURLToPath(new URL('../../',import.meta.url)));
const u04Root=resolve(projectRoot,'u04-owner-pinned');
const {createPresentationBridge}=await import(pathToFileURL(
  resolve(u04Root,'u04-presentation/bridge.mjs')).href);
const APP='iwxa-u04-consumer', HOST='iwxa-agent';
const programId='U04_PROGRAM_V1';
const kinds=[
  ['LINKED_WINDOW','iwxa.presentation.linked'],
  ['EMBEDDED_VIEW','iwxa.presentation.embedded'],
  ['SHARED_LIVE_SURFACE','iwxa.presentation.shared'],
];
const descriptor={
 schema:'IWXA_MODULE_DESCRIPTOR/1.0.0',
 id:'iwxa.u03.public.u04.consumer',version:'0.1.0',
 provides:kinds.map(([kind,id])=>({id,version:'1.0.0'})),
 requires:[],hostCompatibility:[{hostId:'iwxa.agent',range:'=0.5.0'}],
};
const channels=kinds.map(([kind,id])=>({kind,id}));
const row=(card,kind)=>card.capabilities.find(x=>x.kind===kind);

test('native window and official live surface: real U04 owner observations map into independent U03 rows',async()=>{
 const state={alive:true,surfaceLive:true,starts:0,focuses:0,
   mounts:0,connectionSequence:0,mountSequence:0,
   connections:new Set(),mounted:new Set()};
 const server=createServer((req,res)=>{
   if(req.url==='/app'){
     res.writeHead(200,{'content-type':'text/html;charset=utf-8'});
     res.end('<!doctype html><title>IWXA U04 owned UI</title><div>existing live document</div>');
   }else {res.writeHead(404);res.end();}
 });
 server.listen(0,'127.0.0.1');
 await once(server,'listening');
 try{
   const origin='http://127.0.0.1:'+server.address().port;
   const identity={runtimeRef:'one-runtime',sessionRef:'one-session',surfaceRef:'one-panel'};
   const native={
     linkedWindow:true,launchAvailable:true,focusAvailable:true,reattachAvailable:true,
     async launch({appRef,hostRef}){
       assert.equal(appRef,APP);assert.equal(hostRef,HOST);
       state.starts++;
       return {status:'LAUNCHED',pid:3011,processStamp:'process-start-3011'};
     },
     async observe({pid,appRef,processStamp}){
       assert.equal(pid,3011);assert.equal(appRef,APP);
       if(processStamp!==undefined&&processStamp!==null)
         assert.equal(processStamp,'process-start-3011');
       return {observed:state.alive,windowRef:state.alive?'window:3011':null};
     },
     async focus({pid,windowRef}){
       state.focuses++;
       return {focused:state.alive&&pid===3011&&windowRef==='window:3011'};
     },
     async reattach({appRef,hostRef,pid,processStamp}){
       assert.equal(appRef,APP);assert.equal(hostRef,HOST);
       return {status:'REATTACHED',pid,processStamp,spawned:false};
     },
     async verifyProcessIdentity({pid,processStamp}){
       return {verified:state.alive&&pid===3011&&processStamp==='process-start-3011'};
     }
   };
   const provider={
     official:true,embeddedView:true,sharedLiveSurface:true,
     async attachSurface({appRef,hostRef,mode,sessionRef}){
       assert.equal(appRef,APP);assert.equal(hostRef,HOST);
       assert.ok(['EMBEDDED_VIEW','SHARED_LIVE_SURFACE'].includes(mode));
       if(sessionRef!==null)assert.equal(sessionRef,'one-session');
       const connectionRef='connection-'+(++state.connectionSequence);
       state.connections.add(connectionRef);
       return {status:'ATTACHED',verified:true,official:true,existingRuntime:true,
         spawned:false,mode,connectionRef,...identity,
         presentation:{kind:'WEB_URL',official:true,url:origin+'/app',origin}};
     },
     async observeSurface({connectionRef}){
       if(!state.connections.has(connectionRef)||!state.surfaceLive)
         return {status:'LOST'};
       return {status:'LIVE',verified:true,...identity,
         inputOwnerRef:'iwxa-agent',inputEpoch:1};
     },
     async resizeSurface({viewport}){return {status:'VIEWPORT_APPLIED',viewport};},
     async transferSurfaceInput({toOwnerRef,expectedEpoch}){
       return {status:'INPUT_DENIED',inputOwnerRef:'iwxa-agent',inputEpoch:expectedEpoch};
     },
     async detachSurface({connectionRef}){
       state.connections.delete(connectionRef);
       return {status:'DETACHED',runtimePreserved:true,
         sessionPreserved:true,inputReleased:true};
     }
   };
   const host={
     officialPlacement:true,
     async mountSurface({presentation}){
       assert.equal(presentation.official,true);
       const response=await fetch(presentation.url);
       assert.equal(response.status,200);
       assert.match(await response.text(),/existing live document/);
       state.mounts++;
       const mountRef='mount-'+(++state.mountSequence);
       state.mounted.add(mountRef);
       return {mounted:true,mountRef};
     },
     async resizeSurface({mountRef}){return {resized:state.mounted.has(mountRef)};},
     async unmountSurface({mountRef}){
       return {unmounted:state.mounted.delete(mountRef)};
     }
   };
   const bridge=createPresentationBridge(native,{surfaceProvider:provider,surfaceHost:host});
   assert.equal(bridge.capabilities().schema,'IWXA_PRESENTATION_BRIDGE/1.0.0');
   for(const [kind] of kinds)
     assert.equal(bridge.capabilities().capabilities.find(x=>x.mode===kind)?.supported,true);
   const vectors=[];
   function record(label,observations,expect={}){
     const request={programId,descriptor,channels, presentation:{
       appRef:APP,hostRef:HOST,capabilities:bridge.capabilities(),
       observations:structuredClone(observations)}};
     const projection=projectProgramCard(request);
     assert.equal(projection.capabilities.length,3);
     for(const [kind,fields] of Object.entries(expect)){
       const observed=row(projection,kind);
       for(const [field,value] of Object.entries(fields))
         assert.deepEqual(observed?.[field],value,label+': '+kind+'.'+field);
     }
     assert.equal('invoke' in projection,false);
     assert.equal('grants' in projection,false);
     vectors.push({label,request,projection});
     return projection;
   }
   record('unobserved',{},{
     LINKED_WINDOW:{state:'DECLARED',verified:false},
     EMBEDDED_VIEW:{state:'DECLARED',verified:false},
     SHARED_LIVE_SURFACE:{state:'DECLARED',verified:false}
   });
   const opened=await bridge.open({appRef:APP,hostRef:HOST,launchSpec:{approved:true}});
   assert.equal(opened.status,'WINDOW_OBSERVED');
   record('window-observed',{LINKED_WINDOW:opened},{
     LINKED_WINDOW:{state:'INTEGRATED',verified:false,
       reason:'WINDOW_OBSERVED_FOCUS_NOT_CONFIRMED'},
     EMBEDDED_VIEW:{state:'DECLARED',verified:false}});
   const focus=await bridge.activate({bindingRef:opened.binding.bindingRef,hostRef:HOST});
   assert.equal(focus.status,'WINDOW_FOCUSED');
   record('window-focused',{LINKED_WINDOW:focus},{
     LINKED_WINDOW:{state:'AVAILABLE',verified:true,
       observationRef:opened.binding.bindingRef},
     EMBEDDED_VIEW:{state:'DECLARED',verified:false}
   });
   state.alive=false;
   const lost=await bridge.activate({bindingRef:opened.binding.bindingRef,hostRef:HOST});
   assert.equal(lost.status,'PROCESS_IDENTITY_CHANGED');
   record('window-lost',{LINKED_WINDOW:lost},{
     LINKED_WINDOW:{state:'BLOCKED',verified:false,
       reason:'PROCESS_IDENTITY_CHANGED'}});
   const closed=await bridge.closeBinding({bindingRef:opened.binding.bindingRef,hostRef:HOST});
   assert.equal(closed.status,'BINDING_CLOSED');
   record('window-detached',{LINKED_WINDOW:closed},{
     LINKED_WINDOW:{state:'BLOCKED',reason:'BINDING_CLOSED',verified:false}});
   state.alive=true;
   const reattached=await bridge.reattach({appRef:APP,hostRef:HOST,
     pid:3011,processStamp:'process-start-3011'});
   assert.equal(reattached.status,'WINDOW_OBSERVED');
   assert.equal(reattached.spawned,false);
   record('reattached-observed',{LINKED_WINDOW:reattached},{
     LINKED_WINDOW:{state:'INTEGRATED',verified:false}});
   const focused=await bridge.activate({bindingRef:reattached.binding.bindingRef,hostRef:HOST});
   assert.equal(focused.status,'WINDOW_FOCUSED');
   record('reattached-focused',{LINKED_WINDOW:focused},{
     LINKED_WINDOW:{state:'AVAILABLE',verified:true}});
   assert.equal(state.starts,1,'reattach must not launch a second program');

   const attached=await bridge.attachSurface({appRef:APP,hostRef:HOST,mode:'EMBEDDED_VIEW'});
   assert.equal(attached.status,'SURFACE_MOUNTED');
   record('embedded-mounted',{EMBEDDED_VIEW:attached},{
     EMBEDDED_VIEW:{state:'INTEGRATED',verified:false,
       reason:'SURFACE_LIVE_RECHECK_REQUIRED'},
     SHARED_LIVE_SURFACE:{state:'DECLARED',verified:false}});
   const live=await bridge.observeSurface({
     bindingRef:attached.binding.bindingRef,hostRef:HOST});
   assert.equal(live.status,'SURFACE_LIVE');
   record('embedded-live',{EMBEDDED_VIEW:live},{
     EMBEDDED_VIEW:{state:'AVAILABLE',verified:true,
       observationSource:'IWXA_PRESENTATION_SURFACE/1.0.0'},
     SHARED_LIVE_SURFACE:{state:'DECLARED',verified:false}});
   state.surfaceLive=false;
   const gone=await bridge.observeSurface({
     bindingRef:attached.binding.bindingRef,hostRef:HOST});
   assert.equal(gone.status,'SURFACE_LOST');
   assert.equal(gone.binding,undefined);
   record('embedded-lost',{EMBEDDED_VIEW:gone},{
     EMBEDDED_VIEW:{state:'BLOCKED',verified:false,
       reason:'PROVIDER_LIVE_OBSERVATION_UNAVAILABLE',
       observationSource:'IWXA_PRESENTATION_SURFACE/1.0.0'}});
   const detached=await bridge.detachSurface({bindingRef:attached.binding.bindingRef,hostRef:HOST});
   assert.equal(detached.status,'SURFACE_DETACHED');
   record('embedded-detached',{EMBEDDED_VIEW:detached},{
     EMBEDDED_VIEW:{state:'BLOCKED',verified:false,
       reason:'SURFACE_DETACHED'}});
   state.surfaceLive=true;
   const shared=await bridge.attachSurface({appRef:APP,hostRef:HOST,
     mode:'SHARED_LIVE_SURFACE',sessionRef:'one-session'});
   assert.equal(shared.status,'SURFACE_MOUNTED');
   record('shared-mounted',{SHARED_LIVE_SURFACE:shared},{
     SHARED_LIVE_SURFACE:{state:'INTEGRATED',verified:false}});
   const sharedLive=await bridge.observeSurface({
     bindingRef:shared.binding.bindingRef,hostRef:HOST});
   assert.equal(sharedLive.status,'SURFACE_LIVE');
   record('shared-live',{SHARED_LIVE_SURFACE:sharedLive},{
     SHARED_LIVE_SURFACE:{state:'AVAILABLE',verified:true},
     EMBEDDED_VIEW:{state:'DECLARED',verified:false},
     LINKED_WINDOW:{state:'DECLARED',verified:false}});
   const forbidden=await bridge.getSurface({
     bindingRef:shared.binding.bindingRef,hostRef:'different-host'});
   assert.equal(forbidden.status,'BINDING_NOT_FOUND');
   record('foreign-host-denied',{SHARED_LIVE_SURFACE:forbidden},{
     SHARED_LIVE_SURFACE:{state:'BLOCKED',verified:false,
       reason:'BINDING_NOT_FOUND'}});
   const sharedDetached=await bridge.detachSurface({
     bindingRef:shared.binding.bindingRef,hostRef:HOST});
   assert.equal(sharedDetached.status,'SURFACE_DETACHED');
   record('shared-detached',{SHARED_LIVE_SURFACE:sharedDetached},{
     SHARED_LIVE_SURFACE:{state:'BLOCKED',verified:false,
       reason:'SURFACE_DETACHED'}});
   assert.equal((await bridge.closeBinding({
     bindingRef:reattached.binding.bindingRef,hostRef:HOST})).processTerminated,false);
   assert.equal(state.starts,1);
   assert.equal(state.mounts,2);
   assert.equal(state.connections.size,0);
   assert.equal(state.mounted.size,0);

   // Compare every U04 owner observation through the actual Python U03 consumer,
   // without a new U01 request, another U04 attach or any program execution.
   const python=process.env.PYTHON??(process.platform==='win32'?'python':'python3');
   const script=resolve(projectRoot,
     'u03-capability-sdk/qualification/u04_owner_program_card_python.py');
   const result=spawnSync(python,[script],{
     input:JSON.stringify(vectors.map(({label,request})=>({label,request}))),
     encoding:'utf8',timeout:30000,maxBuffer:2*1024*1024
   });
   assert.equal(result.status,0,result.stderr||result.error?.message);
   const verified=JSON.parse(result.stdout.trim());
   assert.equal(verified.status,'PASS');
   assert.deepEqual(verified.results,vectors.map(({label,projection})=>({
     label,projection})));
   assert.equal(state.starts,1);
   assert.equal(state.mounts,2);
   console.log(JSON.stringify({schema:'IWXA_U03_U04_OWNER_INTEROP/1',
     status:'PASS',u04Owner:'1548cf607616904f78085e1e6f32a984022efa5b',
     cases:vectors.length,rows:vectors.length*kinds.length,
     javascriptPythonParity:true,hostNativeLaunches:state.starts,
     officialSurfaceMounts:state.mounts,
     liveSurfaceLostReasonPreserved:true,
     installedWindowsGuiVerified:false}));
 } finally {
   await new Promise((resolve,reject)=>server.close(e=>e?reject(e):resolve()));
 }
});
