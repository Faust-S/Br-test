import assert from 'node:assert/strict';
import {mkdtemp,writeFile,rm} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {createLocalFileSourcePort} from '../local-file.mjs';
import {readSelectedProjectSources} from '../selected-project.mjs';
import {readPinnedSource} from '../source-read.mjs';
import {createRevisionedObservationPort}
  from '../../knowledge-public/modules/k01-context/adapters/revisioned-source.mjs';
import {selectContext}
  from '../../knowledge-public/modules/k01-context/index.mjs';
import {revalidateProjection}
  from '../../knowledge-public/modules/k01-context/revalidation.mjs';

const root=await mkdtemp(join(tmpdir(),'u02-k01-real-context-'));
const file=join(root,'project-plan.md');
try{
  await writeFile(file,'# Project plan\nMilestone: ready for review\n');
  const port=createLocalFileSourcePort({root,rootId:'project_scope',
    relativePath:'project-plan.md'});
  const current=await readSelectedProjectSources({scopeId:'project_scope',sources:[
    {key:'project-plan',format:'markdown',port}]});
  assert.equal(current.complete,true);
  const source=current.sources[0];
  const io={refresh:0,read:0};
  const adapter=createRevisionedObservationPort({
    sourceRef:source.sourceRef,
    pinnedItems:[{id:'project-plan',type:'project-document',sourceRef:source.sourceRef,
      revision:source.revision,epistemic:'observed',estimatedTokens:15,
      keywords:['project','plan','milestone']}],
    getCurrentRevision:async()=>{
      io.refresh++;
      const fresh=await port.refresh();
      return fresh.status==='OK'?fresh.revision:null;
    },
    readEvidence:async()=>{
      io.read++;
      const result=await readPinnedSource(port,source.pin);
      if(result.status!=='OK')throw Error('U02_PIN_STALE');
      return {observedRevision:result.revision,
        excerpt:Buffer.from(result.content.data,'base64').toString('utf8'),
        evidenceRef:'U02E-'+result.contentRef.slice(7)};
    }
  });
  const request={taskId:'TASK-U02-K01-1',terms:['project'],
    allowedSources:[source.sourceRef],maxTokens:1024,allowInferred:false};
  const projection=await selectContext(request,adapter);
  assert.equal(projection.selected.length,1,JSON.stringify(projection.omissions));
  assert.equal(projection.selected[0].revision,source.revision);
  assert.equal(projection.selected[0].evidenceRef,'U02E-'+source.contentRef.slice(7));
  const verified=await revalidateProjection(projection,adapter,request);
  assert.equal(verified.allCurrent,true,JSON.stringify(verified.failures));
  await writeFile(file,'# Project plan\nMilestone: delivered\n');
  const stale=await revalidateProjection(projection,adapter,request);
  assert.equal(stale.allCurrent,false);
  assert.equal(stale.failures[0].reason,'stale');
  assert.notEqual(stale.failures[0].currentRevision,source.revision);
  assert.equal(io.read,2,'Changed source must not be read after stale revision detection');
  const newView=await readSelectedProjectSources({scopeId:'project_scope',sources:[
    {key:'project-plan',format:'markdown',port}]});
  assert.match(newView.sources[0].text,/delivered/);
  assert.notEqual(newView.sources[0].revision,source.revision);
  console.log(JSON.stringify({schema:'IWXA_U02_K01_REAL_SOURCE/1',status:'PASS',
    authenticK01Owner:true,originalU02SourcePort:true,k01Projection:true,
    k01Revalidation:true,staleRecognized:true,staleNotRead:true,
    ownerRefreshes:io.refresh,ownerEvidenceReads:io.read,externalStore:false}));
}finally{await rm(root,{recursive:true,force:true});}
