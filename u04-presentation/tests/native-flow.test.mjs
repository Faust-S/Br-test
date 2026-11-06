import test from "node:test";
import assert from "node:assert/strict";
import {createPresentationBridge} from "../bridge.mjs";

test("linked native: open, Agent focus, app work, return, disappearing window and detach",async()=>{
 const state={pid:2468,stamp:"stamp-2468",window:"HWND-55",
   alive:true,launches:0,focuses:0,work:"user content"};
 const adapter={linkedWindow:true,async launch(){
   state.launches++;return {status:"LAUNCHED",pid:state.pid,
     processStamp:state.stamp};
 },async verifyProcessIdentity({pid,processStamp}){
   return {verified:state.alive&&pid===state.pid&&processStamp===state.stamp};
 },async reattach({pid,processStamp}){
   return {status:state.alive&&processStamp===state.stamp?
     "REATTACHED":"PROCESS_IDENTITY_CHANGED",pid,processStamp,spawned:false};
 },async observe(){return state.alive&&state.window?
   {observed:true,windowRef:state.window}:{observed:false};},
 async focus({windowRef}){state.focuses++;return {focused:windowRef===state.window};}};
 const agent=createPresentationBridge(adapter);
 const opened=await agent.open({appRef:"obsidian",hostRef:"agent",launchSpec:{}});
 assert.equal(opened.status,"WINDOW_OBSERVED");
 assert.equal(opened.binding.processStamp,state.stamp);
 assert.equal((await agent.activate({bindingRef:opened.binding.bindingRef,
   hostRef:"agent"})).status,"WINDOW_FOCUSED");
 state.work="document edited";
 assert.equal((await agent.activate({bindingRef:opened.binding.bindingRef,
   hostRef:"agent"})).status,"WINDOW_FOCUSED");
 state.window=null;
 assert.equal((await agent.activate({bindingRef:opened.binding.bindingRef,
   hostRef:"agent"})).status,"WINDOW_NOT_FOUND");
 state.window="HWND-56";
 assert.equal((await agent.activate({bindingRef:opened.binding.bindingRef,
   hostRef:"agent"})).status,"WINDOW_FOCUSED");
 assert.equal((await agent.closeBinding({bindingRef:opened.binding.bindingRef,
   hostRef:"agent"})).processTerminated,false);
 const restarted=createPresentationBridge(adapter);
 const attached=await restarted.reattach({appRef:"obsidian",hostRef:"agent",
   pid:state.pid,processStamp:state.stamp});
 assert.equal(attached.status,"WINDOW_OBSERVED");
 assert.equal(attached.spawned,false);
 assert.equal(state.launches,1);
 assert.equal((await restarted.activate({bindingRef:attached.binding.bindingRef,
   hostRef:"agent"})).status,"WINDOW_FOCUSED");
 assert.equal(state.work,"document edited");
 state.alive=false;
 assert.equal((await restarted.activate({bindingRef:attached.binding.bindingRef,
   hostRef:"agent"})).status,"PROCESS_IDENTITY_CHANGED");
 assert.equal((await restarted.closeBinding({bindingRef:attached.binding.bindingRef,
   hostRef:"agent"})).status,"BINDING_CLOSED");
});

test("native reattach refuses missing OS verifier and false process identity",async()=>{
 const a={async launch(){return {status:"LAUNCHED",pid:12};},
  async observe(){return {observed:false};},async focus(){return {focused:false}},
  async reattach({pid,processStamp}){return {status:"REATTACHED",pid,
    processStamp,spawned:true}}};
 const bridge=createPresentationBridge(a);
 assert.equal((await bridge.reattach({appRef:"obsidian",hostRef:"agent",
   pid:12,processStamp:"stamp"})).status,"REATTACH_UNSUPPORTED");
 assert.equal((await bridge.reattach({appRef:"obsidian",hostRef:"agent",
   pid:12,processStamp:""})).status,"PRESENTATION_BINDING_INVALID");
});
