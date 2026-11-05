import test from 'node:test';
import assert from 'node:assert/strict';
import {createHash, randomBytes} from 'node:crypto';
import {readFile} from 'node:fs/promises';
import {spawnSync} from 'node:child_process';
import {dirname, join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {
  sourceDescriptor, sourceObservation, captureSource, readPinnedSource, sourceChange
} from '../web.mjs';
const root = dirname(fileURLToPath(new URL('../', import.meta.url)));
const revision = text => 'sha256:' + createHash('sha256').update(text).digest('hex');
const now = '2026-11-05T00:00:00.000Z';
const observe = (descriptor, status, value = null, opts = {}) => sourceObservation(descriptor, {
  status,revision: value === null ? null : revision(value), observedAt:now,
  provenance:{provider:'browser-host'},...opts
});

test('Web entry has no Node native imports in its complete static graph', async () => {
  for (const name of ['web.mjs','port.mjs','source-read.mjs']) {
    const src = await readFile(join(root,name),'utf8');
    assert.doesNotMatch(src, /(?:from|import\()\s*['"]node:/);
    assert.doesNotMatch(src, /\bBuffer\b|\brequire\(/);
  }
});

test('browser-style read works with Buffer disabled for arbitrary binary bytes', async () => {
  const binary=randomBytes(8192);
  const descriptor=sourceDescriptor({providerId:'web.host',sourceRef:'web://example/source'});
  const previous=globalThis.Buffer;
  try {
    globalThis.Buffer=undefined;
    const data=Uint8Array.from(binary);
    const base64=btoa(Array.from(data,x=>String.fromCharCode(x)).join(''));
    const hash=revision(binary);
    const port={
      describe:()=>descriptor,
      refresh:()=>observe(descriptor,'OK',binary),
      read:async pin=>observe(descriptor,'OK',binary,{bytes:data.length,contentRef:hash,
        content:{encoding:'base64',data:base64}})
    };
    const obtained=await captureSource(port);
    assert.equal(obtained.status,'OK');
    assert.equal(obtained.pin.revision,hash);
    assert.equal((await readPinnedSource(port,JSON.parse(JSON.stringify(obtained.pin)))).status,'OK');
    assert.equal(obtained.read.bytes,binary.length);
  } finally {globalThis.Buffer=previous;}
});

test('browser-style consumer rejects substituted content and preserves stale pin',async()=>{
  const descriptor=sourceDescriptor({providerId:'web.host',sourceRef:'web://example/source'});
  let value='original';
  const port={
    describe:()=>descriptor,
    refresh:async()=>observe(descriptor,'OK',value),
    read:async pin=>{
      if(pin.revision!==revision(value))return observe(descriptor,'REVISION_MISMATCH',value);
      return observe(descriptor,'OK',value,{bytes:new TextEncoder().encode(value).length,
        contentRef:revision(value),content:{encoding:'base64',data:btoa(value)}});
    }
  };
  const first=await captureSource(port);
  value='updated';
  const stale=await readPinnedSource(port,JSON.parse(JSON.stringify(first.pin)));
  assert.equal(stale.status,'REVISION_MISMATCH');
  assert.equal(stale.content,null);
  const next=await captureSource(port);
  assert.equal(sourceChange(first.observation,next.observation).status,'CHANGED');
  const forged={...port,read:async()=>observe(descriptor,'OK','original',{
    bytes:8,contentRef:revision('original'),content:{encoding:'base64',data:btoa('tampered')}})};
  await assert.rejects(()=>readPinnedSource(forged,first.pin),/SOURCE_READ_INTEGRITY_INVALID/);
});

test('Web entry imports in a completely new process without Buffer',async()=>{
  const code = String.raw`globalThis.Buffer=undefined;
    const {sourceDescriptor,sourceObservation,captureSource}=await import('./web.mjs');
    const d=sourceDescriptor({providerId:'web.host',sourceRef:'web://example/source'});
    const value='hello';
    const bytes=new TextEncoder().encode(value);
    const h=new Uint8Array(await crypto.subtle.digest('SHA-256',bytes));
    const sha='sha256:'+Array.from(h,b=>b.toString(16).padStart(2,'0')).join('');
    const o=(content=false)=>sourceObservation(d,{status:'OK',revision:sha,
      observedAt:'2026-11-05T00:00:00.000Z',provenance:{provider:'host'},contentRef:sha,bytes:bytes.length,
      content:content?{encoding:'base64',data:btoa(value)}:null});
    const port={describe:()=>d,refresh:()=>o(),read:()=>o(true)};
    const result=await captureSource(port);
    if(result.status!=='OK'||result.read.contentRef!==sha)process.exit(1);
    console.log('U02_WEB_ENTRY_NO_BUFFER_PASS');`;
  const child = spawnSync(process.execPath,['--input-type=module','-e',code],{
    cwd:root,encoding:'utf8',windowsHide:true,timeout:10000
  });
  assert.equal(child.status,0,child.stderr);
  assert.match(child.stdout,/U02_WEB_ENTRY_NO_BUFFER_PASS/);
});
