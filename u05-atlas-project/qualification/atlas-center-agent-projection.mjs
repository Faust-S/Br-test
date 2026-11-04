import assert from 'node:assert/strict';
import {mkdtemp,readFile,writeFile,rm} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {empty,createProject,createItem,editItem,serialize,deserialize} from '../../atlas-public/src/core.mjs';
import {projectReference,workReference,observeWorkReference,requireCurrentWorkReference,
  personalWorkProjection} from '../../atlas-public/src/project-reference.mjs';
import {readAtlasPortfolio,linkableAtlasWork,atlasWorkReadback} from '../../center-public/src/atlas-integration.mjs';
import {createLocalFileSourcePort} from '../../u02-source-connectors/local-file.mjs';
import {pinSource,sourceChange} from '../../u02-source-connectors/port.mjs';

const root=await mkdtemp(join(tmpdir(),'iwxa-u05-contract-'));
try {
 const project=createProject(empty(),{name:'Cross-product Atlas',type:'business',owner:'private'});
 const first=createItem(project.state,project.id,{title:'Assigned verification'});
 const second=createItem(first.state,project.id,{title:'Independent work'});
 const file=join(root,'atlas-portfolio.json');
 await writeFile(file,serialize(second.state));
 const source=createLocalFileSourcePort({root,rootId:'approved-user-selection',relativePath:'atlas-portfolio.json'});
 const oldObserved=await source.refresh();
 assert.equal(oldObserved.status,'OK');
 const oldPin=pinSource(source.describe(),oldObserved);
 const oldRead=await source.read(oldPin);
 assert.equal(oldRead.status,'OK');
 const original=deserialize(Buffer.from(oldRead.content.data,'base64').toString('utf8'));
 const centerView=readAtlasPortfolio(serialize(original));
 const centerRef=linkableAtlasWork(centerView,project.id,first.id);
 const atlasRef=workReference(original,project.id,first.id);
 assert.deepEqual(centerRef,atlasRef);
 assert.deepEqual(projectReference(original,project.id),
   {projectId:project.id,revision:String(original.revision)});
 assert.equal(atlasWorkReadback(centerView,centerRef),'SOURCE_REVISION_MATCHES');
 assert.equal(observeWorkReference(original,centerRef),'SOURCE_REVISION_MATCHES');
 const assigned=requireCurrentWorkReference(original,centerRef);
 assert.equal(assigned.work.id,first.id);
 const projection=personalWorkProjection(original,centerRef,{hostAuthorized:true});
 assert.equal(projection.writeAuthority,false);
 assert.equal(projection.work.id,first.id);
 assert.equal(JSON.stringify(projection).includes('private'),false);
 assert.equal(JSON.stringify(projection).includes('Independent work'),false);
 assert.throws(()=>personalWorkProjection(original,centerRef),/ATLAS_HOST_AUTHORIZATION_REQUIRED/);
 // Persist/reopen with another work edited: immutable work ID, new global revision.
 const edited=editItem(original,project.id,second.id,{state:'done'});
 await writeFile(file,serialize(edited));
 const newObserved=await source.refresh();
 assert.equal(sourceChange(oldObserved,newObserved).status,'CHANGED');
 assert.equal((await source.read(oldPin)).status,'REVISION_MISMATCH');
 const newRead=await source.read(pinSource(source.describe(),newObserved));
 const reopened=deserialize(Buffer.from(newRead.content.data,'base64').toString('utf8'));
 const revisedCenter=readAtlasPortfolio(serialize(reopened));
 assert.equal(revisedCenter.projects[0].workItems[0].id,first.id);
 assert.equal(atlasWorkReadback(revisedCenter,centerRef),'SOURCE_REVISION_CHANGED');
 assert.equal(observeWorkReference(reopened,centerRef),'SOURCE_REVISION_CHANGED');
 assert.throws(()=>requireCurrentWorkReference(reopened,centerRef),/ATLAS_SOURCE_REVISION_CHANGED/);
 assert.throws(()=>personalWorkProjection(reopened,centerRef,{hostAuthorized:true}),/ATLAS_SOURCE_REVISION_CHANGED/);
 assert.equal(atlasWorkReadback(revisedCenter,workReference(reopened,project.id,first.id)),
   'SOURCE_REVISION_MATCHES');
 console.log(JSON.stringify({schema:'IWXA_U05_ATLAS_CENTER_AGENT_PROJECTION/1',status:'PASS',
  canonicalOwner:'Atlas',centerOwnerReader:true,sourceOwnerRevision:true,
  sameInitialRef:true,oldRevision:String(original.revision),newRevision:String(reopened.revision),
  workIdUnchanged:true,oldPinnedSourceRejected:true,corporateOldRevisionRejected:true,
  agentProjectionOnly:true,noProjectStoreAdded:true,
  note:'Source contract qualification; installed desktop readback and Agent host authorization remain untested'}));
} finally {await rm(root,{recursive:true,force:true});}
