"""Fixture and graph tests; not a substitute for a vendor n8n run."""
import json
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.request import Request, urlopen
from urllib.error import HTTPError
from probe import PostLedger, make_workflow


class ProbeTests(unittest.TestCase):
    def test_two_real_http_nodes(self):
        w = make_workflow("trial", 3333, "ok")
        self.assertEqual([n["type"] for n in w["nodes"]],
                         ["n8n-nodes-base.manualTrigger", "n8n-nodes-base.httpRequest",
                          "n8n-nodes-base.httpRequest"])
        self.assertEqual(w["connections"]["HTTP 1"]["main"][0][0]["node"], "HTTP 2")

    def test_loopback_only(self):
        w = make_workflow("trial", 4567, "fail")
        self.assertTrue(all(n["parameters"]["url"].startswith("http://127.0.0.1:4567/fail/")
                            for n in w["nodes"][1:]))
        self.assertTrue(all(n["parameters"]["method"] == "POST" for n in w["nodes"][1:]))

    def test_no_automatic_retry(self):
        self.assertFalse(any(n.get("retryOnFail", False) for n in
                             make_workflow("trial", 22, "ok")["nodes"]))

    def test_http_fixture_receipt_and_503(self):
        with PostLedger.lock:
            PostLedger.entries = []
        s = ThreadingHTTPServer(("127.0.0.1", 0), PostLedger)
        t = threading.Thread(target=s.serve_forever, daemon=True)
        t.start()
        try:
            for route, expected in (("ok/1", 200), ("fail/2", 503)):
                req = Request(f"http://127.0.0.1:{s.server_port}/{route}",
                              data=json.dumps({"token": "IWXA-N8N-PUBLIC"}).encode(),
                              headers={"Content-Type": "application/json"}, method="POST")
                try:
                    with urlopen(req, timeout=3) as resp:
                        status = resp.status
                except HTTPError as err:
                    status = err.code
                self.assertEqual(status, expected)
            self.assertEqual([x["path"] for x in PostLedger.entries], ["/ok/1", "/fail/2"])
        finally:
            s.shutdown()
            s.server_close()
            t.join(timeout=3)


if __name__ == "__main__":
    unittest.main()
