import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp, mkdir, writeFile, rename, rm} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {createHash} from 'node:crypto';
import {createLocalFileSourcePort} from '../local-file.mjs';
import {createAtlasGitHubSourcePort} from '../atlas-github.mjs';
import {sourceDescriptor, sourceObservation, pinSource} from '../port.mjs';
import {readSelectedProjectSources,checkpointSelectedProjectSources,
  inspectSelectedProjectSources,projectSourceObservationsForKnowledge,
  PROJECT_FRESHNESS_SCHEMA} from '../selected-project.mjs';

const sha=bytes=>'sha256:'+createHash('sha256').update(bytes).digest('hex');
const saved=(key,descriptor,revision,contentRef,bytes)=>({
  key,pin:sourceDescriptor({...descriptor,revision}),contentRef,bytes
});

test('restart freshness check reads zero source bytes and keeps historical evidence separate',async()=>{
 const root=await mkdtemp(join(tmpdir(),'u02-freshness-'));
 try{
   await mkdir(join(root,'docs'));
   await writeFile(join(root,'docs','plan.md'),'Project plan v1');
   await writeFile(join(root,'docs','budget.csv'),'item,cost\nreview,400');
   const make=path=>createLocalFileSourcePort({root,rootId:'project_a',relativePath:path});
   const selected=()=>[
    {key:'plan',format:'markdown',port:make('docs/plan.md')},
    {key:'budget',format:'csv',port:make('docs/budget.csv')}
   ];
   const initial=await readSelectedProjectSources({scopeId:'project_a',sources:selected()});
   const checkpoint=JSON.parse(JSON.stringify(checkpointSelectedProjectSources(initial)));
   let reads=0;
   const wrapped=selected().map(item=>({...item,port:{
     describe:()=>item.port.describe(),
     refresh:()=>item.port.refresh(),
     read:()=>{reads++;throw Error('MUST_NOT_READ_SOURCE_BYTES')}
   }}));
   const result=await inspectSelectedProjectSources({
     scopeId:'project_a',sources:wrapped,previous:checkpoint
   });
   assert.equal(result.schema,PROJECT_FRESHNESS_SCHEMA);
   assert.equal(result.contentRead,false);
   assert.equal(result.evidenceReady,false);
   assert.equal(result.observationsComplete,true);
   assert.equal(result.metrics.refreshCalls,2);
   assert.equal(result.metrics.pinnedReadCalls,0);
   assert.equal(result.metrics.returnedTextBytes,0);
   assert.equal(reads,0);
   assert.deepEqual(result.sources.map(x=>x.freshness),['CURRENT','CURRENT']);
   assert.equal(result.sources.every(x=>!('text' in x)&&!('content' in x)),true);
   assert.throws(()=>projectSourceObservationsForKnowledge(result),/PROJECT_VIEW_INVALID/);
   await writeFile(join(root,'docs','plan.md'),'Project plan v2');
   await rename(join(root,'docs','budget.csv'),join(root,'docs','budget-moved.csv'));
   const changed=await inspectSelectedProjectSources({
     scopeId:'project_a',sources:wrapped,previous:checkpoint
   });
   assert.equal(changed.sources[0].freshness,'STALE');
   assert.equal(changed.sources[0].change,'CHANGED');
   assert.equal(changed.sources[1].status,'SOURCE_MISSING');
   assert.equal(changed.sources[1].change,'MISSING');
   assert.equal(changed.observationsComplete,false);
   assert.equal(reads,0);
   assert.equal(changed.metrics.pinnedReadCalls,0);
   const rebuilt=await readSelectedProjectSources({
     scopeId:'project_a',sources:[selected()[0]],previous:checkpoint
   });
   assert.match(rebuilt.sources[0].text,/plan v2/);
   assert.equal(rebuilt.sources[0].change,'CHANGED');
 }finally{await rm(root,{recursive:true,force:true});}
});

