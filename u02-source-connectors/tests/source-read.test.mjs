import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {captureSource, readPinnedSource} from '../source-read.mjs';
import {sourceDescriptor, sourceObservation} from '../port.mjs';
import {createHttpSourcePort} from '../http-source.mjs';

const digest = b => 'sha256:' + createHash('sha256').update(b).digest('hex');
const now = () => '2026-11-04T00:00:00.000Z';
const desc = sourceDescriptor({providerId:'file.local',sourceRef:'file://doc/a'});
const o = (status,revision=null,options={}) => sourceObservation(desc,{
  status,revision,observedAt:now(),provenance:{provider:'file'},...options
});

test('capture pins and reads content from a real HTTP source; no implicit writes',async()=>{
  let value='one',requests=0;
  const fetcher=async(url,params)=>{requests++;assert.equal(params.method,'GET');
    return {ok:true,status:200,url,headers:{get:()=>null},arrayBuffer:async()=>Buffer.from(value)};};
  const port=createHttpSourcePort({endpoint:'https://example.test/api',sourceId:'docs.api',fetcher});
  const first=await captureSource(port);
  assert.equal(first.status,'OK');assert.equal(first.pin.revision,digest('one'));
  assert.equal(Buffer.from(first.read.content.data,'base64').toString(),'one');
  value='two';
  const stale=await readPinnedSource(port,JSON.parse(JSON.stringify(first.pin)));
  assert.equal(stale.status,'REVISION_MISMATCH');assert.equal(stale.content,null);
  const next=await captureSource(port);assert.equal(next.status,'OK');
  assert.equal(next.pin.revision,digest('two'));assert.equal(requests,5);
});

test('observation-only program does not try to pin or invoke',async()=>{
  let read=0;
  const descriptor=sourceDescriptor({providerId:'program.u01',sourceRef:'program://JQ_CLI',
    observationType:'program',capabilities:[{id:'source.describe',available:true},
      {id:'source.refresh',available:true},{id:'source.read',available:false}]});
  const port={describe:()=>descriptor,refresh:()=>sourceObservation(descriptor,{status:'OK',
    observedAt:now(),provenance:{provider:'program'},contentRef:digest('capability')}),
    read:()=>{read++;throw Error('READ_NOT_ALLOWED');}};
  const view=await captureSource(port);
  assert.equal(view.status,'OK');assert.equal(view.pin,null);assert.equal(view.read,null);assert.equal(read,0);
});

test('unavailable source returns observation without fabricating content',async()=>{
  const port={describe:()=>desc,refresh:()=>o('SOURCE_MISSING'),read:()=>{throw Error('NO');}};
  const result=await captureSource(port);
  assert.equal(result.status,'SOURCE_MISSING');assert.equal(result.read,null);
});

test('change between refresh and read is reported, never retried silently',async()=>{
  let count=0;
  const port={describe:()=>desc,refresh:()=>o('OK',digest('old')),
    read:()=>{count++;return o('REVISION_MISMATCH',digest('new'),{content:{encoding:'base64',data:'c2VjcmV0'}})}};
  const result=await captureSource(port);
  assert.equal(result.status,'REVISION_MISMATCH');assert.equal(result.read.content,null);
  assert.equal(count,1);
});

test('malformed or mismatched content cannot cross the consumer boundary',async()=>{
  const rev=digest('correct');
  for(const override of [
    {contentRef:rev,content:{encoding:'base64',data:'YWJj'}},
    {contentRef:digest('other'),content:{encoding:'base64',data:Buffer.from('correct').toString('base64')}},
    {contentRef:rev,content:{encoding:'base64',data:'YW=/'}}
  ]) {
    const port={describe:()=>desc,refresh:()=>o('OK',rev),
      read:()=>o('OK',rev,{bytes:7,...override})};
    await assert.rejects(()=>captureSource(port),/SOURCE_READ_INTEGRITY_INVALID/);
  }
});

test('identity and authority cannot be overwritten through a fake adapter',async()=>{
  const port={describe:()=>desc,refresh:()=>({...o('OK',digest('one')),authority:'WRITE_GRANTED'}),read:()=>null};
  await assert.rejects(()=>captureSource(port),/SOURCE_PORT_CONTRACT_INVALID/);
  await assert.rejects(()=>readPinnedSource(port,{...desc,revision:digest('one'),sourceRef:'file://other'}),/SOURCE_PIN_INVALID/);
});
