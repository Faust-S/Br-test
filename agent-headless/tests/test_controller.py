"""Public no-network Obscura adapter regression checks."""
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from iwxa_obscura.trust import PinnedCABundle
from iwxa_obscura import ObscuraController, ObscuraControllerError, ObscuraAgentToolPort

FAKE = """#!/usr/bin/env python3
import os,sys,time
from urllib.parse import urlsplit
a=sys.argv[1:]
assert a[0]=='fetch' and '--obey-robots' in a
assert '--eval' not in a and '--storage-dir' not in a
assert not os.getenv('OBSCURA_CDP_TOKEN') and not os.getenv('HTTPS_PROXY')
if urlsplit(a[1]).path=='/no-ca':assert 'SSL_CERT_FILE' not in os.environ
if urlsplit(a[1]).path=='/with-ca':
 assert os.environ.get('SSL_CERT_FILE')
 assert open(os.environ['SSL_CERT_FILE'],'rb').read().startswith(b'-----BEGIN CERTIFICATE-----')
path=urlsplit(a[1]).path
if path=='/slow':time.sleep(2)
if path=='/fail':sys.exit(1)
if path=='/empty':sys.exit(0)
if path=='/large':sys.stdout.write('X'*30000);sys.exit(0)
if path=='/stderr':sys.stderr.write('X'*30000);sys.exit(1)
if path=='/invalid':sys.stdout.buffer.write(bytes([0xff]));sys.exit(0)
sys.stdout.write('observed:'+a[1])
"""


