import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,writeFile,rename,rm} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {createLocalFileSourcePort} from '../local-file.mjs';
import {createHostSelectedSourcePort} from '../host-source.mjs';
import {prepareSelectedTaskSources,TASK_INPUTS_SCHEMA} from '../selected-task-inputs.mjs';

const enc=text=>new TextEncoder().encode(text);
async function fixture(){
 const root=await mkdtemp(join(tmpdir(),'u02-task-inputs-'));
 await mkdir(join(root,'docs'));
 await writeFile(join(root,'docs','plan.md'),'Plan first revision');
 await writeFile(join(root,'docs','status.json'),'{"stage":"ready"}');
 const select=()=>[
   {key:'plan',format:'markdown',port:createLocalFileSourcePort({
     root,rootId:'project_root',relativePath:'docs/plan.md'})},
   {key:'status',format:'json',port:createLocalFileSourcePort({
     root,rootId:'project_root',relativePath:'docs/status.json'})}
 ];
 return {root,select};
}

test('Task inputs: ready only after final read-only revalidation and no second pinned reads',async()=>{
 const p=await fixture();
 try {
   const result=await prepareSelectedTaskSources({scopeId:'workspace_1',sources:p.select()});
   assert.equal(result.schema,TASK_INPUTS_SCHEMA);
   assert.equal(result.status,'READY');
   assert.equal(result.ready,true);
   assert.equal(result.authority,'OBSERVATION_ONLY');
   assert.equal(result.observations.length,2);
   assert.equal(result.checkpoint.sources.length,2);
   assert.deepEqual(result.issues,[]);
   assert.equal(result.metrics.read.pinnedReadCalls,2);
   assert.equal(result.metrics.read.refreshCalls,2);
   assert.equal(result.metrics.revalidation.pinnedReadCalls,0);
   assert.equal(result.metrics.revalidation.refreshCalls,2);
   assert.ok(result.observations[0].text.includes('first revision'));
   assert.equal(result.observations.every(x=>x.epistemic==='observed'),true);
   assert.ok(!JSON.stringify(result.checkpoint).includes('first revision'));
   const saved=JSON.parse(JSON.stringify(result.checkpoint));
   const reopened=await prepareSelectedTaskSources({
     scopeId:'workspace_1',sources:p.select(),previous:saved});
   assert.equal(reopened.status,'READY');
   assert.deepEqual(reopened.checkpoint,saved);
   assert.equal(reopened.observations.length,2);
 } finally {await rm(p.root,{recursive:true,force:true});}
});

test('Task inputs: modified file during later source read blocks all new Task content',async()=>{
 const p=await fixture();
 try{
   const source=p.select();
   const other=source[1].port;
   source[1]={...source[1],port:{
     describe:()=>other.describe(),refresh:()=>other.refresh(),
     read:async pin=>{
       const observation=await other.read(pin);
       await writeFile(join(p.root,'docs','plan.md'),'Plan second revision');
       return observation;
     }
   }};
   const failed=await prepareSelectedTaskSources({scopeId:'workspace_1',sources:source});
   assert.equal(failed.status,'INCOMPLETE');
   assert.equal(failed.ready,false);
   assert.equal(failed.checkpoint,null);
   assert.deepEqual(failed.observations,[]);
   assert.equal(failed.issues.length,1);
   assert.equal(failed.issues[0].key,'plan');
   assert.equal(failed.issues[0].status,'STALE_AFTER_READ');
   assert.ok(!JSON.stringify(failed).includes('first revision'));
   const recovered=await prepareSelectedTaskSources({
     scopeId:'workspace_1',sources:p.select()});
   assert.equal(recovered.status,'READY');
   assert.equal(recovered.observations[0].text,'Plan second revision');
 }finally{await rm(p.root,{recursive:true,force:true});}
});

