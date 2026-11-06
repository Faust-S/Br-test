import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp, mkdir, copyFile, rm, readFile} from 'node:fs/promises';
import {spawnSync} from 'node:child_process';
import {tmpdir} from 'node:os';
import {join, dirname} from 'node:path';
import {fileURLToPath} from 'node:url';

const sourceRoot = fileURLToPath(new URL('../', import.meta.url));

test('standalone U02 package imports without U01/Atlas/Center sibling sources', async () => {
  const pkg = JSON.parse(await readFile(join(sourceRoot, 'package.json'), 'utf8'));
  assert.equal(pkg.name, '@iwxa/u02-source-connectors');
  assert.ok(pkg.files.every(name => !name.startsWith('tests/') && !name.startsWith('../')));
  const root = await mkdtemp(join(tmpdir(), 'iwxa-u02-package-'));
  try {
    const target = join(root, 'node_modules', '@iwxa', 'u02-source-connectors');
    await mkdir(target, {recursive: true});
    for (const name of ['package.json', ...pkg.files]) {
      const destination = join(target, name);
      await mkdir(dirname(destination), {recursive: true});
      await copyFile(join(sourceRoot, name), destination);
    }
    const script = `import {sourceDescriptor, captureSource, readPinnedSource,
      createHttpSourcePort, createLocalFileSourcePort, createAtlasGitHubSourcePort,
      createLocalGitSourcePort, createProgramCapabilityView} from '@iwxa/u02-source-connectors';
      if (typeof captureSource !== 'function' || typeof readPinnedSource !== 'function' ||
        typeof createHttpSourcePort !== 'function' || typeof createLocalFileSourcePort !== 'function' ||
        typeof createAtlasGitHubSourcePort !== 'function' || typeof createLocalGitSourcePort !== 'function' ||
        typeof createProgramCapabilityView !== 'function' ||
        sourceDescriptor({providerId:'file.local',sourceRef:'file://a/b'}).authority !== 'OBSERVATION_ONLY')
        process.exit(3);
      const agent = await import('@iwxa/u02-source-connectors/agent03');
      const web = await import('@iwxa/u02-source-connectors/web');
      if (typeof agent.projectAgent03SourceEvidence !== 'function' ||
        typeof agent.compareAgent03P04AtlasReference !== 'function' ||
        typeof web.assessTaskSourceFreshness !== 'function') process.exit(4);
      console.log('U02_PACKAGE_IMPORT_PASS');`;
    const ran = spawnSync(process.execPath, ['--input-type=module', '-e', script], {
      cwd: root, encoding: 'utf8', timeout: 10000, windowsHide: true
    });
    assert.equal(ran.status, 0, ran.stderr);
    assert.match(ran.stdout, /U02_PACKAGE_IMPORT_PASS/);
  } finally {
    await rm(root, {recursive:true, force:true});
  }
});
