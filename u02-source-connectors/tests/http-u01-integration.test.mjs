import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import {HttpProgramConnection} from '../../universal-install/host-bindings/http-program.mjs';
import {createHttpSourcePort} from '../http-source.mjs';
import {pinSource} from '../port.mjs';

test('U02 reads a real U01 HTTP API without invoking program or granting capabilities',async()=>{
 let source='A',token='synthetic',programInvoke=0,sourceGets=0;
 const server=http.createServer((req,res)=>{
  if(req.headers.authorization!=='Bearer synthetic'){res.writeHead(401);res.end('Unauthorized');return;}
  if(req.url==='/health'){res.writeHead(200);res.end('OK');return;}
  if(req.url==='/source'){sourceGets++;res.writeHead(200);res.end(source);return;}
  res.writeHead(404);res.end('missing');
 });
 await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
 try {
  const base='http://127.0.0.1:'+server.address().port;
  const c=new HttpProgramConnection({schema:'IWXA_HTTP_PROGRAM/1',program_id:'HTTP_SOURCE_V1',
   base_url:base,health_path:'/health',operations:{evaluate:{method:'GET',path:'/source'}}},
   {authHeaders:async()=>({Authorization:'Bearer '+token})});
  const originalInvoke=c.invoke.bind(c);
  c.invoke=(...args)=>{programInvoke++;return originalInvoke(...args);};
  assert.equal((await c.connect()).status,'CONNECTED');
  const port=createHttpSourcePort({endpoint:base+'/source',sourceId:'example.api',
   authHeaders:async()=>({Authorization:'Bearer '+token})});
  const seen=await port.refresh();assert.equal(seen.status,'OK');
  const read=await port.read(pinSource(port.describe(),seen));assert.equal(read.status,'OK');
  assert.equal(Buffer.from(read.content.data,'base64').toString(),'A');
  assert.equal(programInvoke,0);
  assert.equal(c.capabilities().operations[0].verified,false);
  assert.equal((await c.invoke('evaluate')).status,'EXECUTED');
  assert.equal(programInvoke,1);
  assert.equal(c.capabilities().operations[0].verified,true);
  source='B';assert.equal((await port.read(pinSource(port.describe(),seen))).status,'REVISION_MISMATCH');
  token='expired';assert.equal((await port.refresh()).status,'AUTHORIZATION_REQUIRED');
  assert.equal((await c.invoke('evaluate')).status,'AUTHORIZATION_REQUIRED');
  assert.equal(c.capabilities().connected,false);
  token='synthetic';assert.equal((await c.recover()).status,'CONNECTED');
  assert.equal(c.capabilities().operations[0].verified,false);
  assert.equal(programInvoke,2);assert.equal(sourceGets,4);
 }finally {await new Promise(resolve=>server.close(resolve));}
});
