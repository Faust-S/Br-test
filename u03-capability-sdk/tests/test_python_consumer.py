"""U03 Python consumer of exactly the same public fixtures and schemas as JS."""
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "python"))
from iwxa_capability_sdk import (MODULE_SCHEMA, STATUS_SCHEMA, assess, satisfies,
                                 validate_descriptor, validate_status, ModuleAdapterPort)


def fixture(name):
    return json.loads((ROOT / "fixtures" / name).read_text(encoding="utf-8"))


class ConsumerTests(unittest.TestCase):
    def test_shared_scenarios(self):
        module = fixture("module.json")
        observations = fixture("observations.json")
        for case in fixture("scenarios.json"):
            with self.subTest(case=case["name"]):
                descriptor = {**module, **case.get("descriptorOverrides", {})}
                self.assertEqual(assess(descriptor, case["host"], observations[case["observations"]]),
                                 {"compatible": not case["expected"], "reasons": case["expected"]})

    def test_shared_schemas_have_no_authority(self):
        module_schema = json.loads((ROOT / "contracts/module-descriptor-v1.schema.json").read_text())
        status_schema = json.loads((ROOT / "contracts/capability-status-v1.schema.json").read_text())
        self.assertEqual(module_schema["properties"]["schema"]["const"], MODULE_SCHEMA)
        self.assertEqual(status_schema["properties"]["schema"]["const"], STATUS_SCHEMA)
        for schema in (module_schema, status_schema):
            self.assertFalse(schema["additionalProperties"])
            for field in ("grants", "permissions", "install", "invoke"):
                self.assertNotIn(field, schema["properties"])
        self.assertTrue(validate_descriptor(fixture("module.json")))
        self.assertTrue(validate_status(fixture("observations.json")["verified"][0]))

    def test_strict_versions(self):
        self.assertTrue(satisfies("1.2.0", ">=1.2.0 <2.0.0"))
        self.assertFalse(satisfies("2.0.0", ">=1.2.0 <2.0.0"))
        self.assertFalse(satisfies("1.2.0-beta", ">=1.0.0"))
        self.assertFalse(satisfies("1.2.0", "^1.0.0"))
        self.assertFalse(satisfies("1.2.0", ">=1.0.0 || >=2.0.0"))
        self.assertTrue(satisfies("1000000000000000000.0.0", ">999999999999999999.0.0"))

    def test_port_does_not_authorize_or_attach_on_incompatibility(self):
        called = []
        port = ModuleAdapterPort(fixture("module.json"),
                                 lambda module, host, observed: called.append(host) or {"attached": True, "hostRef": "host:1"},
                                 lambda binding: {"detached": True, "binding": binding})
        self.assertEqual(port.describe(), fixture("module.json"))
        refused = port.attach({"hostId": "iwxa.agent", "version": "0.2.0"}, fixture("observations.json")["verified"])
        self.assertFalse(refused["attached"])
        self.assertEqual(called, [])
        accepted = port.attach({"hostId": "iwxa.agent", "version": "0.3.0"}, fixture("observations.json")["verified"])
        self.assertTrue(accepted["attached"])
        self.assertEqual(len(called), 1)
        self.assertNotIn("grants", accepted)
        self.assertEqual(port.detach("host:1"), {"detached": True, "binding": "host:1"})

    def test_host_must_attest_actual_attach(self):
        port = ModuleAdapterPort(fixture("module.json"), lambda *_: {}, lambda *_: None)
        result = port.attach({"hostId": "iwxa.agent", "version": "0.3.0"}, fixture("observations.json")["verified"])
        self.assertFalse(result["attached"])


if __name__ == "__main__":
    unittest.main()

class StrictEndOfStringTests(unittest.TestCase):
    def test_inputs_reject_trailing_newline(self):
        descriptor = fixture("module.json")
        self.assertFalse(validate_descriptor({**descriptor, "id": "iwxa.module\n"}))
        self.assertFalse(validate_descriptor({**descriptor, "version": "0.1.0\n"}))
        self.assertFalse(validate_descriptor({**descriptor, "requires": [{"id": "iwxa.source.read", "range": ">=1.0.0\n"}]}))
        self.assertFalse(validate_status({**fixture("observations.json")["verified"][0], "id": "iwxa.source.read\n"}))
        self.assertFalse(satisfies("1.2.0\n", ">=1.0.0"))
        self.assertFalse(satisfies("1.2.0", ">=1.0.0\n"))
