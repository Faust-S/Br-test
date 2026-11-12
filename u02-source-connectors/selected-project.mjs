import {captureSource, readPinnedSource} from './source-read.mjs';
import {OBSERVATION_AUTHORITY, SOURCE_SCHEMA, requirePinnedSource} from './port.mjs';

// Stateless project read model. The host owns the selection, credentials,
// persistence and Task/Result. No discovery, network retry, write or apply.
export const PROJECT_VIEW_SCHEMA = 'IWXA_U02_SELECTED_PROJECT_VIEW/1';
export const PROJECT_CHECKPOINT_SCHEMA = 'IWXA_U02_SELECTED_PROJECT_CHECKPOINT/1';
const FORMATS = new Set(['text', 'markdown', 'json', 'csv']);
const id = x => typeof x === 'string' && /^[A-Za-z0-9][A-Za-z0-9._-]{0,99}$/.test(x);
const digest = x => typeof x === 'string' && /^sha256:[a-f0-9]{64}$/i.test(x);
const validDescriptor = d => d?.schema === SOURCE_SCHEMA && d.authority === OBSERVATION_AUTHORITY
  && typeof d.providerId === 'string' && typeof d.sourceRef === 'string'
  && Array.isArray(d.capabilities);
const hasRead = d => d.capabilities.some(c => c.id === 'source.read' && c.available === true);
const limit = 16 * 1024 * 1024;
const elapsed = begin => Math.round((performance.now() - begin) * 1000) / 1000;

function checkInput({scopeId, sources, previous, maxTotalBytes}) {
  if (!id(scopeId) || !Array.isArray(sources) || sources.length > 24 ||
      !Number.isInteger(maxTotalBytes) || maxTotalBytes < 1 || maxTotalBytes > limit)
    throw Error('PROJECT_SELECTION_INVALID');
  const seen = new Set();
  for (const s of sources) {
    if (!s || !id(s.key) || seen.has(s.key) ||
        typeof s.port?.describe !== 'function' ||
        typeof s.port?.refresh !== 'function' || typeof s.port?.read !== 'function')
      throw Error('PROJECT_SELECTION_INVALID');
    seen.add(s.key);
  }
  if (previous !== null && (previous?.schema !== PROJECT_CHECKPOINT_SCHEMA ||
      previous.scopeId !== scopeId || !Array.isArray(previous.sources) ||
      previous.sources.length > 24 || previous.sources.some(s => !id(s?.key) ||
      !validDescriptor(s.pin) || !requirePinnedSource(s.pin, s.pin) ||
      !digest(s.contentRef) || !Number.isSafeInteger(s.bytes) || s.bytes < 0) ||
      new Set(previous.sources.map(s => s.key)).size !== previous.sources.length))
    throw Error('PROJECT_CHECKPOINT_INVALID');
}

