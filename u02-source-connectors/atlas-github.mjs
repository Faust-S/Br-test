import {createHash} from 'node:crypto';
import {sourceDescriptor, sourceObservation, requirePinnedSource} from './port.mjs';

const SHA = /^[a-f0-9]{40}$/i;
const MAX_BYTES = 1024 * 1024;
const REPO = /^[a-z\d](?:[a-z\d-]{0,37}[a-z\d])?\/[a-z\d_.-]+$/i;
const encodePath = path => path.split('/').map(encodeURIComponent).join('/');

export function createGitHubSourcePort({repository, branch, relativePath,
  observeRepository, access = 'public', fetcher = globalThis.fetch, maxBytes = MAX_BYTES,
  now = () => new Date().toISOString()} = {}) {
  if (typeof repository !== 'string' || !REPO.test(repository) || repository.includes('..') ||
      typeof branch !== 'string' || !branch || branch.length > 250 ||
      !/^[a-zA-Z0-9._/+\-]+$/.test(branch) || branch.includes('..') || branch.startsWith('/') ||
      branch.endsWith('/') ||
      typeof relativePath !== 'string' || !relativePath || relativePath.length > 1024 ||
      relativePath.split('/').some(s => !s || s === '.' || s === '..' ||
        /[\x00-\x1f\x7f\\]/.test(s) || Buffer.byteLength(s) > 255) ||
      !['public', 'authorized'].includes(access) ||
      typeof observeRepository !== 'function' || typeof fetcher !== 'function' ||
      !Number.isInteger(maxBytes) || maxBytes < 1 || maxBytes > MAX_BYTES ||
      typeof now !== 'function') throw Error('GITHUB_SOURCE_CONFIG_INVALID');
  const sourceRef = `github://${repository}/${encodePath(relativePath)}?ref=${encodeURIComponent(branch)}`;
  const descriptor = sourceDescriptor({providerId: 'github.' + access, sourceRef});
  const provenance = {provider: 'github', access, authority: 'GitHub REST API',
    repository, branch, path: relativePath};
  const observe = (status, extra = {}) => sourceObservation(descriptor,
    {status, observedAt: now(), provenance, ...extra});

  const port = {
    describe: () => descriptor,
    refresh: async () => {
      let snapshot;
      try {snapshot = await observeRepository(repository, fetcher, {maxPages: 20});}
      catch {return observe('SOURCE_UNAVAILABLE');}
      if (access === 'authorized' && snapshot?.status !== 'OK') {
        const status = snapshot?.status;
        return observe(typeof status === 'string' && /^[A-Z_]+$/.test(status) ? status : 'SOURCE_UNAVAILABLE');
      }
      if (!snapshot || snapshot.scope !== 'repository' || snapshot.complete !== true ||
          snapshot.repository?.toLowerCase() !== repository.toLowerCase())
        return observe('SOURCE_INCOMPLETE');
      const sha = snapshot.branchHeads?.[branch];
      if (sha == null) return observe('SOURCE_MISSING');
      if (!SHA.test(sha)) return observe('SOURCE_INVALID');
      return observe('OK', {revision: sha.toLowerCase(),
        branchRevision: sha.toLowerCase()});
    },
    read: async pinned => {
      if (!requirePinnedSource(descriptor, pinned) || !SHA.test(pinned.revision))
        return observe('REVISION_REQUIRED');
      const url = `https://api.github.com/repos/${repository}/contents/${encodePath(relativePath)}?ref=${pinned.revision}`;
      let response;
      try {response = await fetcher(url, {headers: {Accept: 'application/vnd.github+json'}, redirect: 'error'});}
      catch {return observe('SOURCE_UNAVAILABLE');}
      if (response?.redirected === true ||
          (typeof response?.url === 'string' && response.url && response.url !== url))
        return observe('SOURCE_UNAVAILABLE');
      if (response?.status === 404) return observe(access === 'authorized' ? 'ACCESS_UNKNOWN' : 'SOURCE_MISSING');
      if (response?.status === 429 || (response?.status === 403 &&
            response?.headers?.get?.('x-ratelimit-remaining') === '0'))
        return observe('RATE_LIMITED');
      if (response?.status === 401 || response?.status === 403)
        return observe(access === 'authorized' && response.status === 401 ? 'AUTHORIZATION_REQUIRED' : 'ACCESS_DENIED');
      if (!response?.ok) return observe('SOURCE_UNAVAILABLE');
      let item;
      try {item = await response.json();}
      catch {return observe('SOURCE_INVALID');}
      if (item?.type !== 'file' || item.path !== relativePath ||
          item.encoding !== 'base64' || typeof item.content !== 'string' ||
          !Number.isSafeInteger(item.size) || item.size < 0 ||
          !SHA.test(item.sha ?? '')) return observe('SOURCE_INVALID');
      if (item.size > maxBytes || item.content.length > Math.ceil(maxBytes / 3) * 4 + 10000)
        return observe('SOURCE_TOO_LARGE');
      const encoded = item.content.replace(/\s/g, '');
      if (encoded.length % 4 !== 0 || !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(encoded))
        return observe('SOURCE_INVALID');
      const buffer = Buffer.from(encoded, 'base64');
      if (buffer.length !== item.size || buffer.length > maxBytes) return observe('SOURCE_INVALID');
      const blob = createHash('sha1').update(`blob ${buffer.length}\0`).update(buffer).digest('hex');
      if (blob !== item.sha.toLowerCase()) return observe('SOURCE_INVALID');
      const contentRef = 'sha256:' + createHash('sha256').update(buffer).digest('hex');
      return observe('OK', {revision: pinned.revision.toLowerCase(), contentRef,
        bytes: buffer.length, content: {encoding: 'base64', data: buffer.toString('base64')},
        gitBlobSha: blob});
    }
  };
  return Object.freeze(port);
}

// Atlas is still the owner of the original public repository observer.
export function createAtlasGitHubSourcePort({observePublicRepo, ...options} = {}) {
  return createGitHubSourcePort({...options, access: 'public', observeRepository: observePublicRepo});
}
