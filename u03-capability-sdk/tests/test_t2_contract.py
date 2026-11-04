"""Shared native T2 contract scenarios, independent Python consumer."""
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'python'))
from t2_manifest import inspect_t2_contract, is_t2_manifest_shape


def read(name):
    return json.loads((ROOT / 'fixtures' / name).read_text(encoding='utf-8'))


def apply(base, case):
    from copy import deepcopy
    result = deepcopy(base)
    result.update(case.get('patch', {}))
    if isinstance(result.get('compatibility'), dict):
        result['compatibility'].update(case.get('compatibilityPatch', {}))
    if isinstance(result.get('security'), dict):
        result['security'].update(case.get('securityPatch', {}))
    if case.get('omitContribution'):
        del result['contributions'][case['omitContribution']]
    return result


class T2Comparison(unittest.TestCase):
    def test_shared_scenarios(self):
        base = read('t2-manifest.json')
        for case in read('t2-scenarios.json'):
            with self.subTest(case=case['name']):
                manifest = apply(base, case)
                result = inspect_t2_contract(manifest, case.get('target', {
                    'workspaceContract': '0.1.0', 'runtimeContract': '1.3.0'}))
                self.assertEqual(result['reasons'], case['expected'])
                self.assertEqual(result['declaredCompatible'], not case['expected'])
                self.assertEqual(result['admission'], 'NOT_ASSESSED')
                self.assertNotIn('grants', result)
                self.assertEqual(result['module'] is None, any(reason in case['expected'] for reason in
                                 ('U03_T2_MANIFEST_SHAPE_INVALID', 'U03_T2_TARGET_INVALID')))
                if result['module']:
                    self.assertEqual(result['module']['declaredContributionCounts']['routes'], 1)

    def test_no_t2_to_u03_schema_substitution(self):
        base = read('t2-manifest.json')
        self.assertTrue(is_t2_manifest_shape(base))
        p = inspect_t2_contract(base, {'workspaceContract':'0.1.0', 'runtimeContract':'1.3.0'})
        self.assertTrue(p['declaredCompatible'])
        self.assertEqual(p['module']['id'], base['module_id'])
        self.assertEqual(p['module']['version'], base['module_version'])
        self.assertNotIn('provides', p['module'])
        self.assertNotIn('hostCompatibility', p['module'])

if __name__ == '__main__':
    unittest.main()
