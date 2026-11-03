"""Offline no-render build-handshake regression checks (fake process only)."""
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from iwxa_obscura.worker import ObscuraWorkerController
from iwxa_obscura.controller import ObscuraControllerError

FAKE = r'''#!/usr/bin/env python3
import json, os, sys
assert os.environ.get('IWXA_OBSCURA_ALLOWED_ORIGIN') == 'https://example.org'
print('IWXA_ORIGIN_NET_GUARD_V2_READY_NO_RENDER',flush=True)
commands=[json.loads(x) for x in sys.stdin if x.strip()]
assert [x['cmd'] for x in commands] == ['navigate','dump_text','shutdown']
assert os.environ['IWXA_OBSCURA_ALLOWED_ORIGIN'] == 'https://example.org'
for r in ({'ok':True,'result':{'title':'OK','url':commands[0]['url'],'status':200}},
          {'ok':True,'result':'observed'},
          {'ok':True,'result':'bye'}):
    print(json.dumps(r),flush=True)
'''

@unittest.skipIf(os.name == "nt", "POSIX fake executable; real Windows binary still unqualified")
class TestOriginGuard(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.binary=Path(self.tmp.name)/'fake-worker'
        self.binary.write_text(FAKE,encoding='utf-8')
        self.binary.chmod(0o700)

    def make(self,**overrides):
        args=dict(binary=self.binary,
                  binary_sha256=hashlib.sha256(self.binary.read_bytes()).hexdigest(),
                  allowed_origins=frozenset({'https://example.org'}),
                  timeout_seconds=3,max_output_bytes=8192)
        args.update(overrides)
        return ObscuraWorkerController(**args)

    def test_guarded_worker_protocol(self):
        item=self.make().observe('https://example.org/a')
        self.assertEqual(item.http_status,200)
        self.assertTrue(item.origin_guard_required)
        self.assertEqual(item.as_data()['network_isolation'],
                         'IWXA_GUARD_V2_GREETING_CONFIRMED_NO_OS_SANDBOX')

    def test_unpatched_worker_rejected(self):
        self.binary.write_text(FAKE.replace("print('IWXA_ORIGIN_NET_GUARD_V2_READY_NO_RENDER')",
                                            "print('wrong-marker')"))
        with self.assertRaisesRegex(ObscuraControllerError,'ENGINE_ORIGIN_GUARD_UNAVAILABLE'):
            self.make().observe('https://example.org')

    def test_guard_probe_failure_rejects_navigation(self):
        self.binary.write_text(FAKE.replace("print('IWXA_ORIGIN_NET_GUARD_V2_READY_NO_RENDER')",
                                            "sys.exit(77)"))
        with self.assertRaisesRegex(ObscuraControllerError,'ENGINE_ORIGIN_GUARD_UNAVAILABLE'):
            self.make().observe('https://example.org')

    def test_bypass_is_not_exposed(self):
        with self.assertRaises(TypeError):
            self.make(unsafe_allow_unpatched_worker=True)

    def test_worker_silent_until_stdin_is_never_navigated(self):
        self.binary.write_text("#!/usr/bin/env python3\nimport sys\n"
                               "sys.stdin.readline()\n")
        with self.assertRaisesRegex(ObscuraControllerError, "WORKER_READY_TIMEOUT"):
            self.make(timeout_seconds=1).observe('https://example.org')

    def test_external_origin_denied_before_running(self):
        with self.assertRaisesRegex(ObscuraControllerError,'ORIGIN_DENIED'):
            self.make().observe('https://outside.org')
