import assert from 'node:assert/strict';
import {mkdtemp,readFile,rm,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {empty,createProject,createItem,editItem,attach,observe,serialize,deserialize} from '../../atlas-public/src/core.mjs';
import {readAtlasPortfolio,linkableAtlasWork,atlasWorkReadback,ATLAS_SOURCE_REVISION} from '../../center-public/src/atlas-integration.mjs';
import {createLocalFileSourcePort} from '../local-file.mjs';
import {sourceChange,pinSource} from '../port.mjs';

assert.equal(ATLAS_SOURCE_REVISION,'e5cc6ff593aa12eec3da8e8940ad5af5819b66fd');
const root=await mkdtemp(join(tmpdir(),'iwxa-u02-atlas-center-'));
try {
 const first=createProject(empty(),{name:'Project qualification',type:'engineering'});
 const item=createItem(first.state,first.id,{title:'Verify pinned document'});
 const file=join(root,'portfolio.json');
 await writeFile(file,serialize(item.state));
 const port=createLocalFileSourcePort({root,rootId:'selected',relativePath:'portfolio.json'});
 const observed=await port.refresh();
 assert.equal(observed.status,'OK');
 const pinned=pinSource(port.describe(),observed);
 const read=await port.read(pinned);
 assert.equal(read.status,'OK');
 assert.equal(read.revision,pinned.revision);
 const raw=Buffer.from(read.content.data,'base64').toString('utf8');
 assert.equal(raw,await readFile(file,'utf8'));
 const center=readAtlasPortfolio(raw);
 const workRef=linkableAtlasWork(center,first.id,item.id);
 assert.equal(atlasWorkReadback(center,workRef),'SOURCE_REVISION_MATCHES');
 assert.equal(center.authorization,'NOT_CHECKED');
 assert.equal(center.mode,'READ_ONLY_USER_SELECTED_FILE');
 assert.equal(read.authority,'OBSERVATION_ONLY');

 const attached=attach(item.state,first.id,'file','portfolio.json');
 const stored=observe(attached.state,first.id,attached.id,'ok',{
  schema:observed.schema,revision:observed.revision,sourceRef:observed.sourceRef,
  contentRef:observed.contentRef,provenance:observed.provenance,
  authority:observed.authority
 });
 const roundtrip=deserialize(serialize(stored));
 const source=roundtrip.projects[0].sources[0];
 assert.equal(source.summary.revision,observed.revision);
 assert.equal(source.summary.authority,'OBSERVATION_ONLY');

 const edited=editItem(item.state,first.id,item.id,{state:'done'});
 await writeFile(file,serialize(edited));
 const changed=await port.refresh();
 assert.equal(sourceChange(observed,changed).status,'CHANGED');
 const stale=await port.read(pinned);
 assert.equal(stale.status,'REVISION_MISMATCH');
 assert.equal(stale.content,null);
 const next=await port.read(pinSource(port.describe(),changed));
 assert.equal(next.status,'OK');
 const updated=readAtlasPortfolio(Buffer.from(next.content.data,'base64').toString('utf8'));
 assert.equal(atlasWorkReadback(updated,workRef),'SOURCE_REVISION_CHANGED');
 assert.equal(updated.projects[0].workItems[0].state,'done');
 assert.equal(updated.projects[0].workItems[0].id,item.id);
 assert.equal(updated.projects[0].id,first.id);
 console.log(JSON.stringify({schema:'IWXA_U02_ATLAS_CENTER_ROUTE/1',status:'PASS',
  actualAtlasCore:true,actualCenterAdapter:true,
  oldRevision:observed.revision,newRevision:changed.revision,
  stalePinnedRefused:true,centerStaleLink:'SOURCE_REVISION_CHANGED',
  authoritativeProjectStore:'Atlas',sourceWriteAuthority:false}));
} finally {await rm(root,{recursive:true,force:true});}
