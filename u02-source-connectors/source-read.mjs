import {SOURCE_SCHEMA, OBSERVATION_AUTHORITY, pinSource, requirePinnedSource} from './port.mjs';

// Shared validation for both Node hosts and embedded WebView consumers.
// Web Crypto is available in Node 22+ and modern secure-context WebViews.
async function digest(bytes) {
  const subtle = globalThis.crypto?.subtle;
  if (!subtle || typeof subtle.digest !== 'function') throw Error('SOURCE_CRYPTO_UNAVAILABLE');
  const hash = new Uint8Array(await subtle.digest('SHA-256', bytes));
  return 'sha256:' + Array.from(hash, b => b.toString(16).padStart(2, '0')).join('');
}
const matches = (descriptor, observation) =>
  observation?.schema === SOURCE_SCHEMA &&
  observation.providerId === descriptor.providerId &&
  observation.sourceRef === descriptor.sourceRef &&
  observation.observationType === descriptor.observationType &&
  observation.authority === OBSERVATION_AUTHORITY;

function requirePort(port) {
  if (typeof port?.describe !== 'function' ||
      typeof port?.refresh !== 'function' ||
      typeof port?.read !== 'function') throw Error('SOURCE_PORT_INVALID');
  const descriptor = port.describe();
  if (descriptor?.schema !== SOURCE_SCHEMA ||
      descriptor.authority !== OBSERVATION_AUTHORITY ||
      !Array.isArray(descriptor.capabilities)) throw Error('SOURCE_PORT_CONTRACT_INVALID');
  return descriptor;
}

function decodeBase64(base64) {
  if (typeof globalThis.atob !== 'function') throw Error('SOURCE_BASE64_UNAVAILABLE');
  let binary;
  try {binary = globalThis.atob(base64);}
  catch {throw Error('SOURCE_READ_INTEGRITY_INVALID');}
  const bytes = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
  return bytes;
}

// No refresh: preserve the exact source revision selected by the host.
export async function readPinnedSource(port, pinned) {
  const descriptor = requirePort(port);
  if (!requirePinnedSource(descriptor, pinned))
    throw Error('SOURCE_PIN_INVALID');
  const result = await port.read(pinned);
  if (!matches(descriptor, result) || typeof result.status !== 'string')
    throw Error('SOURCE_PORT_CONTRACT_INVALID');
  if (result.status !== 'OK') return {...result, content: null};
  if (result.revision !== pinned.revision ||
      !Number.isSafeInteger(result.bytes) || result.bytes < 0 ||
      typeof result.contentRef !== 'string' ||
      !/^sha256:[a-f0-9]{64}$/i.test(result.contentRef) ||
      result.content?.encoding !== 'base64' ||
      typeof result.content.data !== 'string')
    throw Error('SOURCE_READ_INTEGRITY_INVALID');
  const base64 = result.content.data;
  if (result.bytes > 64 * 1024 * 1024 ||
      base64.length > Math.ceil((64 * 1024 * 1024) / 3) * 4 + 4 ||
      base64.length % 4 !== 0 ||
      !/^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(base64))
    throw Error('SOURCE_READ_INTEGRITY_INVALID');
  const bytes = decodeBase64(base64);
  if (bytes.length !== result.bytes || await digest(bytes) !== result.contentRef.toLowerCase())
    throw Error('SOURCE_READ_INTEGRITY_INVALID');
  return result;
}

export async function captureSource(port) {
  const descriptor = requirePort(port);
  const observation = await port.refresh();
  if (!matches(descriptor, observation) || typeof observation.status !== 'string')
    throw Error('SOURCE_PORT_CONTRACT_INVALID');
  if (observation.status !== 'OK')
    return {status: observation.status, descriptor, observation, pin: null, read: null};
  const readable = descriptor.capabilities.some(x => x.id === 'source.read' && x.available === true);
  if (!readable)
    return {status: 'OK', descriptor, observation, pin: null, read: null};
  const pin = pinSource(descriptor, observation);
  const read = await readPinnedSource(port, pin);
  return {status: read.status, descriptor, observation, pin, read};
}
