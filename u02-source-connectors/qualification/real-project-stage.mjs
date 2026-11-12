import assert from 'node:assert/strict';
import {mkdtemp, mkdir, readFile, writeFile, rename, rm} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
import {observePublicRepo} from '../../atlas-public/src/github-public.mjs';
import {createAtlasGitHubSourcePort} from '../atlas-github.mjs';
import {createLocalFileSourcePort} from '../local-file.mjs';
import {readSelectedProjectSources,checkpointSelectedProjectSources,
  projectSourceObservationsForKnowledge,inspectSelectedProjectSources} from '../selected-project.mjs';
import {revalidateSelectedProjectRead} from '../selected-project-revalidate.mjs';
import {prepareSelectedTaskSources} from '../selected-task-inputs.mjs';

const root=process.env.U02_PROJECT_ROOT;
const phase=process.argv[2];
if(!root||!['initial','reopen','task','cancel','inspect','change'].includes(phase))throw Error('QUALIFICATION_INPUT_INVALID');
const paths=[
 {key:'plan',format:'markdown',path:'docs/project-plan.md'},
 {key:'requirements',format:'markdown',path:'docs/requirements.md'},
 {key:'budget',format:'csv',path:'data/budget.csv'},
 {key:'meta',format:'json',path:'data/project.json'}
];
const local=()=>paths.map(s=>({key:s.key,format:s.format,
  port:createLocalFileSourcePort({root,rootId:'iwxa_qual_project',relativePath:s.path})}));
const github=()=>({key:'github-license',format:'text',port:createAtlasGitHubSourcePort({
  repository:'Faust-S/Atlas',branch:'work/2026-11-02-atlas-desktop-0.2',
  relativePath:'LICENSE',observePublicRepo})});
