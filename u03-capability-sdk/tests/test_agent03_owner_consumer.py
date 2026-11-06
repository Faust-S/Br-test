"""Agent 0.3 existing Core snapshot does not require another U01 read."""
import json
import unittest
from copy import deepcopy
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'python'))
from program_card import project_program_card
CASES=json.loads((ROOT/'fixtures/agent03-owner-projection.json').read_text(encoding='utf-8'))

class Agent03OwnerConsumer(unittest.TestCase):
    def test_existing_core_snapshot_cases(self):
        for entry in CASES:
            with self.subTest(case=entry['case']):
                result=project_program_card(**deepcopy(entry['args']))
                op=next(x for x in result['capabilities'] if x['kind']=='OPERATION')
                for key,value in entry['expected'].items():
                    self.assertEqual(op[key],value,key)
                self.assertNotIn('grants',result)
                self.assertNotIn('invoke',result)
    def test_no_mixed_raw_observations(self):
        entry=deepcopy(CASES[0]['args'])
        entry['u01']['capabilities']=entry['u01']['ownerProjection']['source']
        with self.assertRaisesRegex(ValueError,'U03_PROGRAM_CARD_INPUT_INVALID'):
            project_program_card(**entry)
    def test_source_identity_and_authority(self):
        entry=deepcopy(CASES[0]['args'])
        entry['u01']['ownerProjection']['execution_authority_granted']=True
        with self.assertRaisesRegex(ValueError,'U03_PROGRAM_CARD_INPUT_INVALID'):
            project_program_card(**entry)
        entry['u01']['ownerProjection']['execution_authority_granted']=False
        entry['u01']['ownerProjection']['program_id']='OTHER_PROGRAM_V1'
        with self.assertRaisesRegex(ValueError,'U03_PROGRAM_CARD_INPUT_INVALID'):
            project_program_card(**entry)
    def test_operation_binding_mismatch(self):
        entry=deepcopy(CASES[0]['args'])
        entry['u01']['ownerProjection']['operations'][0]['capability_id']='wrong.capability'
        result=project_program_card(**entry)
        op=next(x for x in result['capabilities'] if x['kind']=='OPERATION')
        self.assertEqual(op['reason'],'OWNER_OPERATION_MISMATCH')
        entry['u01']['ownerProjection']['operations'][0]['capability_id']='iwxa.program.operation.execute'
        entry['u01']['ownerProjection']['operations'][0]['execution_verified']=False
        result=project_program_card(**entry)
        op=next(x for x in result['capabilities'] if x['kind']=='OPERATION')
        self.assertEqual(op['reason'],'OWNER_OBSERVATION_INCONSISTENT')
    def test_pure_readback(self):
        item=deepcopy(CASES[1]['args'])
        prior=deepcopy(item)
        self.assertEqual(project_program_card(**item),project_program_card(**item))
        self.assertEqual(item,prior)
