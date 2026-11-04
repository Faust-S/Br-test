import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import {spawn} from 'node:child_process';
import {mkdtemp,rm,writeFile} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';
import {createHttpSourcePort} from '../http-source.mjs';
import {pinSource} from '../port.mjs';

const childScript = new URL('./fixtures/http-read-child.mjs', import.meta.url);
const runChild = (endpoint, pinFile, token = 'synthetic') => new Promise((resolve,reject) => {
  const child = spawn(process.execPath, [fileURLToPath(childScript), endpoint, pinFile], {
    windowsHide: true, env: {...process.env, U02_FIXTURE_TOKEN: token}
  });
  let out = '', err = '';
  child.stdout.setEncoding('utf8'); child.stderr.setEncoding('utf8');
  child.stdout.on('data', x => out += x); child.stderr.on('data', x => err += x);
  child.on('error', reject);
  child.on('close', code => {
    if (code !== 0) reject(Error('HTTP_CHILD_FAILED: ' + err.slice(0,200)));
    else {try {resolve(JSON.parse(out));} catch(e) {reject(e);}}
  });
});

test('HTTP pin survives a cold process and rejects endpoint rebind, change and revoked credential',async()=>{
  const root = await mkdtemp(join(tmpdir(), 'iwxa-u02-http-'));
  let content = 'one', allow = true, requests = 0;
  const server = http.createServer((req,res)=>{
    requests++;
    if (!allow || req.headers.authorization !== 'Bearer synthetic') {
      res.writeHead(401);res.end();return;
    }
    if (req.url !== '/source') {res.writeHead(404);res.end();return;}
    res.writeHead(200,{'content-type':'text/plain'});res.end(content);
  });
  await new Promise(resolve=>server.listen(0,'127.0.0.1',resolve));
  try {
    const endpoint='http://127.0.0.1:' + server.address().port + '/source';
    const port = createHttpSourcePort({endpoint,sourceId:'document.api',
      authHeaders: async()=>({Authorization:'Bearer synthetic'})});
    const first = await port.refresh();
    assert.equal(first.status,'OK');
    const pinFile = join(root,'pin.json');
    await writeFile(pinFile,JSON.stringify(pinSource(port.describe(),first)));
    const fresh = await runChild(endpoint,pinFile);
    assert.deepEqual([fresh.status,fresh.text,fresh.revision,fresh.authority],
      ['OK','one',first.revision,'OBSERVATION_ONLY']);
    content = 'two';
    const stale = await runChild(endpoint,pinFile);
    assert.equal(stale.status,'REVISION_MISMATCH');
    assert.equal(stale.text,null);
    const before = requests;
    const rebound = await runChild(endpoint+'?other=1',pinFile);
    assert.equal(rebound.status,'REVISION_REQUIRED');
    assert.equal(requests,before);
    allow = false;
    const revoked = await runChild(endpoint,pinFile);
    assert.equal(revoked.status,'AUTHORIZATION_REQUIRED');
    assert.equal(revoked.text,null);
  } finally {
    await new Promise(resolve=>server.close(resolve));
    await rm(root,{recursive:true,force:true});
  }
});

test('HTTP descriptor binds endpoint and excludes URL query from exposed source identity',()=>{
  const a=createHttpSourcePort({endpoint:'https://example.test/data?apiKey=private-value',sourceId:'same.api'});
  const b=createHttpSourcePort({endpoint:'https://example.test/other?apiKey=private-value',sourceId:'same.api'});
  const c=createHttpSourcePort({endpoint:'https://example.test/data?apiKey=private-value',sourceId:'same.api'});
  assert.notEqual(a.describe().sourceRef,b.describe().sourceRef);
  assert.equal(a.describe().sourceRef,c.describe().sourceRef);
  assert.ok(!JSON.stringify(a.describe()).includes('private-value'));
});
