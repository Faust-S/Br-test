import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,writeFile,rm} from 'node:fs/promises';
import {spawnSync} from 'node:child_process';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {createLocalFileSourcePort} from '../local-file.mjs';
import {createLocalGitSourcePort} from '../local-git.mjs';
import {pinSource} from '../port.mjs';
import {captureSource,readPinnedSource} from '../source-read.mjs';

const git=(root,...args)=>{
 const run=spawnSync('git',['-C',root,...args],{encoding:'utf8',windowsHide:true});
 assert.equal(run.status,0,String(run.stderr));
 return String(run.stdout).trim();
};

test('same rootId and equal file bytes cannot reuse a pin at a different selected root',async()=>{
 const temp=await mkdtemp(join(tmpdir(),'u02-root-binding-'));
 const left=join(temp,'left'),right=join(temp,'right');
 try{
  await mkdir(left);await mkdir(right);
  await writeFile(join(left,'snapshot.json'),'{"same":true}');
  await writeFile(join(right,'snapshot.json'),'{"same":true}');
  const config={rootId:'documents',relativePath:'snapshot.json'};
  const a=createLocalFileSourcePort({...config,root:left});
  const b=createLocalFileSourcePort({...config,root:right});
  const before=await captureSource(a);
  assert.equal(before.status,'OK');
  const pin=JSON.parse(JSON.stringify(before.pin));
  const reopened=createLocalFileSourcePort({...config,root:left});
  assert.equal((await readPinnedSource(reopened,pin)).status,'OK');
  const different=await captureSource(b);
  assert.equal(different.status,'OK');
  assert.equal(before.observation.revision,different.observation.revision);
  assert.notEqual(a.describe().sourceRef,b.describe().sourceRef);
  assert.equal((await b.read(pin)).status,'REVISION_REQUIRED');
  await assert.rejects(()=>readPinnedSource(b,pin),/SOURCE_PIN_INVALID/);
  assert.ok(!JSON.stringify(a.describe()).includes(left));
  assert.ok(!JSON.stringify(b.describe()).includes(right));
 } finally {await rm(temp,{recursive:true,force:true});}
});

test('same rootId and cloned Git commit cannot reuse a pin at a different selected root',async()=>{
 const temp=await mkdtemp(join(tmpdir(),'u02-git-root-binding-'));
 const left=join(temp,'repo-left'),right=join(temp,'repo-right');
 try {
  await mkdir(left);git(left,'init','--quiet');
  git(left,'config','user.name','U02 fixture');
  git(left,'config','user.email','u02@example.invalid');
  await writeFile(join(left,'data.txt'),'same git snapshot');
  git(left,'add','data.txt');git(left,'commit','--quiet','-m','fixture');
  git(temp,'clone','--quiet',left,right);
  const observe=async root=>({status:'OK',headSha:git(root,'rev-parse','HEAD'),
    headRef:'main',dirty:false});
  const args={rootId:'checkout',relativePath:'data.txt',observeLocalGit:observe};
  const a=createLocalGitSourcePort({...args,root:left});
  const b=createLocalGitSourcePort({...args,root:right});
  const before=await captureSource(a),other=await captureSource(b);
  assert.equal(before.status,'OK');assert.equal(other.status,'OK');
  assert.equal(before.observation.revision,other.observation.revision);
  assert.equal((await readPinnedSource(createLocalGitSourcePort({...args,root:left}),before.pin)).status,'OK');
  assert.notEqual(a.describe().sourceRef,b.describe().sourceRef);
  assert.equal((await b.read(before.pin)).status,'REVISION_REQUIRED');
  await assert.rejects(()=>readPinnedSource(b,before.pin),/SOURCE_PIN_INVALID/);
  assert.ok(!JSON.stringify(a.describe()).includes(left));
  assert.ok(!JSON.stringify(b.describe()).includes(right));
 } finally {await rm(temp,{recursive:true,force:true});}
});
