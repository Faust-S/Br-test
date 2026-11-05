import assert from 'node:assert/strict';
import {mkdtemp, mkdir, readFile, readdir, rm, writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {build} from '../../center-public/node_modules/vite/dist/node/index.js';

const webEntry=fileURLToPath(new URL('../web.mjs',import.meta.url));
const root=await mkdtemp(join(tmpdir(),'iwxa-u02-webview-'));
try {
  await mkdir(join(root,'src'));
  await writeFile(join(root,'index.html'),
    '<!doctype html><html><head><meta charset="utf-8"></head><body><div id="app"></div><script type="module" src="/src/main.mjs"></script></body></html>');
  await writeFile(join(root,'src','main.mjs'), `
    import {sourceDescriptor,sourceObservation,captureSource,readPinnedSource,sourceChange}
      from '@iwxa/u02-source-connectors/web';
    const bytes = new TextEncoder().encode('Atlas Center source preview');
    const hash = new Uint8Array(await crypto.subtle.digest('SHA-256',bytes));
    const sha = 'sha256:' + Array.from(hash,b=>b.toString(16).padStart(2,'0')).join('');
    const descriptor=sourceDescriptor({providerId:'web.host',sourceRef:'web://atlas/preview'});
    const observation = content=>sourceObservation(descriptor,{status:'OK',revision:sha,
      observedAt:new Date().toISOString(),provenance:{provider:'web.host'},contentRef:sha,
      bytes:bytes.length,content:content?{encoding:'base64',data:btoa('Atlas Center source preview')}:null});
    const port={describe:()=>descriptor,refresh:()=>observation(false),read:()=>observation(true)};
    const captured=await captureSource(port);
    const restored=await readPinnedSource(port,JSON.parse(JSON.stringify(captured.pin)));
    window.IWXA_U02_WEBVIEW_READY = captured.status==='OK' &&
      restored.status==='OK' && sourceChange(captured.observation,captured.observation).status==='UNCHANGED';
  `);
  await build({root,configFile:false,logLevel:'error',
    resolve:{alias:[{find:'@iwxa/u02-source-connectors/web',replacement:webEntry}]},
    build:{target:'es2022',outDir:join(root,'dist'),emptyOutDir:true,minify:false}});
  const assets=await readdir(join(root,'dist','assets'));
  assert.ok(assets.some(f=>f.endsWith('.js')),'No JavaScript bundle produced');
  const javascript=(await Promise.all(assets.filter(f=>f.endsWith('.js'))
    .map(f=>readFile(join(root,'dist','assets',f),'utf8')))).join('\n');
  assert.ok(javascript.includes('IWXA_U02_WEBVIEW_READY'));
  assert.ok(javascript.includes('IWXA_SOURCE_CONNECTOR/1.0.0'));
  assert.doesNotMatch(javascript,/node:(?:crypto|fs|path|os)|\bBuffer\.from\(|\brequire\(/);
  console.log(JSON.stringify({schema:'IWXA_U02_CENTER_VITE_WEBVIEW/1',status:'PASS',
    bundler:'Center Vite',browserEntry:'./web',nodeNativeImports:false,readOnly:true,
    bundleFiles:assets.length}));
} finally {
  await rm(root,{recursive:true,force:true});
}
