import test from 'node:test';
import assert from 'node:assert/strict';
import {pinSource} from '../port.mjs';
import {createHostSelectedSourcePort} from '../host-source.mjs';
import {captureSource,readPinnedSource} from '../source-read.mjs';

const enc=text=>new TextEncoder().encode(text);
const args={hostId:'admin-center',scopeId:'orgA',sourceId:'atlas',bindingId:'native-v1'};

test('host snapshot pins and rereads without a native write or implicit apply',async()=>{
 let bytes=enc('Atlas 1'),calls=0;
 const p=createHostSelectedSourcePort({...args,readSelected:async ctx=>{
   calls++;assert.equal(ctx.scopeId,'orgA');return {status:'OK',bytes};
 }});
 assert.equal(calls,0);
 const first=await captureSource(p);
 assert.equal(first.status,'OK');
 assert.equal(Buffer.from(first.read.content.data,'base64').toString(),'Atlas 1');
 const pin=JSON.parse(JSON.stringify(first.pin));
 assert.equal((await readPinnedSource(p,pin)).status,'OK');
 bytes=enc('Atlas 2');
 const old=await readPinnedSource(p,pin);
 assert.equal(old.status,'REVISION_MISMATCH');assert.equal(old.content,null);
 const changed=await captureSource(p);
 assert.equal(changed.status,'OK');assert.notEqual(changed.pin.revision,pin.revision);
 assert.equal(calls,6);
});

test('host scope or selected native binding differs and a foreign pin is refused before read',async()=>{
 let calls=0;const readSelected=async()=>{calls++;return {status:'OK',bytes:enc('same')};};
 const original=createHostSelectedSourcePort({...args,readSelected});
 const pin=pinSource(original.describe(),await original.refresh());
 for(const different of [{scopeId:'orgB'},{bindingId:'other-store'},{sourceId:'different'}]){
   const port=createHostSelectedSourcePort({...args,...different,readSelected});
   assert.equal((await port.read(pin)).status,'REVISION_REQUIRED');
   await assert.rejects(()=>readPinnedSource(port,pin),/SOURCE_PIN_INVALID/);
 }
 assert.equal(calls,1);
});

test('host revocation and missing sources never reveal content',async()=>{
 let status='ACCESS_DENIED';
 const p=createHostSelectedSourcePort({...args,readSelected:async()=>({status,bytes:enc('secret')})});
 const denied=await captureSource(p);
 assert.equal(denied.status,'ACCESS_DENIED');assert.equal(denied.pin,null);
 status='OK';
 const pin=pinSource(p.describe(),await p.refresh());
 status='AUTHORIZATION_REQUIRED';
 const revoked=await p.read(pin);
 assert.equal(revoked.status,'AUTHORIZATION_REQUIRED');assert.equal(revoked.content,null);
 assert.ok(!JSON.stringify(revoked).includes('secret'));
});

test('typed binary bytes and decoded size bounds',async()=>{
 const bytes=Uint8Array.from([0,255,1,2,0,220]);
 const p=createHostSelectedSourcePort({...args,readSelected:()=>({status:'OK',bytes})});
 const first=await captureSource(p);
 assert.equal(first.status,'OK');
 assert.deepEqual(Buffer.from(first.read.content.data,'base64'),Buffer.from(bytes));
 const bounded=createHostSelectedSourcePort({...args,maxBytes:3,readSelected:()=>({status:'OK',bytes})});
 assert.equal((await bounded.refresh()).status,'SOURCE_TOO_LARGE');
 const malformed=createHostSelectedSourcePort({...args,readSelected:()=>({status:'OK',bytes:[1,2,3]})});
 assert.equal((await malformed.refresh()).status,'SOURCE_INVALID');
});

test('binary result is interoperable in a Buffer-free browser-style consumer',async()=>{
 const prior=globalThis.Buffer;
 try {
   globalThis.Buffer=undefined;
   const p=createHostSelectedSourcePort({...args,readSelected:async()=>({status:'OK',
     bytes:Uint8Array.from([0,255,5,3])})});
   const first=await captureSource(p);
   assert.equal(first.status,'OK');
   assert.equal((await readPinnedSource(p,first.pin)).status,'OK');
 } finally {globalThis.Buffer=prior;}
});