test('GitHub source checks current branch commit without requesting pinned file content',async()=>{
 let head='a'.repeat(40),reads=0,observations=0;
 const port=createAtlasGitHubSourcePort({
   repository:'Faust-S/Atlas',branch:'main',relativePath:'LICENSE',
   observePublicRepo:async repository=>{
     observations++;return {scope:'repository',repository,complete:true,branchHeads:{main:head}};
   },
   fetcher:async()=>{reads++;throw Error('MUST_NOT_FETCH_PINNED_FILE')}
 });
 const first=await port.refresh();
 const checkpoint={schema:'IWXA_U02_SELECTED_PROJECT_CHECKPOINT/1',scopeId:'project_a',
  sources:[saved('github',port.describe(),first.revision,sha('previously verified bytes'),25)]};
 const sources=[{key:'github',format:'text',port}];
 const current=await inspectSelectedProjectSources({scopeId:'project_a',sources,previous:checkpoint});
 assert.equal(current.sources[0].freshness,'CURRENT');
 assert.equal(current.metrics.refreshCalls,1);
 assert.equal(current.metrics.pinnedReadCalls,0);
 assert.equal(reads,0);
 head='b'.repeat(40);
 const stale=await inspectSelectedProjectSources({scopeId:'project_a',sources,previous:checkpoint});
 assert.equal(stale.sources[0].freshness,'STALE');
 assert.equal(stale.sources[0].currentRevision,head);
 assert.equal(stale.metrics.pinnedReadCalls,0);
 assert.equal(reads,0);
 assert.equal(observations,3);
});

test('changed source bindings and unauthorized providers are reported without content reads',async()=>{
 const d=sourceDescriptor({providerId:'host.file',sourceRef:'host://one'});
 const p=sourceDescriptor({providerId:'host.file',sourceRef:'host://two'});
 const checkpoint={schema:'IWXA_U02_SELECTED_PROJECT_CHECKPOINT/1',scopeId:'project_a',
   sources:[saved('bound',d,'sha256:'+'a'.repeat(64),'sha256:'+'a'.repeat(64),1)]};
 let reads=0,refreshes=0;
 const other={describe:()=>p,refresh:()=>{refreshes++;throw Error('should not refresh')},
   read:()=>{reads++;throw Error('should not read')}};
 const rebinding=await inspectSelectedProjectSources({scopeId:'project_a',
   sources:[{key:'bound',format:'text',port:other}],previous:checkpoint});
 assert.equal(rebinding.sources[0].status,'SOURCE_REBIND_REQUIRED');
 assert.equal(rebinding.metrics.refreshCalls,0);
 assert.equal(reads,0);assert.equal(refreshes,0);
 const denied={describe:()=>d,refresh:()=>{
   refreshes++;
   return sourceObservation(d,{status:'AUTHORIZATION_REQUIRED',observedAt:new Date().toISOString(),
     provenance:{provider:'authorized-host'}});
 },read:()=>{reads++;throw Error('denied read')}};
 const revoked=await inspectSelectedProjectSources({scopeId:'project_a',
   sources:[{key:'bound',format:'text',port:denied}],previous:checkpoint});
 assert.equal(revoked.sources[0].freshness,'UNKNOWN');
 assert.equal(revoked.sources[0].status,'AUTHORIZATION_REQUIRED');
 assert.equal(reads,0);
 const unsupported=await inspectSelectedProjectSources({scopeId:'project_a',
   sources:[{key:'bound',format:'exe',port:denied}],previous:checkpoint});
 assert.equal(unsupported.sources[0].status,'FORMAT_UNSUPPORTED');
 assert.equal(refreshes,1);
 await assert.rejects(()=>inspectSelectedProjectSources({scopeId:'different',
   sources:[{key:'bound',format:'text',port:denied}],previous:checkpoint}),
   /PROJECT_CHECKPOINT_INVALID/);
});

test('ambiguous unchanged revision with different contentRef fails closed',async()=>{
 const digest1='sha256:'+'a'.repeat(64),digest2='sha256:'+'b'.repeat(64);
 const d=sourceDescriptor({providerId:'host.file',sourceRef:'host://consistent'});
 const p={describe:()=>d,refresh:()=>sourceObservation(d,{status:'OK',
    revision:digest1,contentRef:digest2,observedAt:new Date().toISOString(),
    provenance:{provider:'host.file'}}),read:()=>{throw Error('MUST_NOT_READ')}};
 const checkpoint={schema:'IWXA_U02_SELECTED_PROJECT_CHECKPOINT/1',scopeId:'project_a',
   sources:[saved('file',d,digest1,digest1,128)]};
 const result=await inspectSelectedProjectSources({scopeId:'project_a',
   sources:[{key:'file',format:'text',port:p}],previous:checkpoint});
 assert.equal(result.sources[0].status,'SOURCE_INCONSISTENT');
 assert.equal(result.sources[0].freshness,'UNKNOWN');
 assert.equal(result.metrics.pinnedReadCalls,0);
});
