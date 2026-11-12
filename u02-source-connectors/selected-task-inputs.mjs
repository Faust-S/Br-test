import {readSelectedProjectSources,checkpointSelectedProjectSources,
  projectSourceObservationsForKnowledge} from './selected-project.mjs';
import {revalidateSelectedProjectRead} from './selected-project-revalidate.mjs';
import {OBSERVATION_AUTHORITY} from './port.mjs';

// Agent-facing orchestration over existing read-only U02 ports. This does not
// own selection, credentials, persistence, Task, Review, or Result.
export const TASK_INPUTS_SCHEMA = 'IWXA_U02_TASK_INPUTS/1';

export async function prepareSelectedTaskSources({scopeId,sources,previous=null,
  maxTotalBytes, isCurrent=()=>true}={}) {
  if (typeof isCurrent!=='function') throw Error('TASK_INPUTS_CURRENTNESS_INVALID');
  const current=()=>{try {return isCurrent()===true;} catch {return false;}};
  const blocked=(status,metrics,issues)=>({
    schema:TASK_INPUTS_SCHEMA,scopeId,status,ready:false,authority:OBSERVATION_AUTHORITY,
    observations:[],checkpoint:null,issues,metrics
  });
  if (!current()) return blocked('SUPERSEDED',null,[]);
  // Snapshot the host's selected entries. A superseded Task must not keep
  // reading other files or fetch remote sources just to discard their output.
  // Existing SourceConnectorPort methods still own every authorization check.
  const selected=Array.isArray(sources)?sources.map(entry=>{
    const port=entry?.port;
    if(typeof port?.describe!=='function'||
       typeof port?.refresh!=='function'||typeof port?.read!=='function')
      return entry;
    const guard=()=>{if(!current())throw Error('TASK_INPUTS_SUPERSEDED');};
    return {...entry,port:{
      describe:()=>port.describe(),
      refresh:async()=>{guard();return port.refresh();},
      read:async pin=>{guard();return port.read(pin);}
    }};
  }):sources;
  const read=await readSelectedProjectSources({scopeId,sources:selected,previous,maxTotalBytes});
  const metrics={read:read.metrics,revalidation:null};
  if (!current()) return blocked('SUPERSEDED',metrics,[]);
  if (sources.length===0 || !read.complete) {
    const issues=read.sources.filter(row=>row.status!=='OK').map(row=>({
      key:row.key,providerId:row.providerId,sourceRef:row.sourceRef,
      status:row.status,change:row.change,
      previousRevision:row.previousRevision??null,
      currentRevision:row.currentRevision??null
    }));
    if (sources.length===0) issues.push({
      key:null,providerId:null,sourceRef:null,status:'NO_SOURCES_SELECTED',
      change:'UNKNOWN',previousRevision:null,currentRevision:null
    });
    // A failed batch never publishes any current Task input and does not
    // spend extra I/O revalidating the already-incomplete selection.
    return blocked('INCOMPLETE',metrics,issues);
  }
  const final=await revalidateSelectedProjectRead({view:read,sources:selected});
  metrics.revalidation=final.revalidation;
  if (!current()) return blocked('SUPERSEDED',metrics,[]);
  const issues=final.sources.filter(row=>row.status!=='OK').map(row=>({
    key:row.key,providerId:row.providerId,sourceRef:row.sourceRef,
    status:row.status,change:row.change,
    previousRevision:row.previousRevision??null,
    currentRevision:row.currentRevision??null
  }));
  if (sources.length===0) issues.push({
    key:null,providerId:null,sourceRef:null,status:'NO_SOURCES_SELECTED',
    change:'UNKNOWN',previousRevision:null,currentRevision:null
  });
  if (!final.complete || issues.length>0)
    return blocked('INCOMPLETE',metrics,issues);
  // Build from the final revalidated view, never from the earlier read.
  const observations=projectSourceObservationsForKnowledge(final);
  const checkpoint=checkpointSelectedProjectSources(final);
  if (observations.length!==sources.length ||
      checkpoint.sources.length!==sources.length ||
      final.sources.some(row=>row.finalRevisionConfirmed!==true))
    throw Error('TASK_INPUTS_FINAL_INTEGRITY_INVALID');
  if (!current()) return blocked('SUPERSEDED',metrics,[]);
  return {schema:TASK_INPUTS_SCHEMA,scopeId,status:'READY',ready:true,
    authority:OBSERVATION_AUTHORITY,observations,checkpoint,issues:[],metrics};
}
