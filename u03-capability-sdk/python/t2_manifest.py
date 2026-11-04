"""U03 read-only T2 Workspace manifest inspection. Never a Workspace preflight."""
from __future__ import annotations
import re

SCHEMA = 'IWXA_U03_T2_CONTRACT_PROJECTION/1.0.0'
GROUPS = ('routes', 'navigation', 'editors', 'viewers', 'commands', 'panels',
          'dashboard_cards', 'validators', 'migrations', 'legacy_importers')
ID = re.compile(r'[a-z0-9][a-z0-9._-]*\Z')


def _exact(obj, keys):
    return isinstance(obj, dict) and set(obj) == set(keys)


def is_t2_manifest_shape(obj):
    if not _exact(obj, ('module_manifest_version', 'module_id', 'module_version', 'title',
                        'compatibility', 'contributions', 'security')):
        return False
    if not (obj['module_manifest_version'] == '1.0.0' and
            isinstance(obj['module_id'], str) and ID.fullmatch(obj['module_id']) and
            isinstance(obj['module_version'], str) and isinstance(obj['title'], str)):
        return False
    c = obj['compatibility']
    if not (_exact(c, ('workspace_contract', 'runtime_contract')) and
            isinstance(c['workspace_contract'], str) and c['runtime_contract'] == '1.3.0'):
        return False
    if not _exact(obj['contributions'], GROUPS) or not all(
            isinstance(obj['contributions'][key], list) for key in GROUPS):
        return False
    s = obj['security']
    return _exact(s, ('direct_indexeddb', 'remote_code', 'iwxa_executable_payload')) and all(
        s[key] is False for key in ('direct_indexeddb', 'remote_code', 'iwxa_executable_payload'))


def inspect_t2_contract(manifest, target_contracts):
    reasons = []
    if not is_t2_manifest_shape(manifest):
        reasons.append('U03_T2_MANIFEST_SHAPE_INVALID')
    if not (_exact(target_contracts, ('workspaceContract', 'runtimeContract')) and
            all(isinstance(target_contracts[key], str) and target_contracts[key]
                for key in ('workspaceContract', 'runtimeContract'))):
        reasons.append('U03_T2_TARGET_INVALID')
    if reasons:
        return {'schema': SCHEMA, 'declaredCompatible': False,
                'admission': 'NOT_ASSESSED', 'reasons': reasons, 'module': None}
    c = manifest['compatibility']
    if c['workspace_contract'] != target_contracts['workspaceContract']:
        reasons.append('U03_T2_WORKSPACE_CONTRACT_MISMATCH')
    if c['runtime_contract'] != target_contracts['runtimeContract']:
        reasons.append('U03_T2_RUNTIME_CONTRACT_MISMATCH')
    return {'schema': SCHEMA, 'declaredCompatible': not reasons,
            'admission': 'NOT_ASSESSED', 'reasons': reasons,
            'module': {'id': manifest['module_id'], 'version': manifest['module_version'],
                       'declaredContributionCounts': {
                           key: len(manifest['contributions'][key]) for key in GROUPS}}}


def is_expected_t2_schema(schema):
    if not isinstance(schema, dict) or not _exact(schema.get('properties'), (
            'module_manifest_version', 'module_id', 'module_version', 'title',
            'compatibility', 'contributions', 'security')):
        return False
    p = schema['properties']
    return (schema.get('$id') == 'https://iwxa.dev/schemas/workspace-module-manifest/1.0.0' and
            schema.get('additionalProperties') is False and
            p['module_manifest_version'].get('const') == '1.0.0' and
            p['compatibility'].get('additionalProperties') is False and
            p['compatibility'].get('properties', {}).get('runtime_contract', {}).get('const') == '1.3.0' and
            p['contributions'].get('additionalProperties') is False and
            p['security'].get('additionalProperties') is False and
            all(p['security'].get('properties', {}).get(key, {}).get('const') is False
                for key in ('direct_indexeddb', 'remote_code', 'iwxa_executable_payload')))
