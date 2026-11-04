import {sourceDescriptor, sourceObservation} from './port.mjs';

// Observes an existing U01 connection; never calls connect, invoke, install or apply.
export function createProgramCapabilityView({programId, connection,
  now = () => new Date().toISOString()} = {}) {
  if (typeof programId !== 'string' || !/^[A-Z][A-Z0-9_]{2,79}$/.test(programId) ||
      !connection || typeof connection.inspect !== 'function' ||
      typeof connection.capabilities !== 'function') throw Error('PROGRAM_SOURCE_CONFIG_INVALID');
  const descriptor = sourceDescriptor({providerId: 'program.u01',
    sourceRef: `program://${programId}`, observationType: 'program',
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
            !Array.isArray(caps.operations)) return observe('SOURCE_INVALID');
        return observe('OK', {programStatus: info.status, transport: info.transport,
          connected: caps.connected === true,
          programCapabilities: caps.operations.map(c => ({name: c.name,
            declared: c.declared === true, verified: c.verified === true}))});
      } catch {return observe('SOURCE_UNAVAILABLE');}
    },
    read: async () => observe('READ_UNSUPPORTED')
  });
}
