import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {createAtlasGitHubSourcePort} from '../atlas-github.mjs';
import {pinSource} from '../port.mjs';
const sha='a'.repeat(40);
const reply=(data)=>({ok:true,status:200,json:async()=>data});
test('Unicode and spaces in GitHub file path are encoded without changing revision',async()=>{
 const path='docs/Смета 2026 + план.md';
 let requested='';
 const fetcher=async url=>{
  requested=url;
  const bytes=Buffer.from('content');
  return reply({type:'file',path,encoding:'base64',size:bytes.length,
   content:bytes.toString('base64'),
   sha:createHash('sha1').update('blob '+bytes.length+'\0').update(bytes).digest('hex')});
 };
 const observer=async repository=>({scope:'repository',repository,complete:true,
  branchHeads:{'feature/a+b':sha}});
 const port=createAtlasGitHubSourcePort({repository:'Faust-S/Atlas',branch:'feature/a+b',
  relativePath:path,observePublicRepo:observer,fetcher});
 const pinned=pinSource(port.describe(),await port.refresh());
 const read=await port.read(pinned);
 assert.equal(read.status,'OK');
 assert.equal(read.revision,sha);
 assert.ok(requested.includes('%D0%A1'));
 assert.ok(requested.includes('%20'));
 assert.ok(requested.includes('%2B'));
 assert.ok(requested.includes('ref='+sha));
 for(const invalid of ['../x','docs//x','docs/..','docs\\escape','docs/\0'])
  assert.throws(()=>createAtlasGitHubSourcePort({repository:'Faust-S/Atlas',branch:'main',
   relativePath:invalid,observePublicRepo:observer,fetcher}));
});
