// Browser/WebView entry: only the portable source contract and consumer boundary.
// Native filesystem, CLI, GitHub and HTTP execution remain host-owned adapters.
export {
  SOURCE_SCHEMA, OBSERVATION_AUTHORITY, sourceDescriptor, sourceObservation,
  pinSource, requirePinnedSource, sourceChange
} from './port.mjs';
export {captureSource, readPinnedSource} from './source-read.mjs';
export {createHostSelectedSourcePort} from './host-source.mjs';
export {TASK_SOURCE_SCHEMA,bindSourceObject,linkTaskSource,assessTaskSourceFreshness} from './task-object.mjs';