@unittest.skipIf(os.name == "nt", "POSIX shebang fixture; actual native Windows worker is tested separately")
class ControllerTest(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.exe=Path(self.tmp.name)/'fake-obscura'
        self.exe.write_text(FAKE)
        self.exe.chmod(0o700)
        self.digest=hashlib.sha256(self.exe.read_bytes()).hexdigest()
        self.c=ObscuraController(binary=self.exe,binary_sha256=self.digest,
            allowed_origins=frozenset({'https://example.org'}),
            timeout_seconds=3,max_output_bytes=16384)

    def test_capture(self):
        x=self.c.observe('https://example.org/a?q=1#fragment')
        self.assertEqual(x.requested_url,'https://example.org/a?q=1')
        self.assertEqual(x.content,'observed:https://example.org/a?q=1')
        self.assertEqual(x.content_sha256,hashlib.sha256(x.content.encode()).hexdigest())
        self.assertIsNone(x.as_data()['http_status'])

    def test_denied_urls(self):
        for url in ('http://example.org','https://example.org.evil.com',
                'https://localhost/','https://127.0.0.1/',
                'https://user:pass@example.org/','https://example.org:8080/',
                'https://example.org/\n--eval','file:///etc/passwd'):
            with self.subTest(url=url),self.assertRaises(ObscuraControllerError):
                self.c.observe(url)

    def test_denied_formats(self):
        with self.assertRaisesRegex(ObscuraControllerError,'FORMAT_DENIED'):
            self.c.observe('https://example.org',format='cookies')

    def test_missing_binary(self):
        self.exe.unlink()
        with self.assertRaisesRegex(ObscuraControllerError,'ENGINE_MISSING'):
            self.c.observe('https://example.org')

    def test_tampered_binary(self):
        self.exe.write_text(FAKE+'#tampered')
        with self.assertRaisesRegex(ObscuraControllerError,'ENGINE_DIGEST_MISMATCH'):
            self.c.observe('https://example.org')

    def test_stdout_limit(self):
        with self.assertRaisesRegex(ObscuraControllerError,'ENGINE_OUTPUT_LIMIT'):
            self.c.observe('https://example.org/large')

    def test_stderr_limit(self):
        with self.assertRaisesRegex(ObscuraControllerError,'ENGINE_OUTPUT_LIMIT'):
            self.c.observe('https://example.org/stderr')

    def test_timeout(self):
        self.c.timeout_seconds = 1
        with self.assertRaisesRegex(ObscuraControllerError,'ENGINE_TIMEOUT'):
            self.c.observe('https://example.org/slow')

    def test_nonzero_exit(self):
        with self.assertRaisesRegex(ObscuraControllerError,'ENGINE_FETCH_FAILED'):
            self.c.observe('https://example.org/fail')

    def test_empty_result(self):
        with self.assertRaisesRegex(ObscuraControllerError,'ENGINE_EMPTY_OUTPUT'):
            self.c.observe('https://example.org/empty')

    def test_origin_configuration_and_canonicalization(self):
        from iwxa_obscura import approved_url
        self.assertEqual(approved_url('HTTPS://EXAMPLE.ORG:443/a#fragment',frozenset({'https://example.org'})),
                         'https://example.org/a')
        for raw in ('https://example.org:443','https://example.org?x=1',
                    'https://example.org/path','https://EXAMPLE.ORG',
                    'https://127.0.0.1','https://example.org.','https://user@example.org'):
            with self.subTest(raw=raw),self.assertRaises(ValueError):
                ObscuraController(binary=self.exe,binary_sha256=self.digest,
                    allowed_origins=frozenset({raw}))

    def test_non_utf8_rejected(self):
        with self.assertRaisesRegex(ObscuraControllerError,'ENGINE_NON_UTF8_OUTPUT'):
            self.c.observe('https://example.org/invalid')

    def test_elapsed_ms_metadata(self):
        observation=self.c.observe('https://example.org/')
        self.assertGreaterEqual(observation.elapsed_ms,0)
        self.assertEqual(observation.as_data()['elapsed_ms'],observation.elapsed_ms)

    def test_ambient_ca_environment_is_not_inherited(self):
        with mock.patch.dict(os.environ,{'SSL_CERT_FILE':'/tmp/unapproved-root.pem'}):
            self.assertTrue(self.c.observe('https://example.org/no-ca').content.startswith('observed:'))

    def test_environment_isolation(self):
        with mock.patch.dict(os.environ,{'OBSCURA_CDP_TOKEN':'secret','HTTPS_PROXY':'proxy'}):
            self.assertTrue(self.c.observe('https://example.org').content.startswith('observed:'))


    def test_opt_in_pinned_ca_is_present_only_for_selected_origin(self):
        pem=(Path(__file__).parent/"dummy-ca.pem")
        ca= PinnedCABundle(path=pem.resolve(),sha256=hashlib.sha256(pem.read_bytes()).hexdigest(),
                           approval_ref="AGENT-CA-TEST-001")
        secured=ObscuraController(binary=self.exe,binary_sha256=self.digest,
            allowed_origins=frozenset({"https://example.org","https://other.org"}),
            ca_bundles={"https://example.org":ca}, timeout_seconds=3,max_output_bytes=16384)
        result=secured.observe("https://example.org/with-ca").as_data()
        self.assertEqual(result["tls_trust"]["mode"],"PINNED_ADDITIONAL_CA")
        self.assertEqual(result["tls_trust"]["additional_ca_bundle_sha256"],ca.sha256)
        untrusted=secured.observe("https://other.org/no-ca").as_data()
        self.assertEqual(untrusted["tls_trust"]["mode"],"ENGINE_DEFAULT")
        self.assertIsNone(untrusted["tls_trust"]["additional_ca_bundle_sha256"])

    def test_pinned_ca_modification_fails_closed(self):
        pem=(Path(__file__).parent/"dummy-ca.pem")
        copied=Path(self.tmp.name)/"my-ca.pem"
        copied.write_bytes(pem.read_bytes())
        ca= PinnedCABundle(path=copied.resolve(),sha256=hashlib.sha256(copied.read_bytes()).hexdigest(),
                           approval_ref="AGENT-CA-TEST-001")
        secured=ObscuraController(binary=self.exe,binary_sha256=self.digest,
            allowed_origins=frozenset({"https://example.org"}),ca_bundles={"https://example.org":ca},
            timeout_seconds=3,max_output_bytes=16384)
        copied.write_bytes(copied.read_bytes()+b"\n")
        with self.assertRaisesRegex(ObscuraControllerError,"CA_PIN_MISMATCH"):
            secured.observe("https://example.org/with-ca")

    def test_unauthorized_ca_origin_mapping_fails(self):
        pem=(Path(__file__).parent/"dummy-ca.pem")
        ca= PinnedCABundle(path=pem.resolve(),sha256=hashlib.sha256(pem.read_bytes()).hexdigest(),
                           approval_ref="AGENT-CA-TEST-001")
        with self.assertRaises(ValueError):
            ObscuraController(binary=self.exe,binary_sha256=self.digest,
                allowed_origins=frozenset({"https://example.org"}),
                ca_bundles={"https://unapproved.org":ca})


@unittest.skipIf(os.name == "nt", "POSIX shebang fixture; actual native Windows worker is tested separately")
class BridgeTest(unittest.TestCase):
    def setUp(self):
        ControllerTest.setUp(self)
        class PortError(Exception):
            def __init__(self,code,detail):
                super().__init__(code+':'+detail)
        class Result:
            @classmethod
            def from_adapter_result(cls,raw,*,request):
                return raw
        module=types.ModuleType('providers.tool_provider_port')
        module.ToolProviderPortError=PortError
        module.ToolProviderResult=Result
        provider=types.ModuleType('providers')
        originals={k:sys.modules.get(k) for k in ('providers','providers.tool_provider_port')}
        sys.modules.update({'providers':provider,'providers.tool_provider_port':module})
        def restore():
            for k,v in originals.items():
                if v is None:sys.modules.pop(k,None)
                else:sys.modules[k]=v
        self.addCleanup(restore)
        self.error=PortError
        self.bridge=ObscuraAgentToolPort(self.c,provider_id='engine',binding_digest='f'*64,descriptor_digest='e'*64)

    def request(self,**changes):
        values={'logical_provider_id':'engine',
            'tool_descriptor_ref':{'tool_id':'web.fetch',
                'provider_slot':'web_acquisition','provider_binding_digest':'f'*64,
                'descriptor_digest':'e'*64},
            'operation':'web.fetch','target':None,
            'arguments':{'url':'https://example.org'},
            'request_id':'R1','idempotency_key_or_none':None,
            'tool_plan_digest_or_none':None,'validate':lambda:True}
        values.update(changes)
        return types.SimpleNamespace(**values)

    def test_read_contract(self):
        result=self.bridge.invoke(self.request())
        self.assertEqual(result['external_effect_state'],'NONE')
        self.assertEqual(result['status'],200)
        self.assertTrue(result['data']['content_sha256'])

    def test_no_consequential_request(self):
        for args in ({'idempotency_key_or_none':'abc'},
                     {'target':'anything'},
                     {'arguments':{'url':'https://example.org','eval':'1+1'}},
                     {'tool_plan_digest_or_none':'f'*64}):
            with self.subTest(args=args),self.assertRaises(self.error):
                self.bridge.invoke(self.request(**args))

    def test_descriptor_binding_denied(self):
        request=self.request()
        request.tool_descriptor_ref['descriptor_digest']='0'*64
        with self.assertRaises(self.error):
            self.bridge.invoke(request)

    def test_other_provider_denied(self):
        with self.assertRaises(self.error):
            self.bridge.invoke(self.request(logical_provider_id='other'))

    def test_delegate_preserves_other_provider(self):
        delegate=mock.Mock()
        expected=object()
        delegate.invoke.return_value=expected
        bridge=ObscuraAgentToolPort(self.c,provider_id='engine',
            binding_digest='f'*64,descriptor_digest='e'*64,delegate=delegate)
        self.assertIs(bridge.invoke(self.request(logical_provider_id='other')),expected)
        delegate.invoke.assert_called_once()


if __name__=='__main__':
    unittest.main()