const save=join(root,'qualification-checkpoint.json');
const view=join(root,'qualification-result.json');
const retry=async fn=>{
  let last;
  for(let i=0;i<4;i++){
    last=await fn();
    if(last.complete || !last.sources.some(s=>['SOURCE_UNAVAILABLE','RATE_LIMITED','SOURCE_INCOMPLETE'].includes(s.status)))
      return last;
    await new Promise(r=>setTimeout(r,800*(i+1)));
  }
  return last;
};
if(phase==='initial'){
  await mkdir(join(root,'docs'),{recursive:true});
  await mkdir(join(root,'data'),{recursive:true});
  const plan=[
    '# IWXA U02 source project',
    'Project ID: U02-CHECK-001',
    'Objective: provide an Agent with current, traceable project materials.',
    'Milestone: proposal',
    'Owner: product integration; source ownership remains with each host.',
    'Dependencies:',
    '- requirements.md controls what an observation can claim;',
    '- budget.csv records the labor estimate;',
    '- project.json contains the progress state;',
    '- the publicly observed Atlas LICENSE confirms external GitHub provenance.',
    'Delivery steps:',
    '1. Read and pin every explicitly selected source.',
    '2. Save source refs and hashes in the existing host Task context.',
    '3. Resume with new source ports in a fresh process.',
    '4. Detect a changed document and a renamed source before preparing a result.',
    'Acceptance: no old document content may be described as current after a change.',
    'Acceptance: no source may grant write/apply/execute authority.',
    'Acceptance: missing or unauthorized sources must remain visible as incomplete.'
  ].join('\n')+'\n';
  const requirements=[
    '# Requirements for U02-CHECK-001',
    'R-001: read the four selected local documents and the public Atlas GitHub file.',
    'R-002: bind every source to host scope and exact provider/sourceRef.',
    'R-003: record SHA-256 of actual bytes and exact pinned revision.',
    'R-004: return historical references only as historical evidence.',
    'R-005: detect changed, missing and inaccessible documents.',
    'R-006: reject source rebindings until the host reselects them.',
    'R-007: do not discover user home directories or inspect other projects.',
    'R-008: do not transfer GitHub credentials to U02 or a public runner.',
    'R-009: allow K01 to revalidate evidence at the point of Task use.',
    'R-010: publish elapsed time, source reads and byte counts as measured.',
    'Acceptance: independent processes agree on unchanged source revisions.',
    'Acceptance: subsequent edits change the returned project material.'
  ].join('\n')+'\n';
  const budget=[
    'workstream,hours,rate,total,owner',
    'source_selection,3,100,300,agent_host',
    'github_readback,4,105,420,u02',
    'local_revisions,5,95,475,u02',
    'knowledge_revalidation,4,110,440,k01',
    'final_review,2,90,180,product',
    'TOTAL,18,,1815,'
  ].join('\n')+'\n';
  const state={
    projectId:'U02-CHECK-001',revision:1,status:'draft',
    requirementsVersion:'R-010',documentCount:4,externalSource:'Faust-S/Atlas',
    milestones:[
      {name:'Initial observation',state:'planned',requiredSources:5},
      {name:'Independent readback',state:'planned',requiredSources:5},
      {name:'Revision change',state:'planned',requiredSources:4},
      {name:'Knowledge revalidation',state:'planned',requiredSources:1}
    ],
    acceptance:{noSilentStale:true,readOnly:true,checkpointOwner:'host'}
  };
  await writeFile(join(root,paths[0].path),plan);
  await writeFile(join(root,paths[1].path),requirements);
  await writeFile(join(root,paths[2].path),budget);
  await writeFile(join(root,paths[3].path),JSON.stringify(state,null,2));
  const first=await retry(()=>readSelectedProjectSources({scopeId:'iwxa_selected_project',sources:[...local(),github()]}));
  assert.equal(first.complete,true,JSON.stringify(first.sources.map(s=>({key:s.key,status:s.status}))));
  assert.equal(first.sources.length,5);
  assert.equal(first.metrics.refreshCalls,5);
  assert.equal(first.metrics.pinnedReadCalls,5);
  assert.ok(first.sources.find(s=>s.key==='github-license').text.includes('MIT License'));
  await writeFile(save,JSON.stringify(checkpointSelectedProjectSources(first)));
  await writeFile(view,JSON.stringify({first:first.sources.map(s=>({key:s.key,revision:s.revision,
    contentRef:s.contentRef,status:s.status})),firstMetrics:first.metrics}));
  console.log(JSON.stringify({schema:'IWXA_U02_REAL_PROJECT/1',phase,status:'PASS',
    sourceCount:5,liveGitHub:true,localFiles:4,metrics:first.metrics}));
}else if(phase==='reopen'){
  const checkpoint=JSON.parse(await readFile(save,'utf8'));
  const again=await retry(()=>readSelectedProjectSources({scopeId:'iwxa_selected_project',
    sources:[...local(),github()],previous:checkpoint}));
  assert.equal(again.complete,true,JSON.stringify(again.sources.map(s=>({key:s.key,status:s.status}))));
  const localRows=again.sources.filter(s=>s.key!=='github-license');
  assert.deepEqual(localRows.map(s=>s.change),['UNCHANGED','UNCHANGED','UNCHANGED','UNCHANGED']);
  const gh=again.sources.find(s=>s.key==='github-license');
  assert.ok(['UNCHANGED','CHANGED','REVISION_CHANGED_CONTENT_SAME'].includes(gh.change));
  const original=JSON.parse(await readFile(view,'utf8'));
  assert.equal(again.sources.length,5);
  assert.equal(projectSourceObservationsForKnowledge(again).length,5);
  assert.equal(again.metrics.refreshCalls,5);
  assert.equal(again.metrics.pinnedReadCalls,5);
  const final=await revalidateSelectedProjectRead({view:again,sources:[...local(),github()]});
  assert.equal(final.complete,true);
  assert.equal(final.revalidation.refreshCalls,5);
  assert.equal(final.revalidation.pinnedReadCalls,0);
  assert.equal(final.revalidation.contentRead,false);
  assert.equal(final.sources.every(s=>s.finalRevisionConfirmed===true),true);
  await writeFile(save,JSON.stringify(checkpointSelectedProjectSources(again)));
  await writeFile(view,JSON.stringify({...original,reopen:again.sources.map(s=>({
    key:s.key,revision:s.revision,contentRef:s.contentRef,change:s.change})),reopenMetrics:again.metrics}));
  console.log(JSON.stringify({schema:'IWXA_U02_REAL_PROJECT/1',phase,status:'PASS',
    restartedIndependentProcess:true,githubChange:gh.change,metrics:again.metrics}));
}else if(phase==='task'){
  const checkpoint=JSON.parse(await readFile(save,'utf8'));
  const result=await prepareSelectedTaskSources({
    scopeId:'iwxa_selected_project',
    sources:[...local(),github()],
    previous:checkpoint,
    isCurrent:()=>true
  });
  assert.equal(result.status,'READY',JSON.stringify(result.issues));
  assert.equal(result.ready,true);
  assert.equal(result.authority,'OBSERVATION_ONLY');
  assert.equal(result.observations.length,5);
  assert.equal(result.checkpoint.sources.length,5);
  assert.equal(result.metrics.read.refreshCalls,5);
  assert.equal(result.metrics.read.pinnedReadCalls,5);
  assert.equal(result.metrics.revalidation.refreshCalls,5);
  assert.equal(result.metrics.revalidation.pinnedReadCalls,0);
  assert.ok(result.observations.find(s=>s.key==='plan').text.includes('proposal'));
  assert.ok(result.observations.find(s=>s.key==='github-license').text.includes('MIT License'));
  const original=JSON.parse(await readFile(view,'utf8'));
  await writeFile(view,JSON.stringify({...original,taskSources:result.observations.map(s=>({
    key:s.key,sourceRef:s.sourceRef,revision:s.revision,contentRef:s.contentRef
  })),taskMetrics:result.metrics}));
  console.log(JSON.stringify({schema:'IWXA_U02_REAL_TASK_INPUTS/1',phase,status:'PASS',
    exactSourceCount:5,finalRevalidated:true,taskReady:true,
    additionalPinnedReads:0,metrics:result.metrics}));
}else if(phase==='cancel'){
  const checkpoint=JSON.parse(await readFile(save,'utf8'));
  let active=true,firstReads=0,otherNativeCalls=0;
  const selected=[...local(),github()];
  const first=selected[0].port;
  selected[0]={...selected[0],port:{
    describe:()=>first.describe(),
    refresh:()=>first.refresh(),
    read:async pin=>{
      firstReads++;
      const response=await first.read(pin);
      active=false;
      return response;
    }
  }};
  for(let i=1;i<selected.length;i++){
    const port=selected[i].port;
    selected[i]={...selected[i],port:{
      describe:()=>port.describe(),
      refresh:()=>{otherNativeCalls++;return port.refresh()},
      read:pin=>{otherNativeCalls++;return port.read(pin)}
    }};
  }
  const cancelled=await prepareSelectedTaskSources({
    scopeId:'iwxa_selected_project',sources:selected,
    previous:checkpoint,isCurrent:()=>active
  });
  assert.equal(cancelled.status,'SUPERSEDED');
  assert.equal(cancelled.ready,false);
  assert.equal(cancelled.checkpoint,null);
  assert.deepEqual(cancelled.observations,[]);
  assert.equal(cancelled.metrics.revalidation,null);
  assert.equal(firstReads,1);
  assert.equal(otherNativeCalls,0,'No later local or public GitHub source requests');
  console.log(JSON.stringify({schema:'IWXA_U02_REAL_TASK_CANCELLATION/1',
    phase,status:'PASS',actualFirstSourceReads:firstReads,
    actualLaterSourceRequests:otherNativeCalls,remoteGitHubRequests:0,
    noPartialTaskEvidence:true}));
}else if(phase==='inspect'){
  const checkpoint=JSON.parse(await readFile(save,'utf8'));
  let accidentalReads=0;
  const sources=[...local(),github()].map(({key,format,port})=>({
    key,format,port:{
      describe:()=>port.describe(),refresh:()=>port.refresh(),
      read:async()=>{accidentalReads++;throw Error('OBSERVATION_MUST_NOT_READ_CONTENT')}
    }
  }));
  const check=await retry(()=>inspectSelectedProjectSources({
    scopeId:'iwxa_selected_project',sources,previous:checkpoint
  }));
  assert.equal(check.observationsComplete,true,
    JSON.stringify(check.sources.map(s=>({key:s.key,status:s.status}))));
  assert.deepEqual(check.sources.slice(0,4).map(s=>s.freshness),
    ['CURRENT','CURRENT','CURRENT','CURRENT']);
  assert.ok(['CURRENT','STALE'].includes(check.sources[4].freshness));
  assert.equal(check.metrics.refreshCalls,5);
  assert.equal(check.metrics.pinnedReadCalls,0);
  assert.equal(check.metrics.returnedTextBytes,0);
  assert.equal(accidentalReads,0);
  assert.equal(check.evidenceReady,false);
  assert.equal(check.contentRead,false);
  assert.ok(check.sources.every(row=>!('text' in row)));
  const old=JSON.parse(await readFile(view,'utf8'));
  await writeFile(view,JSON.stringify({...old,inspect:check.sources.map(s=>({
    key:s.key,freshness:s.freshness,change:s.change,revision:s.currentRevision
  })),inspectMetrics:check.metrics}));
  console.log(JSON.stringify({schema:'IWXA_U02_REAL_PROJECT/1',phase,status:'PASS',
    contentReads:0,freshnessOnly:true,externalGitHubStatus:check.sources[4].freshness,
    metrics:check.metrics}));
}else{
  const checkpoint=JSON.parse(await readFile(save,'utf8'));
  // A real second local revision and a renamed file; previous bytes remain historical.
  const priorPlan=await readFile(join(root,paths[0].path),'utf8');
  assert.match(priorPlan,/Milestone: proposal/);
  await writeFile(join(root,paths[0].path),priorPlan.replace('Milestone: proposal',
    'Milestone: approved, execution ready'));

  await rename(join(root,paths[1].path),join(root,'docs','requirements-renamed.md'));
  const freshness=await inspectSelectedProjectSources({
    scopeId:'iwxa_selected_project',sources:local(),previous:checkpoint});
  assert.equal(freshness.sources[0].freshness,'STALE');
  assert.equal(freshness.sources[1].status,'SOURCE_MISSING');
  assert.deepEqual(freshness.sources.slice(2).map(row=>row.freshness),
    ['CURRENT','CURRENT']);
  assert.equal(freshness.metrics.pinnedReadCalls,0);
  assert.equal(freshness.evidenceReady,false);
  const changed=await readSelectedProjectSources({scopeId:'iwxa_selected_project',
    sources:local(),previous:checkpoint});
  assert.equal(changed.sources[0].change,'CHANGED');
  assert.equal(changed.sources[1].status,'SOURCE_MISSING');
  assert.equal(changed.sources[1].change,'MISSING');
  assert.equal(changed.sources[2].change,'UNCHANGED');
  assert.equal(changed.sources[3].change,'UNCHANGED');
  assert.match(changed.sources[0].text,/execution ready/);
  assert.doesNotMatch(changed.sources[0].text,/proposal/);
  assert.equal(projectSourceObservationsForKnowledge(changed).length,3);
  assert.equal(changed.metrics.refreshCalls,4);
  assert.equal(changed.metrics.pinnedReadCalls,3);
  const original=JSON.parse(await readFile(view,'utf8'));
  await writeFile(view,JSON.stringify({...original,changed:changed.sources.map(s=>({
    key:s.key,status:s.status,change:s.change,revision:s.revision??null})),
    changedMetrics:changed.metrics}));
  console.log(JSON.stringify({schema:'IWXA_U02_REAL_PROJECT/1',phase,status:'PASS',
    realDocumentEdited:true,renamedDocumentDetected:true,
    staleNotPromoted:true,knowledgeOnlyCurrent:true,metrics:changed.metrics}));
}