test('Task inputs: missing file, refusal, or format failure never publish partial content',async()=>{
 const p=await fixture();
 try{
   await rename(join(p.root,'docs','status.json'),join(p.root,'docs','moved.json'));
   const missing=await prepareSelectedTaskSources({scopeId:'workspace_1',sources:p.select()});
   assert.equal(missing.status,'INCOMPLETE');
   assert.equal(missing.issues[0].status,'SOURCE_MISSING');
   assert.equal(missing.issues[0].key,'status');
   assert.equal(missing.checkpoint,null);
   assert.equal(missing.observations.length,0);
   assert.equal(JSON.stringify(missing).includes('Plan first revision'),false);

   const denied=createHostSelectedSourcePort({hostId:'agent',scopeId:'workspace_1',
     sourceId:'remote',bindingId:'b1',
     readSelected:()=>({status:'ACCESS_DENIED'})});
   const mixed=await prepareSelectedTaskSources({scopeId:'workspace_1',sources:[
      p.select()[0],{key:'remote',format:'text',port:denied}
   ]});
   assert.equal(mixed.status,'INCOMPLETE');
   assert.equal(mixed.issues[0].status,'ACCESS_DENIED');
   assert.deepEqual(mixed.observations,[]);

   const unsupported=await prepareSelectedTaskSources({scopeId:'workspace_1',
     sources:[{...p.select()[0],format:'exe'}]});
   assert.equal(unsupported.status,'INCOMPLETE');
   assert.equal(unsupported.issues[0].status,'FORMAT_UNSUPPORTED');
 }finally{await rm(p.root,{recursive:true,force:true});}
});

test('Task inputs: host superseded selection cannot generate stale Task context',async()=>{
 let fresh=true,calls=0;
 const port=createHostSelectedSourcePort({hostId:'agent',scopeId:'workspace_1',
   sourceId:'file',bindingId:'a',
   readSelected:()=>{
     calls++;if(calls===2)fresh=false;
     return {status:'OK',bytes:enc('confidential text')};
   }});
 const stopped=await prepareSelectedTaskSources({scopeId:'workspace_1',
   sources:[{key:'selected',format:'text',port}],isCurrent:()=>fresh});
 assert.equal(stopped.status,'SUPERSEDED');
 assert.equal(stopped.ready,false);
 assert.equal(stopped.checkpoint,null);
 assert.deepEqual(stopped.observations,[]);
 assert.equal(stopped.metrics.revalidation,null);
 assert.equal(calls,2);
 assert.equal(JSON.stringify(stopped).includes('confidential'),false);
 const before=await prepareSelectedTaskSources({scopeId:'workspace_1',
   sources:[{key:'selected',format:'text',port}],
   isCurrent:()=>{throw Error('host cancelled')}});
 assert.equal(before.status,'SUPERSEDED');
 assert.equal(before.metrics,null);
 assert.equal(calls,2);
});

test('Task inputs: request superseded during final refresh publishes no Task evidence',async()=>{
 let current=true,calls=0;
 const port=createHostSelectedSourcePort({hostId:'agent',scopeId:'workspace_1',
   sourceId:'file',bindingId:'b',
   readSelected:()=>{
     calls++;
     if(calls===3)current=false;
     return {status:'OK',bytes:enc('old context')};
   }});
 const result=await prepareSelectedTaskSources({scopeId:'workspace_1',
   sources:[{key:'selected',format:'text',port}],isCurrent:()=>current});
 assert.equal(result.status,'SUPERSEDED');
 assert.equal(result.checkpoint,null);
 assert.deepEqual(result.observations,[]);
 assert.equal(result.metrics.read.pinnedReadCalls,1);
 assert.equal(result.metrics.revalidation.pinnedReadCalls,0);
 assert.equal(result.metrics.revalidation.refreshCalls,1);
 assert.equal(calls,3);
 assert.ok(!JSON.stringify(result).includes('old context'));
});

test('Task inputs: no selected sources never report a ready Task context',async()=>{
 const result=await prepareSelectedTaskSources({scopeId:'workspace_1',sources:[]});
 assert.equal(result.status,'INCOMPLETE');
 assert.equal(result.issues[0].status,'NO_SOURCES_SELECTED');
 assert.equal(result.ready,false);
 assert.equal(result.checkpoint,null);
 assert.deepEqual(result.observations,[]);
 assert.equal(result.metrics.read.refreshCalls,0);
});

