import {inspectSelectedProjectSources, PROJECT_VIEW_SCHEMA, PROJECT_CHECKPOINT_SCHEMA} from './selected-project.mjs';
import {OBSERVATION_AUTHORITY, requirePinnedSource} from './port.mjs';

export const PROJECT_REVALIDATION_SCHEMA = 'IWXA_U02_SELECTED_PROJECT_REVALIDATION/1';
const good = x => typeof x === 'string' && /^sha256:[a-f0-9]{64}$/i.test(x);

// Re-check exact revisions at the end of a multi-source read. A document read
// first may change while a remote source is fetched last. No new content read
// happens here; callers choose when this stronger check is worth the cost.
export async function revalidateSelectedProjectRead({view, sources} = {}) {
  if (view?.schema !== PROJECT_VIEW_SCHEMA || typeof view.scopeId !== 'string' ||
      !Array.isArray(view.sources) || !Array.isArray(sources) ||
      view.sources.length !== sources.length ||
      view.sources.some((row, i) => row?.key !== sources[i]?.key))
    throw Error('PROJECT_REVALIDATION_INPUT_INVALID');
  const goodRows = view.sources.filter(row => row.status === 'OK');
  for (const row of goodRows) {
    if (!row.lastKnown || row.lastKnown.key !== row.key ||
        !row.pin || row.pin.revision !== row.revision ||
        row.pin.providerId !== row.providerId || row.pin.sourceRef !== row.sourceRef ||
        !requirePinnedSource(row.pin, row.lastKnown.pin) ||
        row.lastKnown.pin.revision !== row.revision ||
        row.lastKnown.contentRef !== row.contentRef || !good(row.contentRef))
      throw Error('PROJECT_REVALIDATION_PIN_INVALID');
  }
  const selectedByKey = new Map(sources.map(item => [item.key, item]));
  const inspected = await inspectSelectedProjectSources({
    scopeId: view.scopeId,
    sources: goodRows.map(row => selectedByKey.get(row.key)),
    previous: {schema: PROJECT_CHECKPOINT_SCHEMA, scopeId: view.scopeId,
      sources: goodRows.map(row => row.lastKnown)}
  });
  const checks = new Map(inspected.sources.map(row => [row.key, row]));
  const result = view.sources.map(row => {
    if (row.status !== 'OK') return {...row, finalRevisionConfirmed: false};
    const check = checks.get(row.key);
    if (check?.status === 'OK' && check.freshness === 'CURRENT')
      return {...row, finalRevisionConfirmed: true, revalidatedAt: check.observedAt};
    // Fail closed: never release bytes of a now-stale read as current Task
    // evidence. The original historical result remains with its owner.
    return {key: row.key, format: row.format, providerId: row.providerId,
      sourceRef: row.sourceRef, authority: OBSERVATION_AUTHORITY,
      status: check?.status === 'OK' ? 'STALE_AFTER_READ' :
        (check?.status ?? 'SOURCE_UNAVAILABLE'),
      change: check?.freshness === 'STALE' ? 'CHANGED_DURING_PROJECT' :
        (check?.change ?? 'UNKNOWN'),
      lastKnown: null, finalRevisionConfirmed: false,
      previousRevision: row.revision, currentRevision: check?.currentRevision ?? null,
      elapsedMs: row.elapsedMs};
  });
  const complete = result.every(row => row.status === 'OK');
  return {...view, sources: result, complete,
    metrics: {...view.metrics, returnedTextBytes: result.reduce((n, row) =>
      n + (row.status === 'OK' ? row.bytes : 0), 0)},
    revalidation: {schema: PROJECT_REVALIDATION_SCHEMA,
      refreshCalls: inspected.metrics.refreshCalls, pinnedReadCalls: 0,
      returnedTextBytes: 0, elapsedMs: inspected.metrics.elapsedMs,
      contentRead: false, allCurrent: complete}};
}
