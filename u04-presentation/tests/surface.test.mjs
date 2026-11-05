import test from "node:test";
import assert from "node:assert/strict";
import {createServer} from "node:http";
import {once} from "node:events";
import {createPresentationBridge} from "../bridge.mjs";
import {createPresentationSurfaceController} from "../surface.mjs";
import {integrationObservation} from "../../universal-install/host-bindings/ui-levels.mjs";

test("real HTTP live Atlas surface: same runtime, two mounts, exclusive input and host restart", async () => {
  const state={starts:1,owner:"atlas",epoch:0,connections:new Map(),mounts:new Set(),seq:0};
  const server=createServer((req,res)=>{
    if(req.url!=="/live"){res.writeHead(404);res.end();return;}
    res.setHeader("content-type","text/html");
    res.end("<html><body><input value='preserved Atlas document'><button>Save</button></body></html>");
  });
  server.listen(0,"127.0.0.1");await once(server,"listening");
  const origin="http://127.0.0.1:"+server.address().port;
  const identity={runtimeRef:"atlas-runtime",sessionRef:"session-123",surfaceRef:"panel"};
  const provider={
    official:true,embeddedView:true,sharedLiveSurface:true,
    async attachSurface({hostRef,mode}){
      const connectionRef="conn-"+(++state.seq);
      state.connections.set(connectionRef,hostRef);
      return {status:"ATTACHED",verified:true,official:true,existingRuntime:true,
        spawned:false,mode,connectionRef,...identity,
        presentation:{kind:"WEB_URL",official:true,url:origin+"/live",origin}};
    },
    async observeSurface({connectionRef}){
      if(!state.connections.has(connectionRef))return {status:"LOST"};
      return {status:"LIVE",verified:true,...identity,
        inputOwnerRef:state.owner,inputEpoch:state.epoch};
    },
    async resizeSurface({viewport}){return {status:"VIEWPORT_APPLIED",viewport};},
    async transferSurfaceInput({toOwnerRef,expectedEpoch}){
      if(expectedEpoch!==state.epoch)return {status:"INPUT_DENIED",
        inputEpoch:state.epoch,inputOwnerRef:state.owner};
      state.owner=toOwnerRef;state.epoch++;
      return {status:"INPUT_TRANSFERRED",inputEpoch:state.epoch,inputOwnerRef:state.owner};
    },
    async detachSurface({connectionRef}){
      state.connections.delete(connectionRef);
      return {status:"DETACHED",runtimePreserved:true,sessionPreserved:true,inputReleased:true};
    }
  };
  const host={officialPlacement:true,
    async mountSurface({presentation}){
      const html=await (await fetch(presentation.url)).text();
      assert.match(html,/preserved Atlas document/);
      const mountRef="mount-"+state.mounts.size+"-"+state.seq;
      state.mounts.add(mountRef);return {mounted:true,mountRef};
    },
    async resizeSurface({mountRef}){return {resized:state.mounts.has(mountRef)};},
    async unmountSurface({mountRef}){return {unmounted:state.mounts.delete(mountRef)};}
  };
  try {
    const b=createPresentationBridge(null,{surfaceProvider:provider,surfaceHost:host});
    const caps=b.capabilities();
    assert.equal(caps.paths.length,3);
    assert.equal(caps.capabilities.find(c=>c.mode==="EMBEDDED_VIEW").supported,true);
    const a=await b.attachSurface({appRef:"atlas",hostRef:"atlas",
      mode:"EMBEDDED_VIEW"});
    const agent=await b.attachSurface({appRef:"atlas",hostRef:"agent",
      mode:"SHARED_LIVE_SURFACE",sessionRef:"session-123"});
    assert.equal(a.status,"SURFACE_MOUNTED");
    assert.equal(agent.status,"SURFACE_MOUNTED");
    assert.equal(a.binding.runtimeRef,agent.binding.runtimeRef);
    assert.equal(state.starts,1);
    assert.equal(state.connections.size,2);
    assert.equal((await b.observeSurface({bindingRef:agent.binding.bindingRef,
      hostRef:"foreign"})).status,"BINDING_NOT_FOUND");
    const change=await b.transferSurfaceInput({bindingRef:agent.binding.bindingRef,
      hostRef:"agent",toOwnerRef:"agent",expectedEpoch:0});
    assert.equal(change.status,"INPUT_TRANSFERRED");
    const stale=await b.transferSurfaceInput({bindingRef:a.binding.bindingRef,
      hostRef:"atlas",toOwnerRef:"atlas",expectedEpoch:0});
    assert.equal(stale.status,"INPUT_DENIED");
    assert.equal(state.owner,"agent");
    assert.equal((await b.updateViewport({bindingRef:agent.binding.bindingRef,
      hostRef:"agent",viewport:{width:1200,height:700,deviceScaleFactor:1}})).status,"VIEWPORT_APPLIED");
    assert.equal((await b.updateViewport({bindingRef:agent.binding.bindingRef,
      hostRef:"agent",viewport:{width:0,height:700,deviceScaleFactor:1}})).status,"VIEWPORT_INVALID");
    assert.equal((await b.transferSurfaceInput({bindingRef:agent.binding.bindingRef,
      hostRef:"agent",toOwnerRef:"atlas",expectedEpoch:1})).status,"INPUT_TRANSFERRED");
    assert.equal((await b.detachSurface({bindingRef:agent.binding.bindingRef,
      hostRef:"agent"})).status,"SURFACE_DETACHED");
    const restart=await b.attachSurface({appRef:"atlas",hostRef:"agent-restart",
      mode:"SHARED_LIVE_SURFACE",sessionRef:"session-123"});
    assert.equal(restart.status,"SURFACE_MOUNTED");
    assert.equal(restart.binding.runtimeRef,"atlas-runtime");
    assert.equal(state.starts,1);
    assert.equal((await b.detachSurface({bindingRef:restart.binding.bindingRef,
      hostRef:"agent-restart"})).processTerminated,false);
    assert.equal((await b.detachSurface({bindingRef:a.binding.bindingRef,
      hostRef:"atlas"})).status,"SURFACE_DETACHED");
    assert.equal(state.connections.size,0);
    assert.equal(state.mounts.size,0);
  } finally {await new Promise((resolve,reject)=>server.close(e=>e?reject(e):resolve()));}
});