// Explicit source selection only: never scans a user's home directory, git
// working tree, browser tabs or credential providers.
export async function readSelectedProjectSources({scopeId, sources, previous = null,
  maxTotalBytes = limit} = {}) {
  checkInput({scopeId, sources, previous, maxTotalBytes});
  const prior = new Map((previous?.sources ?? []).map(s => [s.key, s]));
  const rows = [], metrics = {refreshCalls: 0, pinnedReadCalls: 0,
    validSourceBytes: 0, returnedTextBytes: 0, elapsedMs: 0};
  const begin = performance.now();
  for (const selected of sources) {
    const start = performance.now();
    const {key, port, format} = selected;
    const d = port.describe();
    if (!validDescriptor(d)) throw Error('PROJECT_SOURCE_DESCRIPTOR_INVALID');
    const previousGood = prior.get(key) ?? null;
    const base = {key, format, providerId: d.providerId, sourceRef: d.sourceRef,
      authority: OBSERVATION_AUTHORITY};
    const row = (status, change, rest = {}) => ({...base, status, change,
      lastKnown: previousGood, ...rest, elapsedMs: elapsed(start)});
    if (!FORMATS.has(format)) {
      rows.push(row('FORMAT_UNSUPPORTED', 'UNKNOWN'));
      continue;
    }
    // A different root/host credential/source is NOT a new revision of the old
    // object. It must be selected again by the host before bytes are opened.
    if (previousGood && (previousGood.pin.providerId !== d.providerId ||
        previousGood.pin.sourceRef !== d.sourceRef ||
        previousGood.pin.observationType !== d.observationType)) {
      rows.push(row('SOURCE_REBIND_REQUIRED', 'REBIND_REQUIRED'));
      continue;
    }
    if (!hasRead(d)) {
      rows.push(row('READ_UNSUPPORTED', 'UNKNOWN'));
      continue;
    }
    if (metrics.validSourceBytes >= maxTotalBytes) {
      rows.push(row('PROJECT_BYTE_LIMIT', 'UNKNOWN'));
      continue;
    }
    let capture;
    metrics.refreshCalls++;
    try {
      capture = await captureSource(port);
    } catch {
      rows.push(row('SOURCE_UNAVAILABLE', 'UNKNOWN'));
      continue;
    }
    if (capture.observation?.status === 'OK') metrics.pinnedReadCalls++;
    if (capture.status !== 'OK' || capture.read?.status !== 'OK') {
      rows.push(row(capture.status, capture.status === 'SOURCE_MISSING' ? 'MISSING'
        : capture.status === 'REVISION_MISMATCH' ? 'CHANGED_DURING_READ' : 'UNKNOWN'));
      continue;
    }
    const r = capture.read;
    if (r.bytes > maxTotalBytes - metrics.validSourceBytes) {
      rows.push(row('PROJECT_BYTE_LIMIT', 'UNKNOWN'));
      continue;
    }
    metrics.validSourceBytes += r.bytes;
    let text;
    try {
      const binary = atob(r.content.data);
      const bytes = Uint8Array.from(binary, x => x.charCodeAt(0));
      text = new TextDecoder('utf-8', {fatal: true}).decode(bytes);
      if (text.includes(String.fromCharCode(0))) throw Error('FORMAT_INVALID');
      if (format === 'json') JSON.parse(text);
    } catch {
      rows.push(row('FORMAT_INVALID', 'UNKNOWN'));
      continue;
    }
    const lastKnown = {key, pin: capture.pin, contentRef: r.contentRef,
      bytes: r.bytes};
    let change = 'FIRST_OBSERVATION';
    if (previousGood) {
      if (previousGood.pin.revision === capture.pin.revision &&
          previousGood.contentRef === r.contentRef) change = 'UNCHANGED';
      else if (previousGood.contentRef === r.contentRef)
        change = 'REVISION_CHANGED_CONTENT_SAME';
      else change = 'CHANGED';
    }
    metrics.returnedTextBytes += r.bytes;
    rows.push(row('OK', change, {revision: r.revision,
      contentRef: r.contentRef, bytes: r.bytes, observedAt: r.observedAt,
      provenance: r.provenance, pin: capture.pin, text, lastKnown}));
  }
  // A source not selected by the host is not silently reintroduced from
  // previous checkpoints. It is simply absent from this read.
  metrics.elapsedMs = elapsed(begin);
  return {schema: PROJECT_VIEW_SCHEMA, scopeId, sources: rows, metrics,
    complete: rows.every(r => r.status === 'OK')};
}

export function checkpointSelectedProjectSources(view) {
  if (view?.schema !== PROJECT_VIEW_SCHEMA || !id(view.scopeId) ||
      !Array.isArray(view.sources)) throw Error('PROJECT_VIEW_INVALID');
  return {schema: PROJECT_CHECKPOINT_SCHEMA, scopeId: view.scopeId,
    sources: view.sources.filter(s => s.lastKnown !== null).map(s => {
      const {key, pin, contentRef, bytes} = s.lastKnown;
      return {key, pin, contentRef, bytes};
    })};
}

// K01/K05 host adaptation needs source identity, exact revision and content
// hash, not a second source store. Unavailable and stale rows have no evidence.
export function projectSourceObservationsForKnowledge(view) {
  if (view?.schema !== PROJECT_VIEW_SCHEMA || !Array.isArray(view.sources))
    throw Error('PROJECT_VIEW_INVALID');
  return view.sources.filter(s => s.status === 'OK').map(s => ({
    key: s.key, sourceRef: s.sourceRef, providerId: s.providerId,
    revision: s.revision, contentRef: s.contentRef, bytes: s.bytes,
    observedAt: s.observedAt, provenance: s.provenance,
    epistemic: 'observed', authority: OBSERVATION_AUTHORITY,
    text: s.text, complete: true
  }));
}

