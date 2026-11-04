import {isAbsolute} from 'node:path';
import {sourceDescriptor, sourceObservation} from './port.mjs';

// Uses the existing U01 read-only local Git observer. No fetch, checkout or git writes.
export function createLocalGitSourcePort({root, rootId, observeLocalGit,
  now = () => new Date().toISOString()} = {}) {
  if (typeof root !== 'string' || !isAbsolute(root) || root.includes('\0') ||
      typeof rootId !== 'string' || !/^[a-z][a-z0-9_-]{0,63}$/.test(rootId) ||
      typeof observeLocalGit !== 'function' || typeof now !== 'function')
    throw Error('GIT_SOURCE_CONFIG_INVALID');
  const descriptor = sourceDescriptor({providerId: 'git.local',
    sourceRef: `git://${rootId}`, observationType: 'repository',
    capabilities: [{id: 'source.describe', available: true},
      {id: 'source.refresh', available: true},
      {id: 'source.read', available: false}]});
  const observe = (status, extra = {}) => sourceObservation(descriptor, {status,
    observedAt: now(), provenance: {provider: 'git', access: 'host-selected-root',
      authority: 'local-git-observation', rootId}, ...extra});
  return Object.freeze({
    describe: () => descriptor,
    refresh: async () => {
      let view;
      try {view = await observeLocalGit(root);}
      catch {return observe('SOURCE_UNAVAILABLE');}
      if (!view || view.status !== 'OK') return observe('SOURCE_UNAVAILABLE');
      if (!/^[a-f0-9]{40,64}$/i.test(view.headSha ?? '')) return observe('SOURCE_INVALID');
      // A commit SHA does not identify uncommitted changes in a worktree.
      if (view.dirty !== false) return observe('SOURCE_DIRTY', {revision: view.headSha});
      return observe('OK', {revision: view.headSha, detachedHead: view.detachedHead === true,
        branch: view.headRef ?? null, branchScope: 'WORKTREE_ONLY'});
    },
    read: async () => observe('READ_UNSUPPORTED')
  });
}
