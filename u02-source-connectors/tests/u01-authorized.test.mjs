import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {createU01AuthorizedGitHubSourcePort} from '../u01-authorized-adapter.mjs';
import {pinSource,sourceChange} from '../port.mjs';

const COMMIT_A='a'.repeat(40),COMMIT_B='b'.repeat(40);
const response=(status,body)=>({ok:status>=200&&status<300,status,json:async()=>body,headers:{get:()=>null}});
const blob=content=>{const bytes=Buffer.from(content);return {type:'file',path:'source.txt',encoding:'base64',size:bytes.length,content:bytes.toString('base64'),sha:createHash('sha1').update('blob '+bytes.length+String.fromCharCode(0)).update(bytes).digest('hex')};};

test('real U01 authorized observer integrates with U02 pinned GitHub read',async()=>{
 let token='test-token',head=COMMIT_A;const seen=[];
 const credentialProvider=async(provider,repo)=>{assert.equal(provider,'github');assert.equal(repo,'Private/Test');return token};
 const fetcher=async(url,options)=>{
  seen.push({url,authorization:options.headers.Authorization});
  if(options.headers.Authorization!=='Bearer test-token')return response(401,{});
  if(url==='https://api.github.com/repos/Private/Test')
   return response(200,{full_name:'Private/Test',id:123,default_branch:'main'});
  if(url.endsWith('/branches?per_page=100&page=1'))
   return response(200,[{name:'main',commit:{sha:head}}]);
  if(url.includes('/contents/source.txt?ref='))
   return response(200,blob(url.endsWith(COMMIT_A)?'old':'new'));
  throw Error('UNEXPECTED_REQUEST');
 };
 const port=createU01AuthorizedGitHubSourcePort({repository:'Private/Test',branch:'main',relativePath:'source.txt',fetcher,credentialProvider});
 const before=await port.refresh();assert.equal(before.status,'OK');
 const pinned=pinSource(port.describe(),before);
 const old=await port.read(pinned);assert.equal(old.status,'OK');
 assert.equal(Buffer.from(old.content.data,'base64').toString(),'old');
 head=COMMIT_B;
 const changed=await port.refresh();assert.equal(sourceChange(before,changed).status,'CHANGED');
 assert.equal(Buffer.from((await port.read(pinned)).content.data,'base64').toString(),'old');
 assert.equal(Buffer.from((await port.read(pinSource(port.describe(),changed))).content.data,'base64').toString(),'new');
 assert.ok(seen.every(s=>s.authorization==='Bearer test-token'));
 assert.ok(!JSON.stringify({before,changed,old,pinned}).includes('test-token'));
 token='expired';assert.equal((await port.read(pinned)).status,'AUTHORIZATION_REQUIRED');
 token=null;assert.equal((await port.read(pinned)).status,'AUTHORIZATION_REQUIRED');
 assert.equal((await port.refresh()).status,'AUTHORIZATION_REQUIRED');
});

test('U01 authorized observer keeps 404 and incomplete access untrusted',async()=>{
 const credentialProvider=async()=> 'test-token';
 let mode='hidden';
 const fetcher=async(url)=>{
  if(mode==='hidden')return response(404,{});
  if(url.endsWith('/branches?per_page=100&page=1'))return response(403,{});
  return response(200,{full_name:'Private/Test',default_branch:'main'});
 };
 const port=createU01AuthorizedGitHubSourcePort({repository:'Private/Test',branch:'main',relativePath:'source.txt',fetcher,credentialProvider});
 assert.equal((await port.refresh()).status,'ACCESS_UNKNOWN');
 mode='partial';assert.equal((await port.refresh()).status,'PARTIAL_ACCESS_DENIED');
 assert.throws(()=>createU01AuthorizedGitHubSourcePort({repository:'Private/Test',branch:'main',relativePath:'source.txt'}),/GITHUB_HOST_CALLBACKS_REQUIRED/);
});
