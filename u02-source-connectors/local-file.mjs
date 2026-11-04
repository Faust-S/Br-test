import {createHash} from 'node:crypto';
import {lstat, realpath, readFile, stat} from 'node:fs/promises';
import {isAbsolute, join, relative, resolve, sep} from 'node:path';
import {sourceDescriptor, sourceObservation, requirePinnedSource} from './port.mjs';

const DEFAULT_MAX_BYTES = 8 * 1024 * 1024;
const digest = buffer => 'sha256:' + createHash('sha256').update(buffer).digest('hex');

export function createLocalFileSourcePort({root, rootId, relativePath, maxBytes = DEFAULT_MAX_BYTES,
  now = () => new Date().toISOString()} = {}) {
  if (typeof root !== 'string' || !isAbsolute(root) || root.includes('\0') ||
      typeof rootId !== 'string' || !/^[a-z][a-z0-9_-]{0,63}$/.test(rootId) ||
      typeof relativePath !== 'string' || relativePath.length > 1024 ||
      !relativePath || relativePath.includes('\\') ||
      !Number.isInteger(maxBytes) || maxBytes < 1 || maxBytes > 64 * 1024 * 1024 ||
      typeof now !== 'function') throw Error('LOCAL_SOURCE_CONFIG_INVALID');
  const segments = relativePath.split('/');
  if (segments.some(s => !s || s === '.' || s === '..' || /[\x00-\x1f<>:"|?*]/.test(s) ||
      /[. ]$/.test(s) || /^(?:CON|PRN|AUX|NUL|COM[1-9]|LPT[1-9])(?:\.|$)/i.test(s)))
    throw Error('LOCAL_SOURCE_PATH_INVALID');
  const descriptor = sourceDescriptor({providerId: 'file.local',
    sourceRef: `file://${rootId}/${segments.map(encodeURIComponent).join('/')}`});
  const provenance = {provider: 'local-file', access: 'host-selected-root',
    authority: 'local-file-read', rootId, relativePath};
  const observe = (status, extra = {}) => sourceObservation(descriptor,
    {status, observedAt: now(), provenance, ...extra});

  async function capture() {
    try {
      const safeRoot = await realpath(resolve(root));
      let cursor = safeRoot;
      for (const segment of segments) {
        cursor = join(cursor, segment);
        const item = await lstat(cursor);
        if (item.isSymbolicLink()) return {status: 'SOURCE_INVALID'};
      }
      const actual = await realpath(cursor);
      const delta = relative(safeRoot, actual);
      if (!delta || delta === '..' || delta.startsWith('..' + sep) || isAbsolute(delta))
        return {status: 'SOURCE_INVALID'};
      const info = await stat(actual);
      if (!info.isFile()) return {status: 'SOURCE_INVALID'};
      if (info.size > maxBytes) return {status: 'SOURCE_TOO_LARGE'};
      const buffer = await readFile(actual);
      if (buffer.length > maxBytes) return {status: 'SOURCE_TOO_LARGE'};
      return {status: 'OK', buffer, revision: digest(buffer)};
    } catch (e) {
      if (e?.code === 'ENOENT') return {status: 'SOURCE_MISSING'};
      return {status: 'SOURCE_UNAVAILABLE'};
    }
  }

  return Object.freeze({
    describe: () => descriptor,
    refresh: async () => {
      const result = await capture();
      return observe(result.status, result.status === 'OK'
        ? {revision: result.revision, contentRef: result.revision, bytes: result.buffer.length}
        : {});
    },
    read: async pinned => {
      if (!requirePinnedSource(descriptor, pinned)) return observe('REVISION_REQUIRED');
      const result = await capture();
      if (result.status !== 'OK') return observe(result.status);
      if (result.revision !== pinned.revision)
        return observe('REVISION_MISMATCH', {revision: result.revision, contentRef: result.revision,
          bytes: result.buffer.length});
      return observe('OK', {revision: result.revision, contentRef: result.revision,
        bytes: result.buffer.length, content: {encoding: 'base64', data: result.buffer.toString('base64')}});
    }
  });
}
