"""Exercise the G2 test website independently of the browser test."""
import hashlib
import http.server
import threading
import unittest
import urllib.error
import urllib.parse
import urllib.request
from http.cookiejar import CookieJar

from advanced_fixture import DOWNLOAD_BYTES, UPLOAD_BYTES, handle_get, handle_post

class FixtureTest(unittest.TestCase):
    def setUp(self):
        self.events = []
        record = self.events.append
        class Handler(http.server.BaseHTTPRequestHandler):
            def log_message(self, *args): pass
            def do_GET(self):
                if not handle_get(self, record): self.send_error(404)
            def do_POST(self):
                if not handle_post(self, record): self.send_error(404)
        self.server = http.server.ThreadingHTTPServer(("127.0.0.1",0), Handler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.base = f"http://127.0.0.1:{self.server.server_port}"
        self.jar = CookieJar()
        self.client = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(self.jar))

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def get(self, path):
        with self.client.open(self.base+path, timeout=5) as response:
            return response.read()

    def post(self, path, body=b""):
        with self.client.open(self.base+path, data=body, timeout=5) as response:
            return response.read()

    def login(self):
        self.assertIn(b"Session: test operator", self.post("/signin"))

    def test_cookie_session_requires_explicit_signin(self):
        self.assertIn(b"Session: signed out", self.get("/advanced"))
        self.login()
        self.assertIn(b"Session: test operator", self.get("/advanced"))
        self.assertEqual(len(list(self.jar)), 1)

    def test_authenticated_order_exact_fields(self):
        self.login()
        body = urllib.parse.urlencode({"company":"IWXA Lab","delivery":"Express","receipt":"yes"}).encode()
        self.assertEqual(self.post("/advanced-order",body),b"Order accepted 167")
        self.assertEqual(self.events[-1]["kind"],"advanced_order")

    def test_bad_order_rejected(self):
        self.login()
        with self.assertRaises(urllib.error.HTTPError) as failure:
            self.post("/advanced-order",b"company=Incorrect")
        self.assertEqual(failure.exception.code,422)
        self.assertEqual(self.events[-1]["kind"],"advanced_order_rejected")

    def test_upload_reports_actual_hash_and_size(self):
        self.login()
        self.post("/upload",UPLOAD_BYTES)
        self.assertEqual(self.events[-1],
            {"kind":"advanced_upload","sha256":hashlib.sha256(UPLOAD_BYTES).hexdigest(),"size":len(UPLOAD_BYTES)})

    def test_download_actual_bytes(self):
        self.login()
        self.assertEqual(self.get("/download"),DOWNLOAD_BYTES)

    def test_no_file_operations_without_session(self):
        with self.assertRaises(urllib.error.HTTPError) as upload:
            self.post("/upload",UPLOAD_BYTES)
        self.assertEqual(upload.exception.code,401)
        with self.assertRaises(urllib.error.HTTPError) as download:
            self.get("/download")
        self.assertEqual(download.exception.code,401)

if __name__ == "__main__":
    unittest.main()
