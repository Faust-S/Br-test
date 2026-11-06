import test from "node:test";
import assert from "node:assert/strict";
import {createPresentationBridge,SCHEMA} from "../bridge.mjs";
import {createWindowsPresentationAdapter} from "../windows-adapter.mjs";

function fixture() {
 const state={alive:true,focus:true,observations:0,launches:0,terminated:0};
 const adapter={
  linkedWindow:true,embeddedView:false,
  async launch(){state.launches++;return {status:"LAUNCHED",pid:105};},
  async observe({pid}){assert.equal(pid,105);state.observations++;return state.alive?{observed:true,windowRef:String(state.observations)}:{observed:false};},
  async focus({pid,windowRef}){assert.equal(pid,105);return {focused:state.focus};}
 };
 return {state,bridge:createPresentationBridge(adapter)};
}
test("launch, real observed window identity, re-observed repeated focus and harmless detach",async()=>{
 const {state,bridge}=fixture();
 const launched=await bridge.open({appRef:"sample",hostRef:"atlas",launchSpec:{approved:true}});
 assert.equal(launched.schema,SCHEMA);
 assert.equal(launched.status,"WINDOW_OBSERVED");
 assert.equal(state.launches,1);
 assert.equal((await bridge.activate({bindingRef:launched.binding.bindingRef,hostRef:"atlas"})).status,"WINDOW_FOCUSED");
 assert.equal((await bridge.activate({bindingRef:launched.binding.bindingRef,hostRef:"atlas"})).status,"WINDOW_FOCUSED");
 assert.equal(state.launches,1);
 assert.equal(state.observations,3);
 assert.equal((await bridge.closeBinding({bindingRef:launched.binding.bindingRef,hostRef:"atlas"})).processTerminated,false);
 assert.equal(state.terminated,0);
 assert.equal((await bridge.activate({bindingRef:launched.binding.bindingRef,hostRef:"atlas"})).status,"BINDING_NOT_FOUND");
});
test("capabilities refuse unsupported official embedding, no speculative I4",()=>{
 const bridge=fixture().bridge;
 const modes=bridge.capabilities().capabilities;
 assert.deepEqual(modes.map(x=>x.mode),["LAUNCH","FOCUS","LINKED_WINDOW","EMBEDDED_VIEW","SHARED_LIVE_SURFACE"]);
 assert.equal(modes.find(x=>x.mode==="EMBEDDED_VIEW").supported,false);
 assert.equal(modes.find(x=>x.mode==="EMBEDDED_VIEW").reason,"NO_OFFICIAL_TOOLKIT_EMBED_API");
 assert.equal(modes.find(x=>x.mode==="SHARED_LIVE_SURFACE").supported,false);
 assert.equal(modes.find(x=>x.mode==="SHARED_LIVE_SURFACE").reason,"SHARED_RUNTIME_SURFACE_UNAVAILABLE");
});
test("foreign host rejected without OS focus, disappearing window fail closed",async()=>{
 const {state,bridge}=fixture();
 const b=await bridge.open({appRef:"sample",hostRef:"host-A",launchSpec:{}});
 assert.equal((await bridge.activate({bindingRef:b.binding.bindingRef,hostRef:"host-B"})).status,"BINDING_NOT_FOUND");
 state.alive=false;
 assert.equal((await bridge.activate({bindingRef:b.binding.bindingRef,hostRef:"host-A"})).status,"WINDOW_NOT_FOUND");
});
test("focus refusal is reported, never assumed successful",async()=>{
 const {state,bridge}=fixture();state.focus=false;
 const b=await bridge.open({appRef:"sample",hostRef:"host-A",launchSpec:{}});
 const result=await bridge.activate({bindingRef:b.binding.bindingRef,hostRef:"host-A"});
 assert.equal(result.status,"WINDOW_FOCUS_DENIED");
 assert.equal(result.focused,false);
});
test("invalid binding and adapter rejected",async()=>{
 assert.throws(()=>createPresentationBridge({}),/PRESENTATION_ADAPTER_INVALID/);
 const bridge=fixture().bridge;
 await assert.rejects(bridge.open({appRef:"",hostRef:"x",launchSpec:{}}),/PRESENTATION_BINDING_INVALID/);
});
test("Windows port never launches foreign-platform executable",async()=>{
 const adapter=createWindowsPresentationAdapter({platform:"linux",observeWindow:()=>{},focusWindow:()=>{}});
 assert.equal((await adapter.launch({launchSpec:{executable:"/bin/echo"}})).status,"PLATFORM_UNSUPPORTED");
 assert.equal(adapter.linkedWindow,false);
});

