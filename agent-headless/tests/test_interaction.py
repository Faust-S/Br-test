"""Protocol and lifecycle regression checks (FAKE worker, not native evidence)."""
from __future__ import annotations
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from iwxa_obscura.interaction import (
    ObscuraInteractiveController, ObscuraInteractionError,
)

FAKE = r'''#!/usr/bin/env python3
import json,os,sys,time
print("IWXA_ORIGIN_NET_GUARD_V2_READY_NO_RENDER", flush=True)
assert os.environ["IWXA_OBSCURA_ALLOWED_ORIGIN"] == "https://example.org"
url = "https://example.org/"
clicked = False
for line in sys.stdin:
    c=json.loads(line)
    cmd=c["cmd"]
    if cmd=="navigate":
        url=c["url"]
        if url.endswith("/timeout"):time.sleep(3)
        if url.endswith("/bad-origin"):url="https://other.org/"
        result={"url":url,"status":200,"title":"Fixture"}
    elif cmd=="evaluate":
        expr=c["expression"]
        if "tagName==='A'" in expr:
            result={"href":"https://example.org/next","text":"Next"}
            if url.endswith("/foreign-link"):
                result["href"]="https://other.org/next"
        elif "querySelectorAll('a[href]')" in expr:
            if url.endswith("/bad-links"):
                result={"invalid":"array_required"}
            elif url.endswith("/no-links"):
                result=[]
            else:
                result=[
                    {"href":"https://example.org/next#fragment","text":"Next page"},
                    {"href":"https://elsewhere.org/untrusted","text":"External"},
                    {"href":"https://example.org/next","text":"Duplicate"},
                    {"href":"https://example.org/help","text":"Help"},
                    {"href":"javascript:alert(1)","text":"Unsafe"},
                ]
        elif "dispatched:true" in expr:
            result={"dispatched":True}
            clicked=True
            if url.endswith("/not-clickable"):
                result={"dispatched":False}
                clicked=False
        elif "String(n.textContent)" in expr:
            result="Confirmed" if clicked and url.endswith("/confirmed") else "Visible content"
        else:result=None
    elif cmd in ("dump_text","dump_html"):
        result="real data from "+url
    elif cmd=="shutdown":
        result="bye"
    else:
        print(json.dumps({"ok":False,"error":"unknown"}),flush=True)
        continue
    print(json.dumps({"ok":True,"result":result}),flush=True)
    if cmd=="shutdown":break
'''


@unittest.skipIf(os.name == "nt", "POSIX fixture; real Windows native test is separate")
class InteractionTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.binary = Path(tmp.name) / "obscura-worker"
        self.binary.write_text(FAKE, encoding="utf-8")
        self.binary.chmod(0o700)
        self.controller = ObscuraInteractiveController(
            binary=self.binary,
            binary_sha256=hashlib.sha256(self.binary.read_bytes()).hexdigest(),
            allowed_origins=frozenset({"https://example.org"}),
            timeout_seconds=2, max_output_bytes=16_384,
        )

    def test_dom_text(self):
        result = self.controller.inspect("https://example.org/start", selector="#name")
        self.assertEqual(result["text"], "Visible content")
        self.assertEqual(result["http_status"], 200)
        self.assertEqual(result["final_url"], "https://example.org/start")

    def test_follow_link_navigation_and_readback(self):
        result = self.controller.follow_link("https://example.org/start", selector="a.next")
        self.assertEqual(result["final_url"], "https://example.org/next")
        self.assertIn("/next", result["content"])
        self.assertEqual(result["external_effect_state"], "NONE")

    def test_discover_same_origin_links_deduplicated(self):
        result = self.controller.list_links("https://example.org/start")
        self.assertEqual([item["url"] for item in result["links"]], [
            "https://example.org/next", "https://example.org/help",
        ])
        self.assertEqual(result["links"][0]["text"], "Next page")
        self.assertEqual(result["candidate_count"], 5)
        self.assertEqual(result["external_effect_state"], "NONE")

    def test_link_list_limit_and_empty_list(self):
        result = self.controller.list_links("https://example.org/start", max_links=1)
        self.assertEqual(len(result["links"]), 1)
        self.assertTrue(result["possibly_truncated"])
        empty = self.controller.list_links("https://example.org/no-links")
        self.assertEqual(empty["links"], [])
        self.assertFalse(empty["possibly_truncated"])

    def test_link_list_denies_bad_limit_or_worker_payload(self):
        for limit in (0, 101, True, "3"):
            with self.assertRaisesRegex(ObscuraInteractionError, "DOM_LINK_LIMIT_DENIED"):
                self.controller.list_links("https://example.org/start", max_links=limit)
        with self.assertRaisesRegex(ObscuraInteractionError, "DOM_LINK_LIST_INVALID"):
            self.controller.list_links("https://example.org/bad-links")

    def test_cross_origin_link_rejected_before_second_navigation(self):
        with self.assertRaisesRegex(ObscuraInteractionError, "ORIGIN_DENIED"):
            self.controller.follow_link("https://example.org/foreign-link", selector="a")

    def test_cross_origin_document_rejected(self):
        with self.assertRaisesRegex(ObscuraInteractionError, "WORKER_FINAL_ORIGIN_DENIED"):
            self.controller.inspect("https://example.org/bad-origin", selector="p")

    def test_unconfirmed_click_not_success(self):
        with self.assertRaises(ObscuraInteractionError) as caught:
            self.controller.click_control(
                "https://example.org/start", selector="button",
                confirmation_selector="#result", expected_text="Confirmed",
            )
        self.assertEqual(caught.exception.code, "DOM_POSTCONDITION_NOT_OBSERVED")
        self.assertEqual(caught.exception.external_effect_state, "UNKNOWN")

    def test_confirmed_click_still_unknown_remote_effect(self):
        result = self.controller.click_control(
            "https://example.org/confirmed", selector="button",
            confirmation_selector="#result", expected_text="Confirmed",
        )
        self.assertTrue(result["dom_postcondition_observed"])
        self.assertEqual(result["external_effect_state"], "UNKNOWN")

    def test_rejected_selector(self):
        with self.assertRaisesRegex(ObscuraInteractionError, "DOM_SELECTOR_DENIED"):
            self.controller.inspect("https://example.org/", selector="\n")

    def test_cancel_before_navigation(self):
        with self.assertRaisesRegex(ObscuraInteractionError, "ENGINE_CANCELLED"):
            self.controller.inspect("https://example.org/", selector="p", is_cancelled=lambda: True)

    def test_timeout_reaps_child(self):
        with self.assertRaisesRegex(ObscuraInteractionError, "ENGINE_TIMEOUT"):
            self.controller.inspect("https://example.org/timeout", selector="p")

    def test_binary_pin(self):
        self.binary.write_text(FAKE + "\n# changed")
        with self.assertRaisesRegex(Exception, "ENGINE_DIGEST_MISMATCH"):
            self.controller.inspect("https://example.org/", selector="p")


if __name__ == "__main__":
    unittest.main()