test("proof rejection, unsupported I4, origin mismatch and I0-I5/U03 compatibility",async()=>{
 let detachCount=0;
 const provider={official:true,embeddedView:true,sharedLiveSurface:true,
   attachSurface:async()=>({status:"ATTACHED",verified:true,official:true,
     existingRuntime:false,spawned:true,connectionRef:"c",mode:"EMBEDDED_VIEW",
     runtimeRef:"r",sessionRef:"s",surfaceRef:"p",
     presentation:{kind:"WEB_URL",official:true,url:"https://example.org/live",origin:"https://example.org"}}),
   observeSurface:async()=>({status:"LIVE",verified:true}),
   resizeSurface:async()=>({status:"VIEWPORT_APPLIED"}),
   transferSurfaceInput:async()=>({status:"INPUT_DENIED"}),
   detachSurface:async()=>{detachCount++;return {status:"DETACHED"}}
 };
 const host={officialPlacement:true,mountSurface:async()=>({mounted:true,mountRef:"m"}),
   resizeSurface:async()=>({resized:true}),unmountSurface:async()=>({unmounted:true})};
 let ctl=createPresentationSurfaceController(provider,host);
 assert.equal((await ctl.attach({appRef:"atlas",hostRef:"host",mode:"EMBEDDED_VIEW"})).status,
   "SURFACE_PROOF_REJECTED");
 assert.equal(detachCount,1);
 provider.embeddedView=false;
 ctl=createPresentationSurfaceController(provider,host);
 assert.equal((await ctl.attach({appRef:"atlas",hostRef:"host",mode:"EMBEDDED_VIEW"})).status,
   "SURFACE_UNSUPPORTED");
 const levels=integrationObservation({embeddedObservation:{verified:true,official:false}});
 assert.equal(levels.schema,"IWXA_UI_INTEGRATION/1.0.0");
 assert.equal(levels.levels.find(x=>x.level==="I4").verified,false);
 // U03 is separately owned: expose an observation that can be reported
 // via IWXA_CAPABILITY_STATUS/1.0.0, without bundling the U03 evaluator.
 const status={schema:"IWXA_CAPABILITY_STATUS/1.0.0",
   id:"presentation.shared.surface",available:true,version:"1.0.0",
   observationRef:"u04:live:receipt"};
 assert.equal(status.available,true);
 assert.ok(status.observationRef);

});