// A low-cost status check for reopened Tasks. This observes source revisions,
// never calls source.read(), never supplies source text, and never turns a
// prior checkpoint into fresh evidence for K01/K05.
export const PROJECT_FRESHNESS_SCHEMA = 'IWXA_U02_SELECTED_PROJECT_FRESHNESS/1';
export async function inspectSelectedProjectSources({scopeId, sources, previous = null} = {}) {
  checkInput({scopeId, sources, previous, maxTotalBytes: limit});
  const prior = new Map((previous?.sources ?? []).map(s => [s.key, s]));
  const metrics = {refreshCalls: 0, pinnedReadCalls: 0, returnedTextBytes: 0, elapsedMs: 0};
  const rows = [];
  const begin = performance.now();
  for (const {key, format, port} of sources) {
    const start = performance.now();
    const descriptor = port.describe();
    if (!validDescriptor(descriptor)) throw Error('PROJECT_SOURCE_DESCRIPTOR_INVALID');
    const saved = prior.get(key) ?? null;
    const base = {key, format, providerId: descriptor.providerId,
      sourceRef: descriptor.sourceRef, authority: OBSERVATION_AUTHORITY};
    const row = (status, freshness, change, more = {}) => ({
      ...base, status, freshness, change, ...more, elapsedMs: elapsed(start)});
    if (!FORMATS.has(format)) {
      rows.push(row('FORMAT_UNSUPPORTED', 'UNKNOWN', 'UNKNOWN'));
      continue;
    }
    if (saved && (saved.pin.providerId !== descriptor.providerId ||
        saved.pin.sourceRef !== descriptor.sourceRef ||
        saved.pin.observationType !== descriptor.observationType)) {
      rows.push(row('SOURCE_REBIND_REQUIRED', 'UNKNOWN', 'REBIND_REQUIRED'));
      continue;
    }
    if (!descriptor.capabilities.some(x => x.id === 'source.refresh' && x.available === true)) {
      rows.push(row('REFRESH_UNSUPPORTED', 'UNKNOWN', 'UNKNOWN'));
      continue;
    }
    let observed;
    metrics.refreshCalls++;
    try {observed = await port.refresh();}
    catch {
      rows.push(row('SOURCE_UNAVAILABLE', 'UNKNOWN', 'UNKNOWN'));
      continue;
    }
    if (observed?.schema !== SOURCE_SCHEMA ||
        observed.authority !== OBSERVATION_AUTHORITY ||
        observed.providerId !== descriptor.providerId ||
        observed.sourceRef !== descriptor.sourceRef ||
        observed.observationType !== descriptor.observationType) {
      rows.push(row('SOURCE_INVALID', 'UNKNOWN', 'UNKNOWN'));
      continue;
    }
    if (observed.status !== 'OK') {
      rows.push(row(observed.status, 'UNKNOWN',
        observed.status === 'SOURCE_MISSING' ? 'MISSING' : 'UNKNOWN'));
      continue;
    }
    const current = observed.revision;
    if (typeof current !== 'string' || !/^(?:[a-f0-9]{40,64}|sha256:[a-f0-9]{64})$/i.test(current)) {
      rows.push(row('REVISION_UNAVAILABLE', 'UNKNOWN', 'UNKNOWN'));
      continue;
    }
    // A content hash supplied by the provider cannot contradict a saved
    // hash for an unchanged revision. This does not reread or validate bytes.
    if (saved && current === saved.pin.revision &&
        observed.contentRef != null && observed.contentRef !== saved.contentRef) {
      rows.push(row('SOURCE_INCONSISTENT', 'UNKNOWN', 'UNKNOWN'));
      continue;
    }
    const same = saved && current === saved.pin.revision;
    rows.push(row('OK', saved ? (same ? 'CURRENT' : 'STALE') : 'NOT_PINNED',
      saved ? (same ? 'UNCHANGED' : 'CHANGED') : 'FIRST_OBSERVATION',
      {currentRevision: current, previousRevision: saved?.pin.revision ?? null,
        observedAt: observed.observedAt, provenance: observed.provenance}));
  }
  metrics.elapsedMs = elapsed(begin);
  return {schema: PROJECT_FRESHNESS_SCHEMA, scopeId, sources: rows, metrics,
    observationsComplete: rows.every(r => r.status === 'OK'),
    contentRead: false, evidenceReady: false};
}
