// U03: observations and compatibility only. No grants, registry or executor.
export const MODULE_SCHEMA = 'IWXA_MODULE_DESCRIPTOR/1.0.0';
export const STATUS_SCHEMA = 'IWXA_CAPABILITY_STATUS/1.0.0';
const idPattern = /^[a-z][a-z0-9]*(?:[._-][a-z0-9]+)*$/;
const versionPattern = /^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$/;
const clausePattern = /^(>=|<=|=|>|<)?(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$/;
const isObject = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const hasKeys = (value, required, permitted) => isObject(value) &&
  required.every(key => Object.hasOwn(value, key)) &&
  Object.keys(value).every(key => permitted.includes(key));
const isId = value => typeof value === 'string' && value.length <= 128 && idPattern.test(value);
const isVersion = value => typeof value === 'string' && value.length <= 120 && versionPattern.test(value);
const isRange = value => typeof value === 'string' && value.length > 0 && value.length <= 240 &&
  value.split(' ').length <= 4 && value.split(' ').every(part => clausePattern.test(part));
const distinct = records => new Set(records.map(entry => entry.id ?? entry.hostId)).size === records.length;
const copy = value => JSON.parse(JSON.stringify(value));

export function validateDescriptor(value) {
  if (!hasKeys(value, ['schema', 'id', 'version', 'provides', 'requires', 'hostCompatibility'],
    ['schema', 'id', 'version', 'provides', 'requires', 'hostCompatibility'])) return false;
  if (value.schema !== MODULE_SCHEMA || !isId(value.id) || !isVersion(value.version)) return false;
  if (!Array.isArray(value.provides) || value.provides.length > 128 ||
      !value.provides.every(item => hasKeys(item, ['id', 'version'], ['id', 'version']) &&
        isId(item.id) && isVersion(item.version)) || !distinct(value.provides)) return false;
  if (!Array.isArray(value.requires) || value.requires.length > 128 ||
      !value.requires.every(item => hasKeys(item, ['id', 'range'], ['id', 'range']) &&
        isId(item.id) && isRange(item.range)) || !distinct(value.requires)) return false;
  if (!Array.isArray(value.hostCompatibility) || value.hostCompatibility.length === 0 ||
      value.hostCompatibility.length > 32 ||
      !value.hostCompatibility.every(item => hasKeys(item, ['hostId', 'range'], ['hostId', 'range']) &&
        isId(item.hostId) && isRange(item.range)) || !distinct(value.hostCompatibility)) return false;
  return true;
}

export function validateStatus(value) {
  return hasKeys(value, ['schema', 'id', 'available'],
    ['schema', 'id', 'available', 'version', 'reason', 'observationRef']) &&
    value.schema === STATUS_SCHEMA && isId(value.id) && typeof value.available === 'boolean' &&
    (!Object.hasOwn(value, 'version') || isVersion(value.version)) &&
    (!Object.hasOwn(value, 'reason') || (typeof value.reason === 'string' && value.reason.length <= 256)) &&
    (!Object.hasOwn(value, 'observationRef') || (typeof value.observationRef === 'string' &&
      value.observationRef.trim().length > 0 && value.observationRef.length <= 512));
}

function compareVersion(actual, target) {
  const a = actual.split('.').map(BigInt);
  const b = target.split('.').map(BigInt);
  for (let i = 0; i < 3; i += 1) {
    if (a[i] < b[i]) return -1;
    if (a[i] > b[i]) return 1;
  }
  return 0;
}

export function satisfies(actual, range) {
  if (!isVersion(actual) || !isRange(range)) return false;
  return range.split(' ').every(part => {
    const match = part.match(clausePattern);
    const comparison = compareVersion(actual, `${match[2]}.${match[3]}.${match[4]}`);
    switch (match[1] ?? '=') {
      case '=': return comparison === 0;
      case '>': return comparison > 0;
      case '>=': return comparison >= 0;
      case '<': return comparison < 0;
      case '<=': return comparison <= 0;
      default: return false;
    }
  });
}

export function assess(descriptor, host, observations) {
  const reasons = [];
  if (descriptor?.schema !== MODULE_SCHEMA) reasons.push('U03_SCHEMA_UNSUPPORTED');
  else if (!validateDescriptor(descriptor)) reasons.push('U03_DESCRIPTOR_INVALID');
  if (reasons.length) return { compatible: false, reasons };
  if (!hasKeys(host, ['hostId', 'version'], ['hostId', 'version']) ||
      !isId(host.hostId) || !isVersion(host.version)) {
    return { compatible: false, reasons: ['U03_HOST_INVALID'] };
  }
  const hostRule = descriptor.hostCompatibility.find(item => item.hostId === host.hostId);
  if (!hostRule) reasons.push('U03_HOST_UNSUPPORTED');
  else if (!satisfies(host.version, hostRule.range)) reasons.push('U03_HOST_VERSION_INCOMPATIBLE');
  if (!Array.isArray(observations) || !observations.every(validateStatus)) {
    reasons.push('U03_OBSERVATION_INVALID');
  } else if (!distinct(observations)) {
    reasons.push('U03_OBSERVATION_AMBIGUOUS');
  } else {
    const observed = new Map(observations.map(item => [item.id, item]));
    for (const required of descriptor.requires) {
      const item = observed.get(required.id);
      if (!item) reasons.push(`U03_CAPABILITY_MISSING:${required.id}`);
      else if (!item.available) reasons.push(`U03_CAPABILITY_UNAVAILABLE:${required.id}`);
      else if (!item.observationRef) reasons.push(`U03_CAPABILITY_UNVERIFIED:${required.id}`);
      else if (!item.version || !satisfies(item.version, required.range)) {
        reasons.push(`U03_CAPABILITY_VERSION_INCOMPATIBLE:${required.id}`);
      }
    }
  }
  return { compatible: reasons.length === 0, reasons };
}

// The host supplies lifecycle hooks and retains authorization/connection/invocation authority.
// A successful assessment never implies a grant or that a program was connected.
export function createModuleAdapter(descriptor, hooks) {
  if (!validateDescriptor(descriptor)) throw new TypeError('U03_DESCRIPTOR_INVALID');
  if (!isObject(hooks) || typeof hooks.attach !== 'function' ||
      typeof hooks.detach !== 'function') throw new TypeError('U03_HOST_HOOKS_REQUIRED');
  const snapshot = copy(descriptor);
  return Object.freeze({
    describe: () => copy(snapshot),
    attach: async (host, observations) => {
      const assessment = assess(snapshot, host, observations);
      if (!assessment.compatible) return { attached: false, assessment, hostReceipt: null };
      const hostReceipt = await hooks.attach(copy(snapshot), copy(host), copy(observations));
      return { attached: hostReceipt?.attached === true, assessment, hostReceipt: hostReceipt ?? null };
    },
    detach: async binding => hooks.detach(binding),
  });
}
