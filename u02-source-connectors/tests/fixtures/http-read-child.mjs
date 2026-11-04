import {readFile} from 'node:fs/promises';
import {createHttpSourcePort} from '../../http-source.mjs';

const [endpoint, pinPath] = process.argv.slice(2);
if (!endpoint || !pinPath) process.exit(2);
try {
  const pinned = JSON.parse(await readFile(pinPath, 'utf8'));
  const port = createHttpSourcePort({
    endpoint,
    sourceId: 'document.api',
    authHeaders: async () => ({Authorization: 'Bearer ' + process.env.U02_FIXTURE_TOKEN})
  });
  const observed = await port.read(pinned);
  console.log(JSON.stringify({status: observed.status, revision: observed.revision,
    contentRef: observed.contentRef, bytes: observed.bytes,
    text: observed.content ? Buffer.from(observed.content.data, 'base64').toString('utf8') : null,
    authority: observed.authority}));
} catch (e) {
  console.error(String(e));
  process.exitCode = 1;
}
