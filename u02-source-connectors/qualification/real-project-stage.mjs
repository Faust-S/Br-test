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
  projectSourceObservationsForKnowledge} from '../selected-project.mjs';

const root=process.env.U02_PROJECT_ROOT;
const phase=process.argv[2];
if(!root||!['initial','reopen','change'].includes(phase))throw Error('QUALIFICATION_INPUT_INVALID');
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
  await writeFile(join(root,paths[0].path),'# IWXA project\nMilestone: proposal\n');
  await writeFile(join(root,paths[1].path),'# Requirements\nPinned sources and repeatable evidence\n');
  await writeFile(join(root,paths[2].path),'item,cost\nreview,400\nsource-verify,300\n');
  await writeFile(join(root,paths[3].path),JSON.stringify({project:'IWXA',revision:1,status:'draft'}));
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
  await writeFile(save,JSON.stringify(checkpointSelectedProjectSources(again)));
  await writeFile(view,JSON.stringify({...original,reopen:again.sources.map(s=>({
    key:s.key,revision:s.revision,contentRef:s.contentRef,change:s.change})),reopenMetrics:again.metrics}));
  console.log(JSON.stringify({schema:'IWXA_U02_REAL_PROJECT/1',phase,status:'PASS',
    restartedIndependentProcess:true,githubChange:gh.change,metrics:again.metrics}));
}else{
  const checkpoint=JSON.parse(await readFile(save,'utf8'));
  // A real second local revision and a renamed file; previous bytes remain historical.
  await writeFile(join(root,paths[0].path),'# IWXA project\nMilestone: approved, execution ready\n');
  await rename(join(root,paths[1].path),join(root,'docs','requirements-renamed.md'));
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
