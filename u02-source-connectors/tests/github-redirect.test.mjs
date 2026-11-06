import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash} from 'node:crypto';
import {createAtlasGitHubSourcePort} from '../atlas-github.mjs';
import {pinSource} from '../port.mjs';

const sha='a'.repeat(40);
const bytes=Buffer.from('fixed-file');
const file={type:'file',path:'LICENSE',size:bytes.length,encoding:'base64',
 content:bytes.toString('base64'),
 sha:createHash('sha1').update('blob '+bytes.length+'\0').update(bytes).digest('hex')};
const observer=async repository=>({scope:'repository',repository,complete:true,branchHeads:{main:sha}});
const make=fetcher=>createAtlasGitHubSourcePort({repository:'Faust-S/Atlas',branch:'main',
 relativePath:'LICENSE',observePublicRepo:observer,fetcher});

test('a redirected or substituted final GitHub URL cannot satisfy a pin',async()=>{
 for(const mode of ['redirected','different-url']){
  let options;
  const port=make(async(url,config)=>{options=config;return {ok:true,status:200,
   redirected:mode==='redirected',url:mode==='different-url'?'https://example.invalid/file':url,
   json:async()=>file};});
  const pin=pinSource(port.describe(),await port.refresh());
  const result=await port.read(pin);
  assert.equal(options.redirect,'error');
  assert.equal(result.status,'SOURCE_UNAVAILABLE');
  assert.equal(result.content,null);
 }
});

test('normal pinned GitHub content remains readable without redirects',async()=>{
 const port=make(async(url,options)=>({ok:true,status:200,redirected:false,url,json:async()=>file}));
 const pin=pinSource(port.describe(),await port.refresh());
 const result=await port.read(pin);
 assert.equal(result.status,'OK');
 assert.equal(result.revision,sha);
 assert.equal(Buffer.from(result.content.data,'base64').toString(),'fixed-file');
});

test('fetch transport redirect failures produce no source content',async()=>{
 const port=make(async()=>{throw Error('redirect was blocked')});
 const pin=pinSource(port.describe(),await port.refresh());
 assert.equal((await port.read(pin)).status,'SOURCE_UNAVAILABLE');
});
