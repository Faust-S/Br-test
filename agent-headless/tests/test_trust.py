"""No-network tests for optional CA trust. The fixture is a test-only CA."""
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from iwxa_obscura.trust import PinnedCABundle, CertificateTrustError

TEST_PEM = (Path(__file__).parent / "dummy-ca.pem").read_bytes()


class TestPinnedCABundle(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.source = self.root / "ca.pem"
        self.source.write_bytes(TEST_PEM)
        self.sha = hashlib.sha256(TEST_PEM).hexdigest()

    def profile(self, **kwargs):
        params = {"path": self.source, "sha256": self.sha,
                  "approval_ref": "AGENT-CA-TEST-001"}
        params.update(kwargs)
        return PinnedCABundle(**params)

    def test_valid_pem_is_copied_with_pinned_digest(self):
        destination = self.root / "isolated"
        destination.mkdir()
        staged = self.profile().stage(destination)
        self.assertEqual(staged.read_bytes(), TEST_PEM)
        self.assertEqual(hashlib.sha256(staged.read_bytes()).hexdigest(), self.sha)
        self.assertNotEqual(staged, self.source)
        self.assertEqual(staged.parent, destination)

    def test_reject_ca_replacement(self):
        profile = self.profile()
        self.source.write_bytes(TEST_PEM + b"\n")
        with self.assertRaisesRegex(CertificateTrustError, "CA_PIN_MISMATCH"):
            profile.stage(self.root)

    def test_reject_invalid_pem_with_valid_pin(self):
        self.source.write_bytes(b"-----BEGIN CERTIFICATE-----\n!!!!\n-----END CERTIFICATE-----\n")
        with self.assertRaisesRegex(CertificateTrustError, "CA_PEM_INVALID"):
            self.profile(sha256=hashlib.sha256(self.source.read_bytes()).hexdigest()).stage(self.root)

    def test_reject_private_key_pem_payload(self):
        self.source.write_bytes(TEST_PEM + b"\n-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----\n")
        with self.assertRaisesRegex(CertificateTrustError, "CA_PEM_INVALID"):
            self.profile(sha256=hashlib.sha256(self.source.read_bytes()).hexdigest()).stage(self.root)

    def test_reject_missing_ca(self):
        self.source.unlink()
        with self.assertRaisesRegex(CertificateTrustError, "CA_SOURCE_UNAVAILABLE"):
            self.profile().stage(self.root)

    def test_reject_symlink_ca(self):
        linked = self.root / "ca-link.pem"
        try:
            linked.symlink_to(self.source)
        except (OSError, NotImplementedError):
            self.skipTest("symlink unavailable")
        with self.assertRaisesRegex(CertificateTrustError, "CA_SOURCE_SYMLINK_DENIED"):
            self.profile(path=linked).stage(self.root)

    def test_reject_oversized_ca(self):
        self.source.write_bytes(TEST_PEM * 300)
        with self.assertRaisesRegex(CertificateTrustError, "CA_SOURCE_INVALID"):
            self.profile(sha256=hashlib.sha256(self.source.read_bytes()).hexdigest()).stage(self.root)

    def test_invalid_configuration(self):
        for options in (dict(sha256="nope"), dict(approval_ref=""),
                        dict(approval_ref="tool-supplied content with spaces"),
                        dict(path="relative.crt")):
            with self.subTest(options=options), self.assertRaises(ValueError):
                self.profile(**options)

    def test_default_root_store_is_unchanged(self):
        before = os.environ.get("SSL_CERT_FILE")
        directory = self.root / "isolated"
        directory.mkdir()
        self.profile().stage(directory)
        self.assertEqual(os.environ.get("SSL_CERT_FILE"), before)


if __name__ == "__main__":
    unittest.main()
