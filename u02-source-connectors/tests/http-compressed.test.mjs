import test from 'node:test';
import assert from 'node:assert/strict';
import http from 'node:http';
import {gzipSync, brotliCompressSync} from 'node:zlib';
import {createHash} from 'node:crypto';
import {createHttpSourcePort} from '../http-source.mjs';
import {pinSource} from '../port.mjs';

const fingerprint = bytes => 'sha256:' + createHash('sha256').update(bytes).digest('hex');
function encode(encoding, bytes) {
  return encoding === 'gzip' ? gzipSync(bytes) : brotliCompressSync(bytes);
}
async function withServer(run) {
  let content = Buffer.from('dataset '.repeat(120));
  const server = http.createServer((req, res) => {
    const encoding = req.url === '/br' ? 'br' : 'gzip';
    const encoded = encode(encoding, content);
    res.writeHead(200, {
      'Content-Encoding': encoding,
      'Content-Type': 'text/plain',
      'Content-Length': encoded.length
    });
    res.end(encoded);
  });
  await new Promise(resolve => server.listen(0, '127.0.0.1', resolve));
  try {
    await run({
      base: 'http://127.0.0.1:' + server.address().port,
      setContent: text => { content = Buffer.from(text); }
    });
  } finally {
    await new Promise(resolve => server.close(resolve));
  }
}

test('gzip and brotli sources pin decoded bytes, not compressed wire length', async () => {
  await withServer(async ({base, setContent}) => {
    for (const encoding of ['gzip', 'br']) {
      setContent('dataset '.repeat(120));
      const port = createHttpSourcePort({
        endpoint: base + '/' + encoding, sourceId: 'compressed.api', maxBytes: 4096
      });
      const before = await port.refresh();
      assert.equal(before.status, 'OK', encoding + ': ' + before.status);
      assert.equal(before.revision, fingerprint('dataset '.repeat(120)));
      const pinned = pinSource(port.describe(), before);
      const read = await port.read(pinned);
      assert.equal(read.status, 'OK');
      assert.equal(Buffer.from(read.content.data, 'base64').toString(), 'dataset '.repeat(120));
      setContent('changed'.repeat(100));
      const stale = await port.read(pinned);
      assert.equal(stale.status, 'REVISION_MISMATCH');
      assert.equal(stale.content, null);
      const changed = await port.refresh();
      assert.equal(changed.status, 'OK');
      assert.equal(changed.revision, fingerprint('changed'.repeat(100)));
    }
  });
});

test('compressed wire size may exceed maxBytes while decoded content fits', async () => {
  await withServer(async ({base, setContent}) => {
    setContent('ok');
    const port = createHttpSourcePort({
      endpoint: base + '/gzip', sourceId: 'compressed.api', maxBytes: 8
    });
    const observed = await port.refresh();
    assert.equal(observed.status, 'OK');
    assert.equal(observed.bytes, 2);
    assert.equal((await port.read(pinSource(port.describe(), observed))).status, 'OK');
  });
});

test('decompressed response is capped even when compressed wire body is small', async () => {
  await withServer(async ({base, setContent}) => {
    setContent('Z'.repeat(40_000));
    for (const encoding of ['gzip', 'br']) {
      const port = createHttpSourcePort({
        endpoint: base + '/' + encoding, sourceId: 'compressed.api', maxBytes: 64
      });
      const result = await port.refresh();
      assert.equal(result.status, 'SOURCE_TOO_LARGE');
      assert.equal(result.content, null);
    }
  });
});
