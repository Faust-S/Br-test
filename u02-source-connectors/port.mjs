export const SOURCE_SCHEMA = 'IWXA_SOURCE_CONNECTOR/1.0.0';
export const OBSERVATION_AUTHORITY = 'OBSERVATION_ONLY';

const sourceCapabilities = Object.freeze([
  Object.freeze({id: 'source.describe', available: true}),
  Object.freeze({id: 'source.refresh', available: true}),
  Object.freeze({id: 'source.read', available: true})
]);
const validRevision = value => typeof value === 'string' &&
  (/^[0-9a-f]{40,64}$/i.test(value) || /^sha256:[0-9a-f]{64}$/i.test(value));

export function sourceDescriptor({providerId, sourceRef, revision = null, observationType = 'file',
  capabilities = sourceCapabilities}) {
  if (typeof providerId !== 'string' || !/^[a-z][a-z0-9._-]{1,79}$/.test(providerId) ||
      typeof sourceRef !== 'string' || !sourceRef || sourceRef.length > 2048 ||
      /[\x00-\x1f\x7f]/.test(sourceRef) ||
      !['file', 'repository', 'program'].includes(observationType) ||
      (revision !== null && !validRevision(revision)) ||
      !Array.isArray(capabilities) || capabilities.length > 128 ||
      capabilities.some(c => !c || typeof c.id !== 'string' ||
        !/^[a-z][a-z0-9._-]{1,79}$/.test(c.id) ||
        typeof c.available !== 'boolean')) throw Error('SOURCE_DESCRIPTOR_INVALID');
  return Object.freeze({schema: SOURCE_SCHEMA, providerId, sourceRef, revision,
    observationType, capabilities: capabilities.map(c => ({...c})), authority: OBSERVATION_AUTHORITY});
}

export function sourceObservation(descriptor, {status, revision = null, observedAt, provenance,
  contentRef = null, bytes = null, content = null, capabilities = descriptor.capabilities,
  ...detail}) {
  if (!descriptor || descriptor.schema !== SOURCE_SCHEMA ||
      typeof status !== 'string' || !/^[A-Z_]+$/.test(status) ||
      (revision !== null && !validRevision(revision)) ||
      typeof observedAt !== 'string' || !Number.isFinite(Date.parse(observedAt)) ||
      !provenance || typeof provenance !== 'object') throw Error('SOURCE_OBSERVATION_INVALID');
  // Provider-specific metadata must never override source identity or authority.
  return {...detail, schema: SOURCE_SCHEMA, providerId: descriptor.providerId,
    sourceRef: descriptor.sourceRef, observationType: descriptor.observationType,
    revision, observedAt, provenance, status, authority: OBSERVATION_AUTHORITY,
    contentRef, bytes, content, capabilities};
}

export function pinSource(descriptor, observation) {
  if (!descriptor || descriptor.schema !== SOURCE_SCHEMA ||
      !observation || observation.schema !== SOURCE_SCHEMA || observation.status !== 'OK' ||
      descriptor.authority !== OBSERVATION_AUTHORITY || observation.authority !== OBSERVATION_AUTHORITY ||
      descriptor.providerId !== observation.providerId || descriptor.sourceRef !== observation.sourceRef ||
      descriptor.observationType !== observation.observationType ||
      !validRevision(observation.revision)) throw Error('SOURCE_PIN_UNAVAILABLE');
  return sourceDescriptor({...descriptor, revision: observation.revision});
}

export function requirePinnedSource(expected, pinned) {
  if (!pinned || pinned.schema !== SOURCE_SCHEMA ||
      pinned.providerId !== expected.providerId || pinned.sourceRef !== expected.sourceRef ||
      pinned.observationType !== expected.observationType ||
      pinned.authority !== OBSERVATION_AUTHORITY ||
      !validRevision(pinned.revision)) return false;
  return true;
}

export function sourceChange(previous, next) {
  if (!previous || !next || previous.schema !== SOURCE_SCHEMA || next.schema !== SOURCE_SCHEMA ||
      previous.status !== 'OK' || next.status !== 'OK' ||
      previous.providerId !== next.providerId || previous.sourceRef !== next.sourceRef ||
      previous.observationType !== next.observationType)
    return {status: 'NOT_COMPARABLE'};
  const byRevision = validRevision(previous.revision) && validRevision(next.revision);
  // Program capability changes can be compared even when the vendor version is unknown.
  // This content fingerprint is not a program binary version or authorization.
  const byCapabilities = previous.observationType === 'program' &&
    previous.revision === null && next.revision === null &&
    validRevision(previous.contentRef) && validRevision(next.contentRef);
  if (!byRevision && !byCapabilities) return {status: 'NOT_COMPARABLE'};
  const older = byRevision ? previous.revision : previous.contentRef;
  const newer = byRevision ? next.revision : next.contentRef;
  return {status: older === newer ? 'UNCHANGED' : 'CHANGED',
    basis: byRevision ? 'SOURCE_REVISION' : 'CAPABILITY_OBSERVATION',
    previousRevision: previous.revision, currentRevision: next.revision,
    ...(byCapabilities ? {previousContentRef: older, currentContentRef: newer} : {})};
}
