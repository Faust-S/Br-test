import test from 'node:test';
import assert from 'node:assert/strict';
import {createHostSelectedSourcePort} from '../host-source.mjs';
import {readSelectedProjectSources,inspectSelectedProjectSources,
  projectSourceObservationsForKnowledge} from '../selected-project.mjs';

const mk=(sourceId,label)=>createHostSelectedSourcePort({
 hostId:'agent',scopeId:'workspace-a',sourceId,bindingId:'selection-1',
 readSelected:async()=>({status:'OK',bytes:new TextEncoder().encode(label)})
});

test('async host selection change cannot attribute source B bytes to source A',async()=>{
 const a=mk('document-a','Document A'), b=mk('document-b','Document B');
 let describes=0,reads=0;
 const unstable={
   describe:()=>++describes===1?a.describe():b.describe(),
   refresh:()=>b.refresh(),
   read:async pin=>{reads++;return b.read(pin)}
 };
 const view=await readSelectedProjectSources({scopeId:'workspace-a',sources:[
  {key:'selected',format:'text',port:unstable}]});
 assert.equal(view.complete,false);
 assert.equal(view.sources[0].status,'SOURCE_REBIND_REQUIRED');
 assert.equal(view.sources[0].change,'REBIND_REQUIRED');
 assert.equal('text' in view.sources[0],false);
 assert.equal('pin' in view.sources[0],false);
 assert.equal(projectSourceObservationsForKnowledge(view).length,0);
 assert.equal(reads,1,'host read already underway must not be attributed to old source');
 assert.equal(view.metrics.pinnedReadCalls,1,'measure actual reads, including rejected bindings');
});

test('refresh-time host selection change returns rebind instead of false current',async()=>{
 const a=mk('document-a','Document A'), b=mk('document-b','Document B');
 let selected=a,reads=0;
 const unstable={
   describe:()=>selected.describe(),
   refresh:async()=>{selected=b;return b.refresh()},
   read:()=>{reads++;throw Error('REFRESH_ONLY')}
 };
 const response=await inspectSelectedProjectSources({scopeId:'workspace-a',sources:[
   {key:'selected',format:'text',port:unstable}
 ]});
 assert.equal(response.observationsComplete,false);
 assert.equal(response.sources[0].status,'SOURCE_REBIND_REQUIRED');
 assert.equal(response.sources[0].change,'REBIND_REQUIRED');
 assert.equal(response.metrics.refreshCalls,1);
 assert.equal(response.metrics.pinnedReadCalls,0);
 assert.equal(reads,0);
});

test('stable selected source still supports full read and cheap freshness',async()=>{
 const a=mk('document-a','Document A');
 const select=[{key:'selected',format:'text',port:a}];
 const full=await readSelectedProjectSources({scopeId:'workspace-a',sources:select});
 assert.equal(full.complete,true);
 assert.equal(full.sources[0].text,'Document A');
 const observed=await inspectSelectedProjectSources({scopeId:'workspace-a',sources:select});
 assert.equal(observed.observationsComplete,true);
 assert.equal(observed.sources[0].freshness,'NOT_PINNED');
});
