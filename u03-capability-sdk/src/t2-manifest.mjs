// U03 read-only T2 Workspace module contract inspection. NOT T2 admission/activation.
export const T2_PROJECTION_SCHEMA = 'IWXA_U03_T2_CONTRACT_PROJECTION/1.0.0';
const groups = Object.freeze(['routes', 'navigation', 'editors', 'viewers', 'commands',
  'panels', 'dashboard_cards', 'validators', 'migrations', 'legacy_importers']);
const exact = (value, keys) => value !== null && typeof value === 'object' &&
  !Array.isArray(value) && Object.keys(value).length === keys.length &&
  keys.every(key => Object.hasOwn(value, key));
const t2Id = /^[a-z0-9][a-z0-9._-]*(?![\s\S])/;
const nonemptyString = value => typeof value === 'string' && value.length > 0;

export function isT2ManifestShape(value) {
  if (!exact(value, ['module_manifest_version', 'module_id', 'module_version', 'title',
    'compatibility', 'contributions', 'security']) ||
      value.module_manifest_version !== '1.0.0' ||
      typeof value.module_id !== 'string' || !t2Id.test(value.module_id) ||
      typeof value.module_version !== 'string' || typeof value.title !== 'string') return false;
  const c = value.compatibility;
  if (!exact(c, ['workspace_contract', 'runtime_contract']) ||
      typeof c.workspace_contract !== 'string' || c.runtime_contract !== '1.3.0') return false;
  if (!exact(value.contributions, groups) ||
      !groups.every(key => Array.isArray(value.contributions[key]))) return false;
  const s = value.security;
  return exact(s, ['direct_indexeddb', 'remote_code', 'iwxa_executable_payload']) &&
    s.direct_indexeddb === false && s.remote_code === false &&
    s.iwxa_executable_payload === false;
}

export function inspectT2Contract(manifest, targetContracts) {
  const reasons = [];
  if (!isT2ManifestShape(manifest)) reasons.push('U03_T2_MANIFEST_SHAPE_INVALID');
  if (!exact(targetContracts, ['workspaceContract', 'runtimeContract']) ||
      !nonemptyString(targetContracts.workspaceContract) ||
      !nonemptyString(targetContracts.runtimeContract)) reasons.push('U03_T2_TARGET_INVALID');
  if (reasons.length) return {
    schema: T2_PROJECTION_SCHEMA, declaredCompatible: false,
    admission: 'NOT_ASSESSED', reasons, module: null,
  };
  if (manifest.compatibility.workspace_contract !== targetContracts.workspaceContract)
    reasons.push('U03_T2_WORKSPACE_CONTRACT_MISMATCH');
  if (manifest.compatibility.runtime_contract !== targetContracts.runtimeContract)
    reasons.push('U03_T2_RUNTIME_CONTRACT_MISMATCH');
  return {
    schema: T2_PROJECTION_SCHEMA,
    declaredCompatible: reasons.length === 0,
    admission: 'NOT_ASSESSED',
    reasons,
    module: {
      id: manifest.module_id,
      version: manifest.module_version,
      declaredContributionCounts: Object.fromEntries(groups.map(k => [k, manifest.contributions[k].length])),
    },
  };
}

export function isExpectedT2Schema(schema) {
  if (!schema || !exact(schema.properties ?? {}, ['module_manifest_version', 'module_id',
      'module_version', 'title', 'compatibility', 'contributions', 'security'])) return false;
  const p = schema.properties;
  return schema.$id === 'https://iwxa.dev/schemas/workspace-module-manifest/1.0.0' &&
    schema.additionalProperties === false &&
    p.module_manifest_version?.const === '1.0.0' &&
    p.compatibility?.additionalProperties === false &&
    p.compatibility?.properties?.runtime_contract?.const === '1.3.0' &&
    p.contributions?.additionalProperties === false &&
    p.security?.additionalProperties === false &&
    p.security?.properties?.direct_indexeddb?.const === false &&
    p.security?.properties?.remote_code?.const === false &&
    p.security?.properties?.iwxa_executable_payload?.const === false;
}
