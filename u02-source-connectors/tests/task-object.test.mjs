import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,writeFile,rm} from 'node:fs/promises';
import {join} from 'node:path';
import {tmpdir} from 'node:os';
import {createLocalFileSourcePort} from '../local-file.mjs';
import {createHostSelectedSourcePort} from '../host-source.mjs';
import {sourceDescriptor,sourceObservation} from '../port.mjs';
import {readPinnedSource} from '../source-read.mjs';
import {bindSourceObject,linkTaskSource,assessTaskSourceFreshness,TASK_SOURCE_SCHEMA} from '../task-object.mjs';

const note={programId:'OBSIDIAN',instanceRef:'vault-a',sourceId:'vault-notes',
  objectRef:'Notes/Идея.md',objectKind:'obsidian.note',representation:'text'};
const browser={programId:'OBSCURA_WORKER',instanceRef:'nav-a',sourceId:'web-capture',
  objectRef:'https://example.org/article',objectKind:'browser.page',representation:'html'};
const enc=text=>new TextEncoder().encode(text);

test('Obsidian note: exact file, vault, revision and Task link survive JSON restart',async()=>{
 const root=await mkdtemp(join(tmpdir(),'u02-obsidian-object-'));
 const other=await mkdtemp(join(tmpdir(),'u02-obsidian-other-'));
 try {
  await mkdir(join(root,'Notes'));await mkdir(join(other,'Notes'));
  const initial='# Идея\nМатериал';
  await writeFile(join(root,'Notes','Идея.md'),initial);
  await writeFile(join(other,'Notes','Идея.md'),initial);
  const base=()=>createLocalFileSourcePort({root,rootId:'vault_a',relativePath:'Notes/Идея.md'});
  const bound=await bindSourceObject(base(),note);
  const linked=await linkTaskSource(bound,{taskRef:'TASK-OBS-1'});
  assert.equal(linked.status,'OK');
  const link=JSON.parse(JSON.stringify(linked.link));
  assert.equal(link.schema,TASK_SOURCE_SCHEMA);
  assert.equal(link.programId,'OBSIDIAN');assert.equal(link.objectRef,'Notes/Идея.md');
  assert.equal(link.authority,'OBSERVATION_ONLY');
  assert.equal(link.evidenceStatus,'SOURCE_OBSERVED_NOT_INDEPENDENTLY_VERIFIED');
  assert.match(link.evidenceRef,/^U02E-[0-9a-f]{64}$/);
  const reopened=await bindSourceObject(base(),note);
  assert.equal((await assessTaskSourceFreshness(reopened,link)).status,'CURRENT');
  const read=await readPinnedSource(reopened,link.pin);
  assert.equal(Buffer.from(read.content.data,'base64').toString('utf8'),initial);
  const otherVault=await bindSourceObject(createLocalFileSourcePort({root:other,rootId:'vault_a',relativePath:'Notes/Идея.md'}),note);
  assert.equal((await assessTaskSourceFreshness(otherVault,link)).status,'SOURCE_MISMATCH');
  await assert.rejects(()=>readPinnedSource(otherVault,link.pin),/SOURCE_PIN_INVALID/);
  const differentNote=await bindSourceObject(base(),{...note,objectRef:'Notes/Другая.md'});
  assert.equal((await assessTaskSourceFreshness(differentNote,link)).status,'SOURCE_MISMATCH');
  await writeFile(join(root,'Notes','Идея.md'),'# Идея\nНовая редакция');
  const stale=await assessTaskSourceFreshness(reopened,link);
  assert.equal(stale.status,'STALE');
  assert.equal((await readPinnedSource(reopened,link.pin)).status,'REVISION_MISMATCH');
  const next=await linkTaskSource(reopened,{taskRef:'TASK-OBS-1'});
  assert.equal(next.status,'OK');assert.notEqual(next.link.evidenceRef,link.evidenceRef);
 }finally{await rm(root,{recursive:true,force:true});await rm(other,{recursive:true,force:true});}
});

test('Atlas work: retain canonical owner revision, no new project store',async()=>{
 let state={revision:4,projects:[{id:'P1',items:[{id:'W1',state:'open'}]}]};
 const host=createHostSelectedSourcePort({hostId:'atlas',scopeId:'person-a',sourceId:'projects',bindingId:'store-a',
  readSelected:()=>({status:'OK',bytes:enc(JSON.stringify(state))})});
 const identity={programId:'ATLAS',instanceRef:'desktop-a',sourceId:'projects',
  objectRef:'project/P1/work/W1',objectKind:'atlas.work',representation:'json'};
 const bound=await bindSourceObject(host,identity);
 const first=await linkTaskSource(bound,{taskRef:'TASK-ATLAS-1',ownerRevision:state.revision});
 assert.equal(first.status,'OK');assert.equal(first.link.ownerRevision,4);
 state={revision:5,projects:[{id:'P1',items:[{id:'W1',state:'done'}]}]};
 assert.equal((await assessTaskSourceFreshness(bound,first.link)).status,'STALE');
 const fresh=await linkTaskSource(bound,{taskRef:'TASK-ATLAS-1',ownerRevision:state.revision});
 assert.equal(fresh.link.ownerRevision,5);assert.equal(fresh.link.objectRef,'project/P1/work/W1');
});

