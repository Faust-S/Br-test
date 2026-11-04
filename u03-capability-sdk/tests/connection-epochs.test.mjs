import assert from 'node:assert/strict';
import {test} from 'node:test';
import {HttpProgramConnection} from '../../universal-install/host-bindings/http-program.mjs';
import {observeU01Capabilities} from '../src/u01-bridge.mjs';

const spec={schema:'IWXA_HTTP_PROGRAM/1',program_id:'HTTP_FIXTURE_V1',
 base_url:'http://127.0.0.1:12345',health_path:'/health',
 operations:{evaluate:{method:'POST',path:'/evaluate'}}};
const config={programId:'HTTP_FIXTURE_V1',
 bindings:[{operation:'evaluate',id:'iwxa.http.evaluate',version:'1.0.0'}]};
const fetcher=async(url)=>url.endsWith('/health')?
 new Response('healthy',{status:200}):
 new Response('{"answer":42}',{status:200});
test('HTTP connection epochs rotate on reconnect; stale execution is refused',async()=>{
 const c=new HttpProgramConnection(spec,{fetcher});
 const first=await c.connect();
 assert.equal(first.status,'CONNECTED');
 assert.match(first.connection_epoch,/^[A-Za-z0-9_-]{16,128}$/);
 const result=await c.invoke('evaluate',{a:6,b:7});
 assert.equal(result.status,'EXECUTED');
 assert.equal(result.connection_epoch,first.connection_epoch);
 const previous={observation:result,observationRef:'host:execution:1'};
 assert.equal(observeU01Capabilities(c,{...config,receipts:{evaluate:previous}})[0].available,true);
 assert.equal((await c.verify()).status,'CONNECTED');
 const next=await c.invoke('evaluate',{a:6,b:7});
 assert.notEqual(next.connection_epoch,first.connection_epoch);
 assert.equal(observeU01Capabilities(c,{...config,receipts:{evaluate:previous}})[0].available,false);
 assert.equal(observeU01Capabilities(c,{...config,receipts:{
  evaluate:{observation:next,observationRef:'host:execution:2'}}})[0].available,true);
 c.disconnect();
 assert.equal(c.capabilities().connection_epoch,undefined);
 assert.equal(observeU01Capabilities(c,{...config,receipts:{evaluate:previous}})[0].available,false);
 const newConnection=await c.recover();
 assert.notEqual(newConnection.connection_epoch,next.connection_epoch);
 assert.equal(c.capabilities().operations[0].verified,false);
});
test('HTTP authorization loss invalidates epochs and prior execution proofs',async()=>{
 let authorized=true;
 const c=new HttpProgramConnection(spec,{fetcher:async(url)=>{
  if(!authorized)return new Response('unauthorized',{status:401});
  return fetcher(url);
 }});
 await c.connect();
 const before=await c.invoke('evaluate',{a:2,b:2});
 assert.ok(before.connection_epoch);
 authorized=false;
 assert.equal((await c.invoke('evaluate',{a:3,b:3})).status,'AUTHORIZATION_REQUIRED');
 assert.equal(c.capabilities().connection_epoch,undefined);
 authorized=true;
 const after=await c.recover();
 assert.notEqual(after.connection_epoch,before.connection_epoch);
});
