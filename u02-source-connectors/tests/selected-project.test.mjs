import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp, mkdir, writeFile, rename, rm, readFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createLocalFileSourcePort} from '../local-file.mjs';
import {createHostSelectedSourcePort} from '../host-source.mjs';
import {readSelectedProjectSources,checkpointSelectedProjectSources,
  projectSourceObservationsForKnowledge} from '../selected-project.mjs';

async function project() {
 const root=await mkdtemp(join(tmpdir(),'u02-project-files-'));
 await mkdir(join(root,'docs'));
 await writeFile(join(root,'docs','plan.md'),'# Plan\nPhase 1');
 await writeFile(join(root,'docs','budget.csv'),'line,cost\nresearch,400');
 await writeFile(join(root,'docs','status.json'),JSON.stringify({stage:'planning',revision:1}));
 const ports=(path,key)=>({key,format:path.endsWith('.json')?'json':
    path.endsWith('.csv')?'csv':'markdown',
    port:createLocalFileSourcePort({root,rootId:'selected_project',relativePath:path})});
 return {root,entries:()=>[
   ports('docs/plan.md','plan'),ports('docs/budget.csv','budget'),
   ports('docs/status.json','status')]};
}

test('selected project produces three independently verified files, provenance and a restart checkpoint',async()=>{
 const p=await project();
 try{
   const view=await readSelectedProjectSources({scopeId:'project_a',sources:p.entries()});
   assert.equal(view.complete,true);
   assert.equal(view.sources.length,3);
   assert.equal(view.metrics.refreshCalls,3);
   assert.equal(view.metrics.pinnedReadCalls,3);
   assert.equal(view.sources.every(s=>s.authority==='OBSERVATION_ONLY'&&
     s.provenance.rootId==='selected_project'&&s.text.length>0),true);
   assert.equal(projectSourceObservationsForKnowledge(view).length,3);
   const saved=JSON.parse(JSON.stringify(checkpointSelectedProjectSources(view)));
   assert.equal(saved.sources.length,3);
   assert.equal(JSON.stringify(saved).includes('Phase 1'),false);
   // New U02 objects, independent consumer, no U02 persistent state.
   const again=await readSelectedProjectSources({scopeId:'project_a',sources:p.entries(),previous:saved});
   assert.equal(again.complete,true);
   assert.deepEqual(again.sources.map(s=>s.change),['UNCHANGED','UNCHANGED','UNCHANGED']);
   assert.equal(again.metrics.refreshCalls,3);
   assert.equal(again.metrics.pinnedReadCalls,3);
 }finally{await rm(p.root,{recursive:true,force:true});}
});

test('one edited document is CHANGED and old bytes never silently reused',async()=>{
 const p=await project();
 try{
  const initial=await readSelectedProjectSources({scopeId:'project_a',sources:p.entries()});
  const saved=checkpointSelectedProjectSources(initial);
  await writeFile(join(p.root,'docs','plan.md'),'# Plan\nPhase 2 is approved');
  const changed=await readSelectedProjectSources({scopeId:'project_a',
    sources:p.entries(),previous:saved});
  assert.equal(changed.sources[0].status,'OK');
  assert.equal(changed.sources[0].change,'CHANGED');
  assert.notEqual(changed.sources[0].contentRef,initial.sources[0].contentRef);
  assert.match(changed.sources[0].text,/Phase 2 is approved/);
  assert.doesNotMatch(changed.sources[0].text,/Phase 1/);
  assert.deepEqual(changed.sources.slice(1).map(s=>s.change),['UNCHANGED','UNCHANGED']);
 }finally{await rm(p.root,{recursive:true,force:true});}
});

test('missing, moved, replaced binding, inaccessible and unsupported files are not marked current',async()=>{
 const p=await project();
 try{
  const first=await readSelectedProjectSources({scopeId:'project_a',sources:p.entries()});
  const saved=checkpointSelectedProjectSources(first);
  await rename(join(p.root,'docs','plan.md'),join(p.root,'docs','moved.md'));
  const after=await readSelectedProjectSources({scopeId:'project_a',sources:p.entries(),previous:saved});
  assert.equal(after.sources[0].status,'SOURCE_MISSING');
  assert.equal(after.sources[0].change,'MISSING');
  assert.equal(after.complete,false);
  assert.equal(projectSourceObservationsForKnowledge(after).length,2);
  const replaced={...p.entries()[0],port:createLocalFileSourcePort({
    root:p.root,rootId:'another_scope',relativePath:'docs/moved.md'})};
  const rebound=await readSelectedProjectSources({scopeId:'project_a',sources:[replaced],previous:saved});
  assert.equal(rebound.sources[0].status,'SOURCE_REBIND_REQUIRED');
  assert.equal(rebound.metrics.refreshCalls,0);
  const notSupported=await readSelectedProjectSources({scopeId:'project_a',sources:[
    {...p.entries()[1],format:'exe'}]});
  assert.equal(notSupported.sources[0].status,'FORMAT_UNSUPPORTED');
  assert.equal(notSupported.metrics.refreshCalls,0);
  const denied=createHostSelectedSourcePort({hostId:'agent',scopeId:'project_a',
    sourceId:'external',bindingId:'host_a',readSelected:()=>({status:'ACCESS_DENIED'})});
  const fail=await readSelectedProjectSources({scopeId:'project_a',sources:[
    {key:'denied',format:'text',port:denied}]});
  assert.equal(fail.sources[0].status,'ACCESS_DENIED');
  assert.equal(fail.complete,false);
  assert.equal(fail.metrics.pinnedReadCalls,0);
  await assert.rejects(()=>readSelectedProjectSources({scopeId:'different',
    sources:p.entries(),previous:saved}),/PROJECT_CHECKPOINT_INVALID/);
  assert.throws(()=>createLocalFileSourcePort({root:p.root,rootId:'selected_project',
    relativePath:'../escape.md'}),/LOCAL_SOURCE_PATH_INVALID/);
 }finally{await rm(p.root,{recursive:true,force:true});}
});

test('invalid JSON, NUL byte, byte-budget and host errors do not create knowledge evidence',async()=>{
 const p=await project();
 try{
  await writeFile(join(p.root,'docs','status.json'),'{invalid');
  const invalid=await readSelectedProjectSources({scopeId:'project_a',sources:[p.entries()[2]]});
  assert.equal(invalid.sources[0].status,'FORMAT_INVALID');
  assert.equal(projectSourceObservationsForKnowledge(invalid).length,0);
  await writeFile(join(p.root,'docs','status.json'),JSON.stringify({valid:true}));
  await writeFile(join(p.root,'docs','plan.md'),Buffer.from([65,0,66]));
  const corrupt=await readSelectedProjectSources({scopeId:'project_a',sources:[p.entries()[0]]});
  assert.equal(corrupt.sources[0].status,'FORMAT_INVALID');
  const tight=await readSelectedProjectSources({scopeId:'project_a',sources:p.entries(),maxTotalBytes:6});
  assert.equal(tight.complete,false);
  assert.ok(tight.sources.some(s=>s.status==='PROJECT_BYTE_LIMIT'));
  const network=createHostSelectedSourcePort({hostId:'agent',scopeId:'project_a',
    sourceId:'api',bindingId:'api_a',readSelected:async()=>{throw Error('network offline')}});
  const offline=await readSelectedProjectSources({scopeId:'project_a',sources:[
    {key:'network',format:'json',port:network}]});
  assert.equal(offline.sources[0].status,'SOURCE_UNAVAILABLE');
 }finally{await rm(p.root,{recursive:true,force:true});}
});
