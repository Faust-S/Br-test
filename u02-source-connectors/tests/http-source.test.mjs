import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import {createHttpSourcePort} from '../http-source.mjs';
import {pinSource,sourceChange} from '../port.mjs';

async function withServer(handler,fn){
 const server=http.createServer(handler);
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 try{return await fn('http://127.0.0.1:'+server.address().port);}
 finally {await new Promise(resolve=>server.close(resolve));}
}
test('pinned HTTP GET, stale refusal, authorization and recovery',async()=>{
 let content='first',token='synthetic-token',requests=0;
 await withServer((req,res)=>{
  requests++;
  if(req.method!=='GET'||req.url!=='/source'){res.writeHead(405);res.end();return;}
  if(req.headers.authorization!=='Bearer synthetic-token'){res.writeHead(401);res.end();return;}
  res.writeHead(200,{'content-type':'text/plain'});res.end(content);
 },async base=>{
  const port=createHttpSourcePort({endpoint:base+'/source',sourceId:'example.api',
    authHeaders:async()=>({Authorization:'Bearer '+token})});
  assert.equal((await port.read(port.describe())).status,'REVISION_REQUIRED');
  const first=await port.refresh();assert.equal(first.status,'OK');
  assert.match(first.revision,/^sha256:[a-f0-9]{64}$/);
  const pinned=pinSource(port.describe(),first);
  const original=await port.read(pinned);assert.equal(original.status,'OK');
  assert.equal(Buffer.from(original.content.data,'base64').toString(),'first');
  content='second';
  const changed=await port.refresh();assert.equal(sourceChange(first,changed).status,'CHANGED');
  const stale=await port.read(pinned);assert.equal(stale.status,'REVISION_MISMATCH');assert.equal(stale.content,null);
  assert.equal(Buffer.from((await port.read(pinSource(port.describe(),changed))).content.data,'base64').toString(),'second');
  token='expired';assert.equal((await port.read(pinned)).status,'AUTHORIZATION_REQUIRED');
  token='synthetic-token';assert.equal((await port.read(pinned)).status,'REVISION_MISMATCH');
  assert.ok(requests>=7);
  assert.ok(!JSON.stringify({first,changed,original,stale}).includes('synthetic-token'));
  assert.equal(port.describe().authority,'OBSERVATION_ONLY');
 });
});
test('HTTP response classes, forbidden URLs and redirects',async()=>{
 for(const endpoint of ['file:///etc/passwd','http://example.org/x','https://user:pass@example.org/x','https://example.org/x#secret']){
  assert.throws(()=>createHttpSourcePort({endpoint,sourceId:'example.api'}));
 }
 const endpoint='https://example.org/data?page=1';
 for(const [status,expected] of [[401,'AUTHORIZATION_REQUIRED'],[403,'ACCESS_DENIED'],[404,'SOURCE_MISSING'],[429,'RATE_LIMITED'],[503,'SOURCE_UNAVAILABLE']]){
  const port=createHttpSourcePort({endpoint,sourceId:'example.api',fetcher:async()=>({ok:false,status})});
  assert.equal((await port.refresh()).status,expected);
 }
 const badAuth=createHttpSourcePort({endpoint,sourceId:'example.api',authHeaders:async()=>{throw Error('secret')},
  fetcher:async()=>{throw Error('MUST_NOT_FETCH')}});
 assert.equal((await badAuth.refresh()).status,'AUTHORIZATION_REQUIRED');
 const absentAuth=createHttpSourcePort({endpoint,sourceId:'example.api',authHeaders:async()=>null,
  fetcher:async()=>{throw Error('MUST_NOT_FETCH')}});
 assert.equal((await absentAuth.refresh()).status,'AUTHORIZATION_REQUIRED');
 await withServer((req,res)=>{res.writeHead(302,{location:'https://example.org/redirect'});res.end();},async base=>{
  const port=createHttpSourcePort({endpoint:base+'/redirect',sourceId:'local.api'});
  assert.equal((await port.refresh()).status,'SOURCE_UNAVAILABLE');
 });
});
test('bounded streaming response, length header and invalid body',async()=>{
 const endpoint='https://example.org/data';
 const stream=new ReadableStream({start(controller){controller.enqueue(new Uint8Array([1,2]));controller.enqueue(new Uint8Array([3,4,5]));controller.close();}});
 const port=createHttpSourcePort({endpoint,sourceId:'example.api',maxBytes:4,
  fetcher:async()=>({ok:true,status:200,headers:{get:()=>null},body:stream})});
 assert.equal((await port.refresh()).status,'SOURCE_TOO_LARGE');
 const oversized=createHttpSourcePort({endpoint,sourceId:'example.api',maxBytes:4,
  fetcher:async()=>({ok:true,status:200,headers:{get:name=>name==='content-length'?'100000':null},arrayBuffer:async()=>{throw Error('NOT_READ')}})});
 assert.equal((await oversized.refresh()).status,'SOURCE_TOO_LARGE');
 const invalid=createHttpSourcePort({endpoint,sourceId:'example.api',fetcher:async()=>({ok:true,status:200})});
 assert.equal((await invalid.refresh()).status,'SOURCE_INVALID');
});

