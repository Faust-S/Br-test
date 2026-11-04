"""Python consumer verifies the existing U01 shape against U03 projection."""
import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1] / 'python'))
from u01_bridge import observe_u01_capabilities

SRC = {"schema":"IWXA_PROGRAM_CONNECTION/1.0.0","program_id":"JQ_CLI_V1","transport":"cli",
       "status":"DECLARED","connected":True,"connection_epoch":"fixtureepoch0000001",
       "operations":[{"name":"evaluate","declared":True,"verified":True}]}
RECEIPT = {"observationRef":"host:test:42","observation":{
           "schema":SRC["schema"],"program_id":SRC["program_id"],"transport":"cli",
           "connection_epoch":"fixtureepoch0000001","status":"EXECUTED","executed":True,"operation":"evaluate","output":"42"}}
CFG = {"programId":"JQ_CLI_V1","bindings":[{"operation":"evaluate","id":"iwxa.cli.evaluate","version":"1.0.0"}],
       "receipts":{"evaluate":RECEIPT}}

class Connection:
    def __init__(self, value):
        self.value=value
    def capabilities(self):
        return self.value

def value(source=SRC, cfg=CFG):
    return observe_u01_capabilities(Connection(source),cfg)[0]

class ProjectionTests(unittest.TestCase):
    def test_operation_requires_live_verified_and_receipt(self):
        self.assertTrue(value()["available"])
        self.assertEqual(value(SRC,{**CFG,"receipts":{}})["reason"],"execution-receipt-missing")
        s={**SRC,"operations":[{"name":"evaluate","declared":True,"verified":False}]}
        self.assertEqual(value(s)["reason"],"operation-unverified")
        for patch in ({"connected":False},{"status":"CONNECTION_LOST"},{"program_id":"WRONG_V1"},
                      {"schema":"IWXA_PROGRAM_CONNECTION/2.0.0"},{"transport":"unsupported"}):
            with self.subTest(patch=patch):
                self.assertFalse(value({**SRC,**patch})["available"])
        self.assertEqual(value({**SRC,"operations":[]})["reason"],"operation-unsupported")
        self.assertEqual(value({**SRC,"operations":[SRC["operations"][0]]*2})["reason"],"source-unavailable")
        for patch in ({"program_id":"YQ_CLI_V1"},{"operation":"wrong"},{"status":"EXECUTION_FAILED"},{"executed":False}):
            r={**RECEIPT,"observation":{**RECEIPT["observation"],**patch}}
            self.assertFalse(value(SRC,{**CFG,"receipts":{"evaluate":r}})["available"])

    def test_config_rejects_bad_shapes(self):
        for invalid in ({**CFG,"programId":"WRONG\n"},
                        {**CFG,"bindings":[{**CFG["bindings"][0],"id":"iwxa.cli.evaluate\n"}]},
                        {**CFG,"bindings":CFG["bindings"]*2},{**CFG,"receipts":None}):
            with self.assertRaisesRegex(ValueError,"U03_U01_BINDINGS_INVALID"):
                value(SRC,invalid)

    def test_source_error(self):
        class Gone:
            def capabilities(self):
                raise RuntimeError("gone")
        self.assertFalse(observe_u01_capabilities(Gone(),CFG)[0]["available"])

if __name__=="__main__":
    unittest.main()

class ConnectionEpochTests(unittest.TestCase):
    def test_replay_after_reconnect_and_repeat_invoke_is_refused(self):
        current = {**SRC, "connection_epoch": "fixtureepoch0000002"}
        self.assertEqual(value(current)["reason"], "execution-receipt-missing")
        self.assertEqual(value({**SRC, "connection_epoch": None})["reason"], "connection-unscoped")
        self.assertEqual(value({**SRC, "connection_epoch": "x"})["reason"], "connection-unscoped")
        newer = {**RECEIPT, "observation": {**RECEIPT["observation"],
                 "connection_epoch": current["connection_epoch"]}}
        self.assertTrue(value(current, {**CFG, "receipts": {"evaluate": newer}})["available"])
