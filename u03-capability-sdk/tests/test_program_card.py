"""Same I0-I5 source-bound Programs Center scenarios as JavaScript."""
import json
from pathlib import Path
import sys
import unittest
from copy import deepcopy
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'python'))
from program_card import project_program_card

CASES=json.loads((ROOT/'fixtures/program-card-scenarios.json').read_text('utf-8'))

def project(case):
    return project_program_card(**deepcopy(case['args']))

def by_kind(result):
    return {x['kind']:x for x in result['capabilities']}

class ProgramCardConsumers(unittest.TestCase):
    def test_shared_four_cases(self):
        for entry in CASES:
            with self.subTest(case=entry['case']):
                result=project(entry)
                self.assertEqual(result['programId'],entry['args']['programId'])
                self.assertEqual(result['highestVerified'],entry['args']['ui']['highestVerified'])
                by_id={x['id']:x for x in result['capabilities']}
                self.assertEqual(len(by_id),len(entry['args']['channels']))
                for identifier,state in entry['expected'].items():
                    self.assertEqual(by_id[identifier]['state'],state)
                    self.assertTrue(by_id[identifier]['version'])
                    self.assertIn(by_id[identifier]['state'],
                        ('DECLARED','INTEGRATED','AVAILABLE','EXECUTABLE','BLOCKED'))
                for identifier,values in entry.get('checks',{}).items():
                    for k,v in values.items():
                        self.assertEqual(by_id[identifier][k],v)
                self.assertNotIn('grants',result)

    def test_stale_receipt_is_not_executable(self):
        sample=deepcopy(next(c['args'] for c in CASES if c['case']=='atlas_embedded_ui'))
        sample['u01']['capabilities']['connection_epoch']='epoch0000000000002'
        result=by_kind(project_program_card(**sample))
        self.assertEqual(result['OPERATION']['state'],'BLOCKED')
        self.assertEqual(result['OPERATION']['reason'],'execution-receipt-missing')
        self.assertEqual(result['EMBEDDED_VIEW']['state'],'DECLARED')

    def test_window_does_not_imply_embed(self):
        case=next(c for c in CASES if c['case']=='obsidian_native_window')
        rows=by_kind(project(case))
        self.assertEqual(rows['LINKED_WINDOW']['state'],'DECLARED')
        self.assertEqual(rows['EMBEDDED_VIEW']['state'],'BLOCKED')
        self.assertFalse(rows['OPERATION']['executionConfirmed'])

    def test_browsers_shared_surface_target_is_not_verified(self):
        case=next(c for c in CASES if c['case']=='browser_shared_live_target')
        rows=by_kind(project(case))
        self.assertEqual(rows['DISCOVERY']['state'],'AVAILABLE')
        self.assertEqual(rows['SHARED_LIVE_SURFACE']['state'],'DECLARED')
        self.assertFalse(rows['SHARED_LIVE_SURFACE']['verified'])

    def test_invalid_config_and_duplicate_status(self):
        case=deepcopy(CASES[0]['args'])
        case['channels']=[]
        with self.assertRaisesRegex(ValueError,'U03_PROGRAM_CARD_INPUT_INVALID'):
            project_program_card(**case)
        case=deepcopy(CASES[0]['args'])
        case['statuses'].append(case['statuses'][0])
        with self.assertRaisesRegex(ValueError,'U03_PROGRAM_CARD_INPUT_INVALID'):
            project_program_card(**case)

if __name__=='__main__':
    unittest.main()
