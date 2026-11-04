import assert from 'node:assert/strict';
import {readFile, writeFile} from 'node:fs/promises';
import {join} from 'node:path';
import {empty, createProject, createItem, editItem, serialize, deserialize} from '../../atlas-public/src/core.mjs';
import {readAtlasPortfolio, linkableAtlasWork, atlasWorkReadback} from '../../center-public/src/atlas-integration.mjs';
import {createLocalFileSourcePort} from '../local-file.mjs';
import {captureSource, readPinnedSource} from '../source-read.mjs';

const [phase, root] = process.argv.slice(2);
assert.ok(['create','resume','change','relink','reuse'].includes(phase));
assert.ok(root);
const snapshot = join(root,'portfolio.json');
const saved = join(root,'center-saved-link.json');
const updated = join(root,'center-new-link.json');
const port = () => createLocalFileSourcePort({root,rootId:'selected',relativePath:'portfolio.json'});
const viewOf = read => readAtlasPortfolio(Buffer.from(read.content.data,'base64').toString('utf8'));
const load = async file => JSON.parse(await readFile(file,'utf8'));

if (phase === 'create') {
  const created = createProject(empty(), {name:'Independent host integration',type:'engineering'});
  const work = createItem(created.state,created.id,{title:'Use source across restarts'});
  await writeFile(snapshot,serialize(work.state));
  const capture = await captureSource(port());
  assert.equal(capture.status,'OK');
  const view = viewOf(capture.read);
  const link = linkableAtlasWork(view,created.id,work.id);
  assert.equal(atlasWorkReadback(view,link),'SOURCE_REVISION_MATCHES');
  await writeFile(saved,JSON.stringify({pin:capture.pin,link}));
  console.log(JSON.stringify({phase,status:'PASS',revision:capture.pin.revision,workId:link.workId}));
} else if (phase === 'resume') {
  const {pin,link} = await load(saved);
  const read = await readPinnedSource(port(),pin);
  assert.equal(read.status,'OK');
  const view=viewOf(read);
  assert.equal(atlasWorkReadback(view,link),'SOURCE_REVISION_MATCHES');
  console.log(JSON.stringify({phase,status:'PASS',revision:read.revision,workId:link.workId}));
} else if (phase === 'change') {
  const {link} = await load(saved);
  const state=deserialize(await readFile(snapshot,'utf8'));
  const next=editItem(state,link.projectId,link.workId,{state:'done'});
  await writeFile(snapshot,serialize(next));
  assert.equal(next.projects[0].items.find(x=>x.id===link.workId).state,'done');
  console.log(JSON.stringify({phase,status:'PASS',projectRevision:next.revision}));
} else if (phase === 'relink') {
  const old=await load(saved);
  const stale=await readPinnedSource(port(),old.pin);
  assert.equal(stale.status,'REVISION_MISMATCH');
  assert.equal(stale.content,null);
  const capture=await captureSource(port());
  assert.equal(capture.status,'OK');
  assert.notEqual(capture.pin.revision,old.pin.revision);
  const view=viewOf(capture.read);
  assert.equal(atlasWorkReadback(view,old.link),'SOURCE_REVISION_CHANGED');
  const newLink=linkableAtlasWork(view,old.link.projectId,old.link.workId);
  assert.notEqual(newLink.revision,old.link.revision);
  await writeFile(updated,JSON.stringify({pin:capture.pin,link:newLink}));
  console.log(JSON.stringify({phase,status:'PASS',stale:stale.status,oldLink:'SOURCE_REVISION_CHANGED'}));
} else {
  const old=await load(saved);
  const fresh=await load(updated);
  const read=await readPinnedSource(port(),fresh.pin);
  assert.equal(read.status,'OK');
  const view=viewOf(read);
  assert.equal(atlasWorkReadback(view,fresh.link),'SOURCE_REVISION_MATCHES');
  assert.equal(atlasWorkReadback(view,old.link),'SOURCE_REVISION_CHANGED');
  assert.equal(view.projects[0].workItems.find(x=>x.id===old.link.workId).state,'done');
  const historical=await load(saved);
  assert.deepEqual(historical,old);
  console.log(JSON.stringify({phase,status:'PASS',historicalLinkUnchanged:true}));
}
