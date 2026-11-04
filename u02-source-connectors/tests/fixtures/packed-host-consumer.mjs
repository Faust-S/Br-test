import assert from 'node:assert/strict';
import {readFile, writeFile} from 'node:fs/promises';
import {createLocalFileSourcePort, captureSource, readPinnedSource}
  from '@iwxa/u02-source-connectors';

const phase = process.argv[2];
const root = process.cwd(), relativePath = 'document.txt';
const port = () => createLocalFileSourcePort({root, rootId:'host', relativePath});
if (phase === 'init') {
  await writeFile(relativePath, 'first document');
  const result = await captureSource(port());
  assert.equal(result.status, 'OK');
  assert.equal(result.read.authority, 'OBSERVATION_ONLY');
  assert.equal(Buffer.from(result.read.content.data, 'base64').toString(), 'first document');
  await writeFile('saved-pin.json', JSON.stringify(result.pin));
  console.log('U02_PACKED_HOST_INITIAL_PASS');
} else {
  const pinned = JSON.parse(await readFile('saved-pin.json', 'utf8'));
  const result = await readPinnedSource(port(), pinned);
  if (phase === 'reopen') {
    assert.equal(result.status, 'OK');
    assert.equal(Buffer.from(result.content.data, 'base64').toString(), 'first document');
    await writeFile(relativePath, 'second document');
    console.log('U02_PACKED_HOST_REOPEN_PASS');
  } else if (phase === 'stale') {
    assert.equal(result.status, 'REVISION_MISMATCH');
    assert.equal(result.content, null);
    console.log('U02_PACKED_HOST_STALE_PASS');
  } else throw Error('UNKNOWN_PHASE');
}