test('Task inputs: retained checkpoint cannot authorize a re-bound provider',async()=>{
 const p=await fixture();
 try{
   const initial=await prepareSelectedTaskSources({scopeId:'workspace_1',sources:p.select()});
   assert.equal(initial.status,'READY');
   const switched=createLocalFileSourcePort({
     root:p.root,rootId:'other_binding',relativePath:'docs/plan.md'});
   const follow=await prepareSelectedTaskSources({scopeId:'workspace_1',
     sources:[{key:'plan',format:'markdown',port:switched},
       p.select()[1]],previous:initial.checkpoint});
   assert.equal(follow.status,'INCOMPLETE');
   assert.equal(follow.issues[0].status,'SOURCE_REBIND_REQUIRED');
   assert.equal(follow.checkpoint,null);
   assert.deepEqual(follow.observations,[]);
 } finally {await rm(p.root,{recursive:true,force:true});}
});


test('Task inputs: switching project during first read prevents all later host source reads',async()=>{
 let active=true, firstCalls=0, laterCalls=0;
 const first=createHostSelectedSourcePort({
  hostId:'agent',scopeId:'workspace_1',sourceId:'first',bindingId:'a',
  readSelected:()=>{
   firstCalls++;
   if(firstCalls===2)active=false;
   return {status:'OK',bytes:enc('First document')};
  }
 });
 const later=createHostSelectedSourcePort({
  hostId:'agent',scopeId:'workspace_1',sourceId:'remote',bindingId:'a',
  readSelected:()=>{laterCalls++;return {status:'OK',bytes:enc('Remote document')}};
 });
 const result=await prepareSelectedTaskSources({scopeId:'workspace_1',
  sources:[{key:'first',format:'text',port:first},
   {key:'remote',format:'text',port:later}],
  isCurrent:()=>active});
 assert.equal(result.status,'SUPERSEDED');
 assert.equal(result.ready,false);
 assert.equal(result.checkpoint,null);
 assert.deepEqual(result.observations,[]);
 assert.equal(firstCalls,2);
 assert.equal(laterCalls,0,'superseded selection must not open remote source');
 assert.equal(result.metrics.revalidation,null);
 assert.ok(!JSON.stringify(result).includes('First document'));
});

test('Task inputs: cancel after first refresh prevents first pinned read and all later I/O',async()=>{
 let active=true,firstCalls=0,laterCalls=0;
 const first=createHostSelectedSourcePort({
  hostId:'agent',scopeId:'workspace_1',sourceId:'first',bindingId:'b',
  readSelected:()=>{
   firstCalls++;
   active=false;
   return {status:'OK',bytes:enc('first source')};
  }
 });
 const second=createHostSelectedSourcePort({
  hostId:'agent',scopeId:'workspace_1',sourceId:'second',bindingId:'b',
  readSelected:()=>{laterCalls++;return {status:'OK',bytes:enc('second source')}};
 });
 const result=await prepareSelectedTaskSources({
  scopeId:'workspace_1',
  sources:[{key:'first',format:'text',port:first},
   {key:'second',format:'text',port:second}],
  isCurrent:()=>active
 });
 assert.equal(result.status,'SUPERSEDED');
 assert.deepEqual(result.observations,[]);
 assert.equal(result.checkpoint,null);
 assert.equal(firstCalls,1);
 assert.equal(laterCalls,0);
 assert.equal(result.metrics.read.pinnedReadCalls,0);
});

test('Task inputs: cancellation in revalidation skips later owner refreshes',async()=>{
 let active=true,firstCalls=0,laterCalls=0;
 const first=createHostSelectedSourcePort({
  hostId:'agent',scopeId:'workspace_1',sourceId:'first',bindingId:'c',
  readSelected:()=>{
   firstCalls++;
   if(firstCalls===3)active=false;
   return {status:'OK',bytes:enc('first source')};
  }
 });
 const second=createHostSelectedSourcePort({
  hostId:'agent',scopeId:'workspace_1',sourceId:'second',bindingId:'c',
  readSelected:()=>{laterCalls++;return {status:'OK',bytes:enc('second source')}};
 });
 const result=await prepareSelectedTaskSources({
  scopeId:'workspace_1',
  sources:[{key:'first',format:'text',port:first},
   {key:'second',format:'text',port:second}],
  isCurrent:()=>active
 });
 assert.equal(result.status,'SUPERSEDED');
 assert.deepEqual(result.observations,[]);
 assert.equal(result.checkpoint,null);
 assert.equal(firstCalls,3,'one final revision observation is allowed');
 assert.equal(laterCalls,2,'second source must not be reobserved after cancel');
 assert.equal(result.metrics.revalidation.pinnedReadCalls,0);
});