test('partial, alternate and redirected HTTP success responses cannot be pinned',async()=>{
 const endpoint='https://example.org/data';
 for (const status of [201,202,203,204,205,206,207,226]) {
   const port=createHttpSourcePort({endpoint,sourceId:'example.api',fetcher:async()=>
     ({ok:true,status,url:endpoint,headers:{get:()=>null},arrayBuffer:async()=>Uint8Array.from([65]).buffer})});
   const observation=await port.refresh();
   assert.equal(observation.status,'SOURCE_INVALID');
   assert.equal(observation.content,null);
   assert.throws(()=>pinSource(port.describe(),observation),/SOURCE_PIN_UNAVAILABLE/);
 }
 const badRedirect=createHttpSourcePort({endpoint,sourceId:'example.api',fetcher:async()=>({
   ok:true,status:200,url:'https://example.net/unapproved',redirected:true,
   headers:{get:()=>null},arrayBuffer:async()=>Uint8Array.from([65]).buffer})});
 assert.equal((await badRedirect.refresh()).status,'SOURCE_UNAVAILABLE');
 const inconsistentUrl=createHttpSourcePort({endpoint,sourceId:'example.api',fetcher:async()=>({
   ok:true,status:200,url:'https://example.org/other',redirected:false,
   headers:{get:()=>null},arrayBuffer:async()=>Uint8Array.from([65]).buffer})});
 assert.equal((await inconsistentUrl.refresh()).status,'SOURCE_UNAVAILABLE');
});
test('HTTP pinned descriptor can be serialized and reused with new port instance',async()=>{
 let body='v1';const endpoint='https://example.org/document';
 const fetcher=async(url,options)=>({ok:true,status:200,url,
  headers:{get:()=>null},arrayBuffer:async()=>Buffer.from(body)});
 const port=createHttpSourcePort({endpoint,sourceId:'document.api',fetcher});
 const observed=await port.refresh(),json=JSON.stringify(pinSource(port.describe(),observed));
 const restored=JSON.parse(json);
 const reopened=createHttpSourcePort({endpoint,sourceId:'document.api',fetcher});
 const read=await reopened.read(restored);
 assert.equal(read.status,'OK');
 assert.equal(Buffer.from(read.content.data,'base64').toString(),'v1');
 assert.equal(read.revision,observed.revision);
 body='v2';
 const stale=await reopened.read(restored);
 assert.equal(stale.status,'REVISION_MISMATCH');
 assert.equal(stale.content,null);
 const other=createHttpSourcePort({endpoint,sourceId:'other.api',fetcher});
 assert.equal((await other.read(restored)).status,'REVISION_REQUIRED');
});

test('HTTP partial content hints and incorrect Content-Length fail without source content',async()=>{
 const endpoint='https://example.org/document';
 for(const [headers,payload] of [
   [{ 'content-range':'bytes 0-1/20'},'ab'],
   [{ 'content-length':'5'},'ab'],
   [{ 'content-length':'xyz'},'ab'],
   [{ 'content-length':'02'},'ab']
 ]){
   const port=createHttpSourcePort({endpoint,sourceId:'document.api',fetcher:async(url)=>({
     ok:true,status:200,url,headers:{get:name=>headers[name]??null},
     arrayBuffer:async()=>Buffer.from(payload)
   })});
   const observation=await port.refresh();
   assert.equal(observation.status,'SOURCE_INVALID');
   assert.equal(observation.content,null);
 }
});
