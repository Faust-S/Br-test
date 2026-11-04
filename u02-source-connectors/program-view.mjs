import {createHash} from 'node:crypto';
import {sourceDescriptor, sourceObservation} from './port.mjs';

// Observes existing U01 connections without initiating connection or execution.
// A capability digest describes an observation, not an executable version or grant.
export function createProgramCapabilityView({programId, connection,
  now = () => new Date().toISOString()} = {}) {
  if (typeof programId !== 'string' || !/^[A-Z][A-Z0-9_]{2,79}$/.test(programId) ||
      !connection || typeof connection.inspect !== 'function' ||
      typeof connection.capabilities !== 'function') throw Error('PROGRAM_SOURCE_CONFIG_INVALID');
  const descriptor = sourceDescriptor({providerId: 'program.u01',
    sourceRef: 'program://' + programId, observationType: 'program',
    capabilities: [{id: 'source.describe', available: true},
      {id: 'source.refresh', available: true},
      {id: 'source.read', available: false}]});
  const observe = (status, extra = {}) => sourceObservation(descriptor, {status,
    observedAt: now(), provenance: {provider: 'program.u01', programId,
      access: 'host-owned', authority: 'U01 connection observation'}, ...extra});
  return Object.freeze({
    describe: () => descriptor,
    refresh: async () => {
      try {
        const info = await connection.inspect();
        const caps = await connection.capabilities();
        if (info?.schema !== 'IWXA_PROGRAM_CONNECTION/1.0.0' ||
            caps?.schema !== 'IWXA_PROGRAM_CONNECTION/1.0.0' ||
            info.program_id !== programId || caps.program_id !== programId ||
            typeof info.status !== 'string' ||
            typeof info.transport !== 'string' ||
            typeof caps.connected !== 'boolean' ||
            !Array.isArray(caps.operations) || caps.operations.length > 256)
          return observe('SOURCE_INVALID');
        const seen = new Set();
        const programCapabilities = [];
        for (const op of caps.operations) {
          if (typeof op?.name !== 'string' || !op.name || op.name.length > 128 ||
              /[\x00-\x1f\x7f]/.test(op.name) || seen.has(op.name) ||
              typeof op.declared !== 'boolean' || typeof op.verified !== 'boolean' ||
              (!caps.connected && op.verified)) return observe('SOURCE_INVALID');
          seen.add(op.name);
          programCapabilities.push({name: op.name, declared: op.declared, verified: op.verified});
        }
        programCapabilities.sort((a,b) => a.name.localeCompare(b.name, 'en'));
        const programVersion = typeof info.version === 'string' && info.version.trim()
          ? info.version.trim().slice(0,256)
          : typeof caps.version === 'string' && caps.version.trim()
            ? caps.version.trim().slice(0,256) : null;
        const data = {programId, programStatus: info.status, transport: info.transport,
          connected: caps.connected, programVersion, programCapabilities};
        const contentRef = 'sha256:' + createHash('sha256')
          .update(JSON.stringify(data)).digest('hex');
        return observe('OK', {...data, contentRef,
          versionStatus: programVersion ? 'HOST_REPORTED' : 'NOT_OBSERVED'});
      } catch {return observe('SOURCE_UNAVAILABLE');}
    },
    read: async () => observe('READ_UNSUPPORTED')
  });
}
