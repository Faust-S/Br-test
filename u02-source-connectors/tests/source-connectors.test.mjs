import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,rm,symlink} from 'node:fs/promises';
import {spawnSync} from 'node:child_process';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createHash} from 'node:crypto';
import {pinSource,sourceChange,sourceDescriptor,sourceObservation} from '../port.mjs';
import {createLocalFileSourcePort} from '../local-file.mjs';
import {createAtlasGitHubSourcePort} from '../atlas-github.mjs';
import {createAuthorizedGitHubSourcePort} from '../authorized-github.mjs';
import {observeLocalGit} from '../../universal-install/host-bindings/local-git-source.mjs';
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

test('real local Git pin survives two commits and mutable worktree changes',async()=>{
 const root=await mkdtemp(join(tmpdir(),'u02-git-'));
 const git=(...args)=>{const r=spawnSync('git',['-C',root,...args],{encoding:'utf8',windowsHide:true});
   assert.equal(r.status,0,String(r.stderr));return String(r.stdout).trim();};
 try {
   git('init','--quiet');git('config','user.email','fixture@example.invalid');
   git('config','user.name','U02 fixture');
   await writeFile(join(root,'data.txt'),'version one');
   git('add','data.txt');git('commit','--quiet','-m','v1');
   const p=createLocalGitSourcePort({root,rootId:'checkout',relativePath:'data.txt',observeLocalGit});
   const a=await p.refresh();const old=pinSource(p.describe(),a);
   assert.equal(Buffer.from((await p.read(old)).content.data,'base64').toString(),'version one');
   await writeFile(join(root,'data.txt'),'version two');
   assert.equal((await p.refresh()).status,'SOURCE_DIRTY');
   git('add','data.txt');git('commit','--quiet','-m','v2');
   const b=await p.refresh();const fresh=pinSource(p.describe(),b);
   assert.equal(sourceChange(a,b).status,'CHANGED');
   assert.equal(Buffer.from((await p.read(old)).content.data,'base64').toString(),'version one');
   assert.equal(Buffer.from((await p.read(fresh)).content.data,'base64').toString(),'version two');
   assert.equal((await p.read({...fresh,revision:'main'})).status,'REVISION_REQUIRED');
   assert.equal((await p.read({...fresh,sourceRef:'git://other/data.txt'})).status,'REVISION_REQUIRED');
   const absent=createLocalGitSourcePort({root,rootId:'checkout',relativePath:'absent.txt',observeLocalGit});
   assert.equal((await absent.read(pinSource(absent.describe(),await absent.refresh()))).status,'SOURCE_MISSING');
   const bounded=createLocalGitSourcePort({root,rootId:'checkout',relativePath:'data.txt',observeLocalGit,maxBytes:2});
   assert.equal((await bounded.read(pinSource(bounded.describe(),await bounded.refresh()))).status,'SOURCE_TOO_LARGE');
   for(const relativePath of ['../secret','/etc/passwd','bad:path'])
     assert.throws(()=>createLocalGitSourcePort({root,rootId:'checkout',relativePath,observeLocalGit}));
 } finally {await rm(root,{recursive:true,force:true});}
});
test('host-supplied authorized GitHub observation and SHA read have no token ownership',async()=>{
 const secret='SYNTHETIC-CI-NOT-PUBLISHED';let sha='a'.repeat(40),requests=0;
 const observer=async repository=>({scope:'repository',repository,complete:true,status:'OK',
   branchHeads:{main:sha}});
 const fetcher=async (url,options)=>{
   requests++;const hostResolvedHeader={...options.headers,Authorization:'Bearer '+secret};
   assert.equal(hostResolvedHeader.Authorization,'Bearer '+secret);
   const bytes=Buffer.from(new URL(url).searchParams.get('ref').startsWith('a')?'private one':'private two');
   return reply(200,{type:'file',path:'LICENSE',encoding:'base64',size:bytes.length,
     content:bytes.toString('base64'),
     sha:createHash('sha1').update('blob '+bytes.length+String.fromCharCode(0)).update(bytes).digest('hex')});
 };
 const p=createAuthorizedGitHubSourcePort({repository:'Private/Example',branch:'main',relativePath:'LICENSE',
   observeAuthorizedRepo:observer,authorizedFetcher:fetcher,now:t});
 const a=await p.refresh(),old=pinSource(p.describe(),a);
 const read=await p.read(old);assert.equal(read.status,'OK');
 assert.equal(Buffer.from(read.content.data,'base64').toString(),'private one');
 sha='b'.repeat(40);const next=await p.refresh();
 assert.equal(sourceChange(a,next).status,'CHANGED');
 assert.equal(Buffer.from((await p.read(old)).content.data,'base64').toString(),'private one');
 assert.equal(requests,2);
 assert.ok(!JSON.stringify({a,next,read}).includes(secret));
 const unknown=createAuthorizedGitHubSourcePort({repository:'Private/Example',branch:'main',relativePath:'LICENSE',
   observeAuthorizedRepo:observer,authorizedFetcher:async()=>reply(404,{}),now:t});
 assert.equal((await unknown.read(pinSource(unknown.describe(),await unknown.refresh()))).status,'ACCESS_UNKNOWN');
 const denied=createAuthorizedGitHubSourcePort({repository:'Private/Example',branch:'main',relativePath:'LICENSE',
   observeAuthorizedRepo:async()=>({status:'AUTHORIZATION_REQUIRED'}),authorizedFetcher:async()=>{throw Error('SHOULD_NOT_FETCH')},now:t});
 assert.equal((await denied.refresh()).status,'AUTHORIZATION_REQUIRED');
});
test('provider-specific extension cannot overwrite contract authority',()=>{
 const d=sourceDescriptor({providerId:'file.local',sourceRef:'file://root/example'});
 const o=sourceObservation(d,{status:'OK',revision:digest('safe'),observedAt:t(),
   provenance:{provider:'test'},authority:'WRITE_GRANTED',schema:'INCOMPATIBLE',providerId:'evil',
   sourceRef:'file://other/file'});
 assert.equal(o.authority,'OBSERVATION_ONLY');assert.equal(o.schema,d.schema);
 assert.equal(o.providerId,d.providerId);assert.equal(o.sourceRef,d.sourceRef);
});


test('pin requires observation-only authority and matching source kind',()=>{
 const d=sourceDescriptor({providerId:'git.local',sourceRef:'git://repository',observationType:'repository'});
 const o=sourceObservation(d,{status:'OK',revision:sha('a'),observedAt:t(),provenance:{provider:'git'}});
 assert.throws(()=>pinSource(d,{...o,authority:'WRITE_GRANTED'}),/SOURCE_PIN_UNAVAILABLE/);
 assert.throws(()=>pinSource(d,{...o,observationType:'file'}),/SOURCE_PIN_UNAVAILABLE/);
 assert.equal(sourceChange(o,{...o,observationType:'file'}).status,'NOT_COMPARABLE');
 assert.throws(()=>sourceDescriptor({providerId:'git.local',sourceRef:'git://r',
  capabilities:[{id:'source.read',available:'yes'}]}),/SOURCE_DESCRIPTOR_INVALID/);
});