test("host approval required before Windows executable launch",async()=>{
 const port=createWindowsPresentationAdapter({platform:"win32",observeWindow:()=>{},focusWindow:()=>{}});
 assert.equal(port.launchAvailable,false);
 assert.equal((await port.launch({appRef:"editor",hostRef:"agent",
   launchSpec:{executable:"C:\\\\Windows\\\\System32\\\\notepad.exe"}})).status,"HOST_APPROVAL_REQUIRED");
});
test("capabilities follow real platform adapters",()=>{
 const a=createWindowsPresentationAdapter({platform:"linux",observeWindow:()=>{},focusWindow:()=>{}});
 const caps=createPresentationBridge(a).capabilities().capabilities;
 assert.equal(caps.find(c=>c.mode==="LAUNCH").supported,false);
 assert.equal(caps.find(c=>c.mode==="FOCUS").supported,false);
 assert.equal(caps.find(c=>c.mode==="LINKED_WINDOW").supported,false);
 assert.equal(caps.find(c=>c.mode==="EMBEDDED_VIEW").supported,false);
});
test("launch observation failure retains detached-safe binding",async()=>{
 const bridge=createPresentationBridge({linkedWindow:true,
   async launch(){return {status:"LAUNCHED",pid:202}},
   async observe(){throw Error("transient native probe")},
   async focus(){return {focused:false}}});
 const x=await bridge.open({appRef:"editor",hostRef:"center",launchSpec:{}});
 assert.equal(x.status,"LAUNCHED_NO_WINDOW");
 assert.equal(x.reason,"WINDOW_OBSERVATION_FAILED");
 assert.equal((await bridge.activate({bindingRef:x.binding.bindingRef,hostRef:"center"})).status,"WINDOW_OBSERVATION_FAILED");
 assert.equal((await bridge.closeBinding({bindingRef:x.binding.bindingRef,hostRef:"center"})).processTerminated,false);
});
test("closing binding during pending focus does not return a stale success",async()=>{
 let resolve;
 const bridge=createPresentationBridge({linkedWindow:true,
   async launch(){return {status:"LAUNCHED",pid:205}},
   async observe(){return {observed:true,windowRef:"25"}},
   async focus(){return await new Promise(r=>{resolve=r})}});
 const x=await bridge.open({appRef:"editor",hostRef:"host",launchSpec:{}});
 const pending=bridge.activate({bindingRef:x.binding.bindingRef,hostRef:"host"});
 await new Promise(r=>setImmediate(r));
 assert.equal((await bridge.closeBinding({bindingRef:x.binding.bindingRef,hostRef:"host"})).status,"BINDING_CLOSED");
 resolve({focused:true});
 assert.equal((await pending).status,"BINDING_NOT_FOUND");
});

test("Agent 0.3 native reattach requires exact host approval and OS process identity",async()=>{
 let approvals=0,observations=0;
 const port=createWindowsPresentationAdapter({
  platform:"win32",
  observeWindow:async pid=>{observations++;return {observed:true,
    windows:[{handle:1234}]};},
  focusWindow:async(pid,handle)=>({focused:pid===4512&&handle===1234}),
  approveExistingProcess:async({appRef,hostRef,pid,processStamp})=>{
    approvals++;return {approved:appRef==="obsidian"&&hostRef==="agent",
      pid,processStamp};
  },
  verifyNativeProcessIdentity:async({pid,processStamp})=>({
    verified:pid===4512&&processStamp==="native-start-4512"
  })
 });
 const bridge=createPresentationBridge(port);
 const connected=await bridge.reattach({appRef:"obsidian",hostRef:"agent",
   pid:4512,processStamp:"native-start-4512"});
 assert.equal(connected.status,"WINDOW_OBSERVED");
 assert.equal(connected.spawned,false);
 assert.equal(connected.binding.processId,4512);
 assert.equal(connected.binding.processStamp,"native-start-4512");
 const focus=await bridge.activate({bindingRef:connected.binding.bindingRef,
   hostRef:"agent"});
 assert.equal(focus.status,"WINDOW_FOCUSED");
 assert.equal(focus.focused,true);
 assert.equal((await bridge.activate({bindingRef:connected.binding.bindingRef,
   hostRef:"wrong-host"})).status,"BINDING_NOT_FOUND");
 assert.equal((await bridge.closeBinding({bindingRef:connected.binding.bindingRef,
   hostRef:"agent"})).processTerminated,false);
 const denied=await bridge.reattach({appRef:"obsidian",hostRef:"agent",
   pid:4512,processStamp:"stale-start"});
 assert.equal(denied.status,"PROCESS_IDENTITY_UNVERIFIED");
 assert.equal(approvals,2);
 assert.ok(observations>=2);
});

test("Agent 0.3 Windows reattach remains unavailable without host-provided native verifier",async()=>{
 const adapter=createWindowsPresentationAdapter({platform:"win32",
   observeWindow:async()=>({observed:false}),focusWindow:async()=>({focused:false})});
 assert.equal(adapter.reattachAvailable,false);
 assert.equal((await adapter.reattach({appRef:"obsidian",hostRef:"agent",
   pid:1,processStamp:"native"})).status,"REATTACH_UNSUPPORTED");
 assert.equal((await createPresentationBridge(adapter).reattach({
   appRef:"obsidian",hostRef:"agent",pid:1,processStamp:"native"
 })).status,"REATTACH_UNSUPPORTED");
});
