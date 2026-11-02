"""No-network protocol checks for the standalone Obscura JSON-line worker.

Tests use a fake executable, not a real browser engine. Do not infer site
compatibility or network isolation from them.
"""
from __future__ import annotations

import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from iwxa_obscura.worker import ObscuraWorkerController
from iwxa_obscura.controller import ObscuraControllerError


FAKE_WORKER = '''#!/usr/bin/env python3
import json,os,sys,time
commands=[json.loads(x) for x in sys.stdin if x.strip()]
assert [x['cmd'] for x in commands] in (
    ['navigate','dump_text','shutdown'],['navigate','dump_html','shutdown'])
assert commands[0]['waitUntil']=='networkidle2'
assert os.environ['OBSCURA_OBEY_ROBOTS']=='1'
assert not os.getenv('OBSCURA_CDP_TOKEN') and not os.getenv('HTTPS_PROXY')
assert not os.getenv('SSL_CERT_FILE')
url=commands[0]['url']
path=url.split('?',1)[0].rsplit('/',1)[-1]
if path=='timeout':time.sleep(3)
if path=='exit':sys.exit(9)
if path=='invalid':sys.stdout.write('{invalid\\n');sys.exit(0)
if path=='large':sys.stdout.write('X'*30000);sys.exit(0)
if path=='redirect':url='https://other.example.org/secret'
if path=='same':url='https://example.org/moved'
status=404 if path=='notfound' else None if path=='nostatus' else True if path=='bool' else 200
print(json.dumps({'ok':True,'result':{'title':'Actual title','url':url,'status':status}}),flush=True)
print(json.dumps({'ok':True,'result':'page contents'}),flush=True)
print(json.dumps({'ok':True,'result':'bye'}),flush=True)
'''


@unittest.skipIf(os.name == "nt", "POSIX shebang fixture; Windows needs a packaged .exe")
class WorkerTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.binary = Path(self.tmp.name) / "obscura-worker"
        self.binary.write_text(FAKE_WORKER, encoding="utf-8")
        self.binary.chmod(0o700)
        self.controller = ObscuraWorkerController(
            binary=self.binary,
            binary_sha256=hashlib.sha256(self.binary.read_bytes()).hexdigest(),
            allowed_origins=frozenset({"https://example.org"}),
            timeout_seconds=2, max_output_bytes=8192)

    def test_status_final_url_hash_and_evidence(self):
        o = self.controller.observe("https://example.org/a?x=1#fragment")
        self.assertEqual(o.http_status, 200)
        self.assertEqual(o.final_url, "https://example.org/a?x=1")
        self.assertEqual(o.page_title, "Actual title")
        self.assertEqual(o.content_sha256, hashlib.sha256(b"page contents").hexdigest())
        self.assertEqual(o.as_data()["network_isolation"], "NOT_ESTABLISHED")

    def test_404_not_relabelled_as_200(self):
        self.assertEqual(self.controller.observe("https://example.org/notfound", format="html").http_status, 404)

    def test_same_origin_redirect_reported(self):
        self.assertEqual(self.controller.observe("https://example.org/same").final_url,
                         "https://example.org/moved")

    def test_cross_origin_redirect_result_denied(self):
        with self.assertRaisesRegex(ObscuraControllerError, "WORKER_FINAL_ORIGIN_DENIED"):
            self.controller.observe("https://example.org/redirect")

    def test_missing_status_rejected(self):
        with self.assertRaisesRegex(ObscuraControllerError, "WORKER_PROTOCOL_INVALID"):
            self.controller.observe("https://example.org/nostatus")

    def test_boolean_status_rejected(self):
        with self.assertRaisesRegex(ObscuraControllerError, "WORKER_PROTOCOL_INVALID"):
            self.controller.observe("https://example.org/bool")

    def test_malformed_json_rejected(self):
        with self.assertRaisesRegex(ObscuraControllerError, "WORKER_PROTOCOL_INVALID"):
            self.controller.observe("https://example.org/invalid")

    def test_stdout_limit(self):
        with self.assertRaisesRegex(ObscuraControllerError, "ENGINE_OUTPUT_LIMIT"):
            self.controller.observe("https://example.org/large")

    def test_timeout(self):
        with self.assertRaisesRegex(ObscuraControllerError, "ENGINE_TIMEOUT"):
            self.controller.observe("https://example.org/timeout")

    def test_nonzero_exit(self):
        with self.assertRaisesRegex(ObscuraControllerError, "ENGINE_WORKER_FAILED"):
            self.controller.observe("https://example.org/exit")

    def test_eval_format_denied(self):
        with self.assertRaisesRegex(ObscuraControllerError, "FORMAT_DENIED"):
            self.controller.observe("https://example.org", format="evaluate")

    def test_binary_digest_pin(self):
        self.binary.write_text(FAKE_WORKER + "\n#tampered\n")
        with self.assertRaisesRegex(ObscuraControllerError, "ENGINE_DIGEST_MISMATCH"):
            self.controller.observe("https://example.org")

    def test_inherited_env_secrets_are_not_passed_to_worker(self):
        with mock.patch.dict(os.environ, {"OBSCURA_CDP_TOKEN":"secret",
                                          "HTTPS_PROXY":"http://proxy.invalid",
                                          "SSL_CERT_FILE":"untrusted.pem"}):
            self.assertEqual(self.controller.observe("https://example.org").http_status, 200)

    def test_bad_wait_until_rejected(self):
        with self.assertRaises(ValueError):
            ObscuraWorkerController(
                binary=self.binary,
                binary_sha256="f"*64,
                allowed_origins=frozenset({"https://example.org"}),
                wait_until="incorrect")


if __name__ == "__main__":
    unittest.main()
