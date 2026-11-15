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
        self.target=args.get("url") if idempotency is not None else None
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
                "web.follow_link":"a"*64,"web.list_links":"a"*64,
                "web.click_control":"a"*64,
            }, enable_experimental_click=True)

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

    def test_list_links_read_only_and_uses_controller(self):
        with patch.object(ObscuraInteractiveController, "list_links",
                          return_value={"links": [{"url": "https://example.org/next"}]}) as call:
            result = self.port.invoke(DummyRequest("web.list_links",
                                                  {"url": "https://example.org/"}))
        self.assertEqual(result["data"]["links"][0]["url"], "https://example.org/next")
        self.assertEqual(result["external_effect_state"], "NONE")
        call.assert_called_once_with("https://example.org/")

    def test_list_links_rejects_unbound_limit(self):
        with self.assertRaisesRegex(PortError, "PORT_PROTOCOL_ERROR"):
            self.port.invoke(DummyRequest("web.list_links",
                                          {"url": "https://example.org/", "max_links": 1000}))

    def test_default_port_never_dispatches_unqualified_click(self):
        port = ObscuraAgentToolPort(
            self.controller, provider_id="obscura", binding_digest="b"*64,
            descriptor_digest={"web.click_control": "a"*64},
        )
        args={"url":"https://example.org/","selector":"button","confirmation_selector":"#done","expected_text":"Done"}
        with patch.object(ObscuraInteractiveController, "click_control") as click:
            with self.assertRaises(PortError) as caught:
                port.invoke(DummyRequest("web.click_control", args,
                                         idempotency="key", plan="c"*64))
        self.assertEqual(caught.exception.code, "PORT_UNAVAILABLE")
        click.assert_not_called()

    def test_search_keeps_original_selected_provider(self):
        class ExistingWebProvider:
            def __init__(self):
                self.calls = []
            def invoke(self, request):
                self.calls.append(request)
                return {"search": "existing-provider-result"}

        delegate = ExistingWebProvider()
        port = ObscuraAgentToolPort(
            self.controller, provider_id="obscura", binding_digest="b"*64,
            descriptor_digest={"web.fetch": "a"*64}, delegate=delegate,
        )
        request = DummyRequest("web.search", {"query": "IWXA Obscura"})
        self.assertEqual(port.invoke(request)["search"], "existing-provider-result")
        self.assertEqual(delegate.calls, [request])

    def test_search_delegate_requires_same_authoritative_binding(self):
        class ExistingWebProvider:
            def invoke(self, _):
                raise AssertionError("invalid binding reached original provider")
        port = ObscuraAgentToolPort(
            self.controller, provider_id="obscura", binding_digest="b"*64,
            descriptor_digest={"web.fetch": "a"*64}, delegate=ExistingWebProvider(),
        )
        request = DummyRequest("web.search", {"query": "iwxa"})
        request.tool_descriptor_ref["provider_binding_digest"] = "c"*64
        with self.assertRaises(PortError) as caught:
            port.invoke(request)
        self.assertEqual(caught.exception.code, "PORT_PROTOCOL_ERROR")

    def test_search_delegate_cannot_expand_unreviewed_operations(self):
        class ExistingWebProvider:
            def invoke(self, _):
                raise AssertionError("unsupported operation reached delegate")
        port = ObscuraAgentToolPort(
            self.controller, provider_id="obscura", binding_digest="b"*64,
            descriptor_digest={"web.fetch": "a"*64}, delegate=ExistingWebProvider(),
        )
        with self.assertRaises(PortError) as caught:
            port.invoke(DummyRequest("web.eval", {"js": "alert(1)"}))
        self.assertEqual(caught.exception.code, "PORT_PROTOCOL_ERROR")

    def test_action_requires_plan_and_idempotency(self):
        args={"url":"https://example.org/","selector":"button","confirmation_selector":"#done","expected_text":"Done"}
        for idk,plan in ((None,None),("key",None),(None,"c"*64)):
            with self.assertRaisesRegex(PortError,"PORT_PROTOCOL_ERROR"):
                self.port.invoke(DummyRequest("web.click_control",args,idempotency=idk,plan=plan))

    def test_action_target_must_match_url(self):
        args={"url":"https://example.org/","selector":"button","confirmation_selector":"#done","expected_text":"Done"}
        req=DummyRequest("web.click_control",args,idempotency="key",plan="c"*64)
        req.target="https://other.org/"
        with self.assertRaisesRegex(PortError,"PORT_PROTOCOL_ERROR"):
            self.port.invoke(req)

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
