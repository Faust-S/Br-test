import {createHash} from 'node:crypto';
import {sourceDescriptor, sourceObservation, requirePinnedSource} from './port.mjs';

const U01_SCHEMA = 'IWXA_PROGRAM_CONNECTION/1.0.0';
const DEFAULT_MAX_BYTES = 64 * 1024;
const digest = bytes => 'sha256:' + createHash('sha256').update(bytes).digest('hex');

// U02 never starts a process or selects an executable. The host authorizes and
// supplies an idempotent, read-only U01 operation via readOutput.
export function createCliOutputSourcePort({programId, sourceId, operationId, contextId,
  readOutput, maxBytes = DEFAULT_MAX_BYTES,
  now = () => new Date().toISOString()} = {}) {
  if (typeof programId !== 'string' || !/^[A-Z][A-Z0-9_]{2,79}$/.test(programId) ||
      typeof sourceId !== 'string' || !/^[a-z][a-z0-9._-]{1,79}$/.test(sourceId) ||
      typeof operationId !== 'string' || !/^[a-z][a-z0-9._-]{1,79}$/.test(operationId) ||
      typeof contextId !== 'string' || !/^[a-z][a-z0-9_-]{1,63}$/.test(contextId) ||
      typeof readOutput !== 'function' || typeof now !== 'function' ||
      !Number.isInteger(maxBytes) || maxBytes < 1 || maxBytes > 1024 * 1024)
    throw Error('CLI_SOURCE_CONFIG_INVALID');

  const descriptor = sourceDescriptor({providerId: 'cli.u01',
    sourceRef: `cli://${programId}/${sourceId}/${operationId}/${contextId}`});
  const provenance = {provider: 'cli', programId, sourceId, operationId,
    contextId, access: 'host-authorized-read',
    authority: 'U01 read-only operation result'};
  const observe = (status, details = {}) => sourceObservation(descriptor,
    {status, observedAt: now(), provenance, ...details});

  async function capture() {
    let result;
    try {
      result = await readOutput(Object.freeze({programId, sourceId, operationId,
        contextId, maxBytes}));
    } catch {
      return {status: 'SOURCE_UNAVAILABLE'};
    }
    if (!result || result.schema !== U01_SCHEMA || result.program_id !== programId ||
        typeof result.status !== 'string') return {status: 'SOURCE_INVALID'};
    if (result.status === 'NOT_CONNECTED' || result.status === 'CONNECTION_LOST')
      return {status: 'CONNECTION_REQUIRED'};
    if (result.status === 'AUTHORIZATION_REQUIRED') return {status: 'AUTHORIZATION_REQUIRED'};
    if (result.status === 'ACCESS_DENIED') return {status: 'ACCESS_DENIED'};
    if (result.status === 'OPERATION_UNSUPPORTED') return {status: 'READ_UNSUPPORTED'};
    if (result.status !== 'EXECUTED') return {status: 'SOURCE_UNAVAILABLE'};
    if (result.executed !== true || result.operation !== operationId ||
        typeof result.output !== 'string')
      return {status: 'SOURCE_INVALID'};
    if (result.output.length > maxBytes) return {status: 'SOURCE_TOO_LARGE'};
    const buffer = Buffer.from(result.output, 'utf8');
    if (buffer.length > maxBytes) return {status: 'SOURCE_TOO_LARGE'};
    return {status: 'OK', buffer, revision: digest(buffer)};
  }
  return Object.freeze({
    describe: () => descriptor,
    refresh: async () => {
      const result = await capture();
      return observe(result.status, result.status === 'OK'
        ? {revision: result.revision, contentRef: result.revision,
          bytes: result.buffer.length} : {});
    },
    read: async pinned => {
      if (!requirePinnedSource(descriptor, pinned)) return observe('REVISION_REQUIRED');
      const result = await capture();
      if (result.status !== 'OK') return observe(result.status);
      if (result.revision !== pinned.revision)
        return observe('REVISION_MISMATCH',
          {revision: result.revision, contentRef: result.revision,
            bytes: result.buffer.length});
      return observe('OK', {revision: result.revision, contentRef: result.revision,
        bytes: result.buffer.length,
        content: {encoding: 'base64', data: result.buffer.toString('base64')}});
    }
  });
}
