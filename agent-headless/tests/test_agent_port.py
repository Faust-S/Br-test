"""ToolProviderPort adapter contract probes without importing private Agent sources.

These are unit tests, NOT actual Agent Task, native worker or approval evidence.
"""
from __future__ import annotations
import hashlib
from pathlib import Path
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from iwxa_obscura.agent_port import ObscuraAgentToolPort
from iwxa_obscura.interaction import ObscuraInteractiveController, ObscuraInteractionError

PORT = ModuleType("providers.tool_provider_port")
PACKAGE = ModuleType("providers")

class PortError(RuntimeError):
    def __init__(self, code, detail="", external_effect_state="NONE"):
        super().__init__(code)
        self.code=code
        self.detail=detail
        self.external_effect_state=external_effect_state

class Result:
    @staticmethod
    def from_adapter_result(raw, *, request):
        return raw

PORT.ToolProviderPortError=PortError
PORT.ToolProviderResult=Result

class DummyRequest:
    def __init__(self, operation, args, *, idempotency=None, plan=None,
                 descriptor="a"*64, provider="obscura"):
        self.logical_provider_id=provider
        self.tool_descriptor_ref={
            "tool_id":operation,"descriptor_digest":descriptor,
            "provider_slot":"web_acquisition",
            "provider_binding_digest":"b"*64,
        }
        self.operation=operation
        self.arguments=args
        self.target=None
        self.idempotency_key_or_none=idempotency
        self.tool_plan_digest_or_none=plan
        self.request_id="req-test"
    def validate(self):
        return self

class PortTests(unittest.TestCase):
    def setUp(self):
        self.env=patch.dict(sys.modules, {"providers":PACKAGE,"providers.tool_provider_port":PORT})
        self.env.start()
        self.addCleanup(self.env.stop)
        self.controller=object.__new__(ObscuraInteractiveController)
        self.port=ObscuraAgentToolPort(
            self.controller, provider_id="obscura",binding_digest="b"*64,
            descriptor_digest={
                "web.fetch":"a"*64,"web.inspect":"a"*64,
                "web.follow_link":"a"*64,"web.click_control":"a"*64,
            })

    def test_fetch_returns_observation_without_side_effect_claim(self):
        with patch.object(ObscuraInteractiveController,"observe",
                          return_value=SimpleNamespace(as_data=lambda:{"content":"real"})):
            result=self.port.invoke(DummyRequest("web.fetch",{"url":"https://example.org/"}))
        self.assertEqual(result["data"]["content"],"real")
        self.assertEqual(result["external_effect_state"],"NONE")

    def test_dom_inspection(self):
        with patch.object(ObscuraInteractiveController,"inspect",return_value={"text":"hello"}):
            result=self.port.invoke(DummyRequest(
                "web.inspect",{"url":"https://example.org/","selector":"body"}))
        self.assertEqual(result["data"]["text"],"hello")

    def test_follow_link(self):
        with patch.object(ObscuraInteractiveController,"follow_link",return_value={"final_url":"https://example.org/a"}):
            result=self.port.invoke(DummyRequest(
                "web.follow_link",{"url":"https://example.org/","selector":"a"}))
        self.assertEqual(result["external_effect_state"],"NONE")

    def test_action_requires_plan_and_idempotency(self):
        args={"url":"https://example.org/","selector":"button","confirmation_selector":"#done","expected_text":"Done"}
        for idk,plan in ((None,None),("key",None),(None,"c"*64)):
            with self.assertRaisesRegex(PortError,"PORT_PROTOCOL_ERROR"):
                self.port.invoke(DummyRequest("web.click_control",args,idempotency=idk,plan=plan))

    def test_action_uncertain_even_with_dom_confirmation(self):
        args={"url":"https://example.org/","selector":"button","confirmation_selector":"#done","expected_text":"Done"}
        with patch.object(ObscuraInteractiveController,"click_control",return_value={"dom_postcondition_observed":True}):
            result=self.port.invoke(DummyRequest("web.click_control",args,idempotency="key",plan="c"*64))
        self.assertEqual(result["external_effect_state"],"UNKNOWN")

    def test_action_timeout_preserves_uncertain_effect(self):
        args={"url":"https://example.org/","selector":"button","confirmation_selector":"#done","expected_text":"Done"}
        with patch.object(ObscuraInteractiveController,"click_control",
                          side_effect=ObscuraInteractionError("ENGINE_TIMEOUT",external_effect_state="UNKNOWN")):
            with self.assertRaises(PortError) as caught:
                self.port.invoke(DummyRequest("web.click_control",args,idempotency="key",plan="c"*64))
        self.assertEqual(caught.exception.external_effect_state,"UNKNOWN")

    def test_wrong_descriptor_rejected(self):
        with self.assertRaisesRegex(PortError,"PORT_PROTOCOL_ERROR"):
            self.port.invoke(DummyRequest("web.fetch",{"url":"https://example.org/"},descriptor="c"*64))

    def test_unbound_provider_rejected(self):
        with self.assertRaisesRegex(PortError,"PORT_UNAVAILABLE"):
            self.port.invoke(DummyRequest("web.fetch",{"url":"https://example.org/"},provider="other"))

    def test_unsupported_operation_rejected(self):
        with self.assertRaisesRegex(PortError,"PORT_PROTOCOL_ERROR"):
            self.port.invoke(DummyRequest("web.eval",{"url":"https://example.org/","js":"alert(1)"}))


if __name__=="__main__":
    unittest.main()
