import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp, mkdir, readFile, rm, writeFile, copyFile} from 'node:fs/promises';
import {spawnSync} from 'node:child_process';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {fileURLToPath} from 'node:url';

const sourceRoot = fileURLToPath(new URL('../', import.meta.url));
const fixturePath = fileURLToPath(new URL('./fixtures/packed-host-consumer.mjs', import.meta.url));
const npm = process.platform === 'win32' ? 'npm.cmd' : 'npm';

function run(command, args, cwd, timeout = 45000) {
  const result = spawnSync(command, args, {
    cwd, encoding: 'utf8', timeout, windowsHide: true,
    shell: process.platform === 'win32',
    env: {
      ...process.env,
      npm_config_update_notifier: 'false',
      npm_config_audit: 'false',
      npm_config_fund: 'false',
      npm_config_offline: 'true'
    }
  });
  assert.equal(result.status, 0,
    command + ' failed (' + (result.error?.message || result.status) + '): ' +
    (result.stderr || result.stdout || '').slice(0, 1200));
  return result.stdout.trim();
}

test('npm tarball installs offline in a fresh host and reads a pinned source across process restarts', async () => {
  const root = await mkdtemp(join(tmpdir(), 'iwxa-u02-package-consumer-'));
  try {
    const artifacts = join(root, 'artifacts'), host = join(root, 'host');
    await mkdir(artifacts);
    await mkdir(host);
    const output = run(npm, [
      'pack', sourceRoot, '--pack-destination', artifacts, '--json', '--ignore-scripts',
      '--offline'
    ], root);
    const result = JSON.parse(output);
    assert.equal(result.length, 1);
    const [packed] = result;
    assert.equal(packed.name, '@iwxa/u02-source-connectors');
    assert.equal(packed.version, '0.1.0');
    const included = packed.files.map(f => f.path);
    for (const required of [
      'package.json', 'index.mjs', 'port.mjs', 'source-read.mjs',
      'local-file.mjs', 'source-connector-v1.schema.json'
    ]) assert.ok(included.includes(required), required);
    assert.ok(included.every(path => !path.startsWith('tests/') &&
      !path.startsWith('qualification/') && !/README|\.md$/i.test(path)));
    const archive = join(artifacts, packed.filename);
    await writeFile(join(host, 'package.json'), '{"type":"module","private":true}\n');
    run(npm, [
      'install', archive, '--ignore-scripts', '--no-audit', '--no-fund',
      '--offline', '--package-lock=false'
    ], host);
    await copyFile(fixturePath, join(host, 'consumer.mjs'));
    assert.match(run(process.execPath, ['consumer.mjs', 'init'], host),
      /U02_PACKED_HOST_INITIAL_PASS/);
    assert.match(run(process.execPath, ['consumer.mjs', 'reopen'], host),
      /U02_PACKED_HOST_REOPEN_PASS/);
    assert.match(run(process.execPath, ['consumer.mjs', 'stale'], host),
      /U02_PACKED_HOST_STALE_PASS/);
    const installed = JSON.parse(await readFile(
      join(host, 'node_modules', '@iwxa', 'u02-source-connectors', 'package.json'), 'utf8'));
    assert.equal(installed.version, '0.1.0');
  } finally {
    await rm(root, {recursive: true, force: true});
  }
});
