import test from "node:test";
import assert from "node:assert/strict";
import {readFileSync} from "node:fs";
import {presentationBindingId} from "../id.mjs";
import {createPresentationBridge} from "../bridge.mjs";

test("U04 frontend entrypoints have no Node-only crypto import",()=>{
 for(const file of ["id.mjs","bridge.mjs","surface.mjs","observations.mjs"]){
  const source=readFileSync(new URL("../"+file,import.meta.url),"utf8");
  assert.doesNotMatch(source,/from\s+["'](?:node:)?(?:crypto|child_process|fs|path)["']/);
 }
 const a=presentationBindingId(),b=presentationBindingId();
 assert.match(a,/^[0-9a-f-]{36}$/i);
 assert.notEqual(a,b);
});

test("missing secure Web Crypto fails before spawning external native app",async()=>{
 const previous=Object.getOwnPropertyDescriptor(globalThis,"crypto");
 let launches=0;
 const port={async launch(){launches++;return {status:"LAUNCHED",pid:321}},
  async observe(){return {observed:false}},async focus(){return {focused:false}}};
 try {
  Object.defineProperty(globalThis,"crypto",{value:undefined,configurable:true});
  const bridge=createPresentationBridge(port);
  const result=await bridge.open({appRef:"obsidian",hostRef:"agent",
    launchSpec:{}});
  assert.equal(result.status,"BINDING_ID_UNAVAILABLE");
  assert.equal(launches,0);
 } finally {
  if(previous)Object.defineProperty(globalThis,"crypto",previous);
  else delete globalThis.crypto;
 }
});
