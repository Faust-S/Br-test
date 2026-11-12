import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,writeFile,rm} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {createLocalFileSourcePort} from '../local-file.mjs';
import {readSelectedProjectSources,checkpointSelectedProjectSources,
  projectSourceObservationsForKnowledge} from '../selected-project.mjs';
import {revalidateSelectedProjectRead,PROJECT_REVALIDATION_SCHEMA}
  from '../selected-project-revalidate.mjs';

const setup=async()=>{
 const root=await mkdtemp(join(tmpdir(),'u02-final-snapshot-'));
 await mkdir(join(root,'docs'));
 await writeFile(join(root,'docs','plan.md'),'Plan v1');
 await writeFile(join(root,'docs','status.json'),'{"state":"ready"}');
 const select=()=>['docs/plan.md','docs/status.json'].map((path,i)=>({
   key:i?'status':'plan',format:i?'json':'markdown',
   port:createLocalFileSourcePort({root,rootId:'project_revalidate',relativePath:path})
 }));
 return {root,select};
};

test('final coherence rejects a document changed while another source was read',async()=>{
 const {root,select}=await setup();
 try{
  const selected=select();
  const second=selected[1].port;
  selected[1]={...selected[1],port:{
    describe:()=>second.describe(),
    refresh:()=>second.refresh(),
    read:async pin=>{
      const result=await second.read(pin);
      await writeFile(join(root,'docs','plan.md'),'Plan v2');
      return result;
    }
  }};
  const initial=await readSelectedProjectSources({scopeId:'project_a',sources:selected});
  assert.equal(initial.complete,true);
  assert.equal(initial.sources[0].text,'Plan v1');
  const checked=await revalidateSelectedProjectRead({view:initial,sources:selected});
  assert.equal(checked.complete,false);
  assert.equal(checked.sources[0].status,'STALE_AFTER_READ');
  assert.equal(checked.sources[0].change,'CHANGED_DURING_PROJECT');
  assert.equal(checked.sources[0].finalRevisionConfirmed,false);
  assert.equal('text' in checked.sources[0],false);
  assert.equal('pin' in checked.sources[0],false);
  assert.equal(checked.sources[0].lastKnown,null);
  assert.equal(checked.sources[1].finalRevisionConfirmed,true);
  assert.equal(checked.revalidation.schema,PROJECT_REVALIDATION_SCHEMA);
  assert.equal(checked.revalidation.refreshCalls,2);
  assert.equal(checked.revalidation.pinnedReadCalls,0);
  assert.equal(checked.revalidation.allCurrent,false);
  assert.equal(checked.metrics.returnedTextBytes,checked.sources[1].bytes);
  assert.deepEqual(projectSourceObservationsForKnowledge(checked).map(x=>x.key),['status']);
  assert.deepEqual(checkpointSelectedProjectSources(checked).sources.map(x=>x.key),['status']);
  assert.equal(initial.sources[0].text,'Plan v1','historical observation was not mutated');
 }finally{await rm(root,{recursive:true,force:true});}
});

test('unchanged project revalidates without a second pinned read',async()=>{
 const {root,select}=await setup();
 try{
  const selected=select();
  const view=await readSelectedProjectSources({scopeId:'project_a',sources:selected});
  let readCalls=0,refreshCalls=0;
  const probe=selected.map(item=>({...item,port:{
    describe:()=>item.port.describe(),
    refresh:()=>{refreshCalls++;return item.port.refresh();},
    read:()=>{readCalls++;throw Error('REVALIDATION_MUST_NOT_READ')}
  }}));
  const checked=await revalidateSelectedProjectRead({view,sources:probe});
  assert.equal(checked.complete,true);
  assert.equal(checked.revalidation.allCurrent,true);
  assert.equal(checked.revalidation.contentRead,false);
  assert.equal(checked.revalidation.returnedTextBytes,0);
  assert.equal(checked.revalidation.pinnedReadCalls,0);
  assert.equal(refreshCalls,2);assert.equal(readCalls,0);
  assert.equal(checked.sources.every(row=>row.finalRevisionConfirmed===true),true);
  assert.equal(projectSourceObservationsForKnowledge(checked).length,2);
 }finally{await rm(root,{recursive:true,force:true});}
});

test('lost access and changed provider binding discard formerly readable evidence',async()=>{
 const {root,select}=await setup();
 try{
  const selected=select();
  const view=await readSelectedProjectSources({scopeId:'project_a',sources:selected});
  const first=selected[0].port;
  const unavailable=[{...selected[0],port:{
    describe:()=>first.describe(),
    refresh:async()=>{throw Error('offline')},
    read:()=>{throw Error('not permitted')}
  }},selected[1]];
  const failed=await revalidateSelectedProjectRead({view,sources:unavailable});
  assert.equal(failed.complete,false);
  assert.equal(failed.sources[0].status,'SOURCE_UNAVAILABLE');
  assert.equal('text' in failed.sources[0],false);
  assert.equal(projectSourceObservationsForKnowledge(failed).length,1);
  const foreign=createLocalFileSourcePort({root,rootId:'different_scope',relativePath:'docs/plan.md'});
  let refreshed=0;
  const mismatched=await revalidateSelectedProjectRead({view,sources:[
    {...selected[0],port:{describe:()=>foreign.describe(),
      refresh:()=>{refreshed++;return foreign.refresh()},read:()=>{throw Error('no')}}},
    selected[1]
  ]});
  assert.equal(mismatched.sources[0].status,'SOURCE_REBIND_REQUIRED');
  assert.equal(mismatched.revalidation.refreshCalls,1);
  assert.equal(refreshed,0);
 }finally{await rm(root,{recursive:true,force:true});}
});

test('invalid saved pins or reordered source selection cannot be passed as verified',async()=>{
 const {root,select}=await setup();
 try{
  const selected=select();
  const view=await readSelectedProjectSources({scopeId:'project_a',sources:selected});
  const altered={...view,sources:[{...view.sources[0],
    lastKnown:{...view.sources[0].lastKnown,contentRef:'sha256:'+'0'.repeat(64)}},
    view.sources[1]]};
  await assert.rejects(()=>revalidateSelectedProjectRead({view:altered,sources:selected}),
    /PROJECT_REVALIDATION_PIN_INVALID/);
  await assert.rejects(()=>revalidateSelectedProjectRead({view,sources:selected.toReversed()}),
    /PROJECT_REVALIDATION_INPUT_INVALID/);
 }finally{await rm(root,{recursive:true,force:true});}
});
