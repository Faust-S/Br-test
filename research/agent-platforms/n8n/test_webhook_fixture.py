"""Unit checks of the authored published-webhook fixture, not n8n server behavior."""
import unittest
from webhook_probe import workflow_for_webhook

class PublicWebhookFixtureTests(unittest.TestCase):
    def test_webhook_route_and_trigger(self):
        w=workflow_for_webhook("ok", 9911)
        self.assertEqual(w["nodes"][0]["type"],"n8n-nodes-base.webhook")
        self.assertEqual(w["nodes"][0]["parameters"]["path"],"iwxa-public-ok")
        self.assertEqual(w["nodes"][0]["parameters"]["httpMethod"],"POST")
        self.assertEqual(w["nodes"][0]["parameters"]["responseMode"],"lastNode")
    def test_chain_and_http_ledger(self):
        w=workflow_for_webhook("fail", 9911)
        self.assertEqual(w["connections"]["Webhook"]["main"][0][0]["node"],"HTTP 1")
        self.assertEqual(w["connections"]["HTTP 1"]["main"][0][0]["node"],"HTTP 2")
        self.assertTrue(w["nodes"][2]["parameters"]["url"].endswith("/fail/2"))

if __name__=="__main__":
    unittest.main()