test('Browser document and frame are different evidence, window reference is advisory',async()=>{
 let text='<h1>Captured page</h1>';let status='OK';
 const host=createHostSelectedSourcePort({hostId:'personal-agent',scopeId:'task-a',sourceId:'web',bindingId:'nav-1',
  readSelected:()=>status==='OK'?{status:'OK',bytes:enc(text)}:{status}});
 const page=await bindSourceObject(host,browser);
 const result=await linkTaskSource(page,{taskRef:'TASK-WEB-1',u03ObservationRef:'u03-web-available',
  u04BindingRef:'presentation-optional'});
 assert.equal(result.status,'OK');
 const framed=await bindSourceObject(host,{...browser,objectKind:'browser.frame',
  objectRef:'frame-2026-11-05-01',representation:'visual_frame'});
 assert.notEqual(framed.describe().sourceRef,page.describe().sourceRef);
 assert.equal((await assessTaskSourceFreshness(framed,result.link)).status,'SOURCE_MISMATCH');
 const frame=await linkTaskSource(framed,{taskRef:'TASK-WEB-1'});
 assert.equal(frame.status,'OK');assert.notEqual(frame.link.evidenceRef,result.link.evidenceRef);
 assert.equal((await assessTaskSourceFreshness(page,{...result.link,u04BindingRef:'new-window'})).status,'CURRENT');
 await assert.rejects(()=>bindSourceObject(host,{...browser,objectKind:'browser.frame',representation:'html'}),/TASK_SOURCE_OBJECT_INVALID/);
 status='SOURCE_UNAVAILABLE';
 assert.deepEqual(await assessTaskSourceFreshness(page,result.link),
  {status:'UNKNOWN',reason:'SOURCE_UNAVAILABLE',currentRevision:null});
});

test('Racing source never makes Task evidence, returns mismatch without content',async()=>{
 let refreshCalls=0;
 const descriptor=sourceDescriptor({providerId:'file.local',sourceRef:'file://source/a'});
 const o=(status,revision)=>sourceObservation(descriptor,{status,revision,observedAt:new Date().toISOString(),
  provenance:{provider:'fixture'}});
 const port={describe:()=>descriptor,refresh:()=>{refreshCalls++;return o('OK','sha256:'+'a'.repeat(64))},
  read:()=>o('REVISION_MISMATCH','sha256:'+'b'.repeat(64))};
 const result=await linkTaskSource(await bindSourceObject(port,note),{taskRef:'TASK-RACE-1'});
 assert.deepEqual(result,{status:'REVISION_MISMATCH',link:null});assert.equal(refreshCalls,1);
});

test('Changed owner, task and provenance are rejected independently',async()=>{
 const bytes=enc('stable');
 const host=createHostSelectedSourcePort({hostId:'personal-agent',scopeId:'user-a',sourceId:'notes',bindingId:'host-a',
  readSelected:()=>({status:'OK',bytes})});
 const p=await bindSourceObject(host,note);
 const {link}=await linkTaskSource(p,{taskRef:'TASK-TAMPER-1'});
 for(const modified of [{...link,taskRef:'TASK-TAMPER-2'},
  {...link,ownerRevision:7},{...link,objectRef:'Notes/Other.md'},
  {...link,provenance:{...link.provenance,underlyingSourceRef:'file://other'}}]){
    assert.equal((await assessTaskSourceFreshness(p,modified)).status,'SOURCE_MISMATCH');
 }
});

test('Capability-only U01 program cannot be read as Task content',async()=>{
 const d=sourceDescriptor({providerId:'program.u01',sourceRef:'program://OBSIDIAN',
  observationType:'program',capabilities:[{id:'source.describe',available:true},
  {id:'source.refresh',available:true},{id:'source.read',available:false}]});
 const base={describe:()=>d,refresh:()=>sourceObservation(d,{status:'OK',
  observedAt:new Date().toISOString(),provenance:{provider:'program.u01'}}),
  read:()=>{throw Error('MUST_NOT_READ')}};
 const result=await linkTaskSource(await bindSourceObject(base,note),{taskRef:'TASK-NOREAD-1'});
 assert.deepEqual(result,{status:'READ_UNSUPPORTED',link:null});
});
