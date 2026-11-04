import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,rm,symlink} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createHash} from 'node:crypto';
import {pinSource,sourceChange,sourceDescriptor,sourceObservation} from '../port.mjs';
import {createLocalFileSourcePort} from '../local-file.mjs';
import {createAtlasGitHubSourcePort} from '../atlas-github.mjs';
import {createLocalGitSourcePort} from '../local-git.mjs';
import {createProgramCapabilityView} from '../program-view.mjs';
const t=()=>'2026-11-04T10:00:00Z',sha=c=>c.repeat(40);
const digest=x=>'sha256:'+createHash('sha256').update(x).digest('hex');
const item=x=>{const b=Buffer.from(x);return {type:'file',path:'LICENSE',size:b.length,encoding:'base64',content:b.toString('base64'),sha:createHash('sha1').update('blob '+b.length+'\0').update(b).digest('hex')};};
const reply=(status,x)=>({ok:status>=200&&status<300,status,json:async()=>x});
test('source revision and authority',()=>{
 const d=sourceDescriptor({providerId:'file.local',sourceRef:'file://r/a'});
 const a=sourceObservation(d,{status:'OK',revision:digest('a'),observedAt:t(),provenance:{provider:'local'}});
 assert.equal(pinSource(d,a).revision,a.revision);
 assert.equal(sourceChange(a,{...a,revision:digest('b')}).status,'CHANGED');
 assert.equal(sourceChange(a,a).status,'UNCHANGED');
 assert.equal(d.authority,'OBSERVATION_ONLY');
 assert.throws(()=>sourceDescriptor({...d,revision:'latest'}));
 assert.throws(()=>pinSource(d,{...a,sourceRef:'file://other/a'}));
});
test('local file pin, read, changed, stale, repin, deleted, bad paths',async()=>{
 const root=await mkdtemp(join(tmpdir(),'u02-'));
 try{
  await writeFile(join(root,'file.txt'),'first');
  const p=createLocalFileSourcePort({root,rootId:'work',relativePath:'file.txt',now:t});
  assert.equal((await p.read(p.describe())).status,'REVISION_REQUIRED');
  const a=await p.refresh(),pin=pinSource(p.describe(),a);
  assert.equal(a.revision,digest('first'));
  assert.equal(Buffer.from((await p.read(pin)).content.data,'base64').toString(),'first');
  await writeFile(join(root,'file.txt'),'second');
  const b=await p.refresh();
  assert.equal(sourceChange(a,b).status,'CHANGED');
  assert.equal((await p.read(pin)).status,'REVISION_MISMATCH');
  assert.equal(Buffer.from((await p.read(pinSource(p.describe(),b))).content.data,'base64').toString(),'second');
  assert.ok(!JSON.stringify(b).includes(root));
  for(const path of ['../outside','CON.txt','D:\\temp'])assert.throws(()=>createLocalFileSourcePort({root,rootId:'work',relativePath:path}));
  const q=createLocalFileSourcePort({root,rootId:'other',relativePath:'file.txt',maxBytes:2});
  assert.equal((await q.refresh()).status,'SOURCE_TOO_LARGE');
  try{
   await symlink(join(root,'file.txt'),join(root,'link.txt'));
   assert.equal((await createLocalFileSourcePort({root,rootId:'work',relativePath:'link.txt'}).refresh()).status,'SOURCE_INVALID');
  }catch(e){if(process.platform!=='win32'||!['EPERM','EACCES'].includes(e.code))throw e;}
  await rm(join(root,'file.txt'));assert.equal((await p.refresh()).status,'SOURCE_MISSING');
 }finally{await rm(root,{recursive:true,force:true});}
});
test('public GitHub immutable SHA read and branch change',async()=>{
 let head=sha('a');const requests=[];
 const obs=async repository=>({scope:'repository',repository,complete:true,branchHeads:{main:head}});
 const contents={a:item('old'),b:item('new')};
 const fetcher=async(url,options)=>{requests.push([url,options]);return reply(200,contents[new URL(url).searchParams.get('ref')[0]]);};
 const p=createAtlasGitHubSourcePort({repository:'Faust-S/Atlas',branch:'main',relativePath:'LICENSE',observePublicRepo:obs,fetcher,now:t});
 const first=await p.refresh(),pin=pinSource(p.describe(),first);
 assert.equal(Buffer.from((await p.read(pin)).content.data,'base64').toString(),'old');
 head=sha('b');const second=await p.refresh();
 assert.equal(sourceChange(first,second).status,'CHANGED');
 assert.equal(Buffer.from((await p.read(pin)).content.data,'base64').toString(),'old');
 assert.equal(Buffer.from((await p.read(pinSource(p.describe(),second))).content.data,'base64').toString(),'new');
 assert.ok(requests.every(([url,o])=>url.includes('ref=')&&!('Authorization' in o.headers)));
});
test('GitHub denial, missing, rate, invalid blob, incomplete observation',async()=>{
 const b={repository:'Faust-S/Atlas',branch:'main',relativePath:'LICENSE',
 observePublicRepo:async repository=>({scope:'repository',repository,complete:true,branchHeads:{main:sha('a')}})};
 const port=fetcher=>createAtlasGitHubSourcePort({...b,fetcher,now:t});
 const pinned=pinSource(port(()=>reply(200,item('ok'))).describe(),await port(()=>reply(200,item('ok'))).refresh());
 for(const [code,status] of [[401,'ACCESS_DENIED'],[404,'SOURCE_MISSING'],[429,'RATE_LIMITED']])
  assert.equal((await port(()=>reply(code,{})).read(pinned)).status,status);
 assert.equal((await port(()=>reply(200,{...item('ok'),sha:sha('f')})).read(pinned)).status,'SOURCE_INVALID');
 assert.equal((await createAtlasGitHubSourcePort({...b,maxBytes:1,fetcher:()=>reply(200,item('large'))}).read(pinned)).status,'SOURCE_TOO_LARGE');
 assert.throws(()=>createAtlasGitHubSourcePort({...b,relativePath:'../escape'}));
 const incomplete=createAtlasGitHubSourcePort({...b,observePublicRepo:async()=>({scope:'repository',repository:b.repository,complete:false})});
 assert.equal((await incomplete.refresh()).status,'SOURCE_INCOMPLETE');
});
test('U01 capabilities observed without invoking or connecting',async()=>{
 let effects=0;const c={
 inspect:()=>({schema:'IWXA_PROGRAM_CONNECTION/1.0.0',program_id:'JQ_CLI_V1',status:'DISCOVERED',transport:'cli'}),
 capabilities:()=>({schema:'IWXA_PROGRAM_CONNECTION/1.0.0',program_id:'JQ_CLI_V1',connected:false,operations:[{name:'evaluate',declared:true,verified:false}]}),
 invoke:()=>effects++,connect:()=>effects++};
 const p=createProgramCapabilityView({programId:'JQ_CLI_V1',connection:c,now:t});
 assert.equal((await p.refresh()).status,'OK');
 assert.equal(p.describe().capabilities[2].available,false);
 assert.equal((await p.read()).status,'READ_UNSUPPORTED');assert.equal(effects,0);
});
test('existing local Git observer: dirty worktree is not a pinned complete source',async()=>{
 let dirty=false;
 const p=createLocalGitSourcePort({root:process.cwd(),rootId:'work',now:t,
 observeLocalGit:()=>({status:'OK',headSha:sha('a'),headRef:'main',dirty})});
 const clean=await p.refresh();assert.equal(clean.status,'OK');
 dirty=true;const changed=await p.refresh();
 assert.equal(changed.status,'SOURCE_DIRTY');assert.equal(sourceChange(clean,changed).status,'NOT_COMPARABLE');
 assert.equal((await p.read()).status,'READ_UNSUPPORTED');
});
