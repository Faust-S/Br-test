"""Contract and isolation tests for the native package backend.

Mocks never perform host system installation. Real native smoke tests use a
separate opt-in CI job with disposable package identities and cleanup.
"""
import hashlib
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "universal-install"))
from iwxa_universal_install import (
    ApprovedInstall, ApprovedRemoval, InstallError, ManagedArtifactBackend,
    ManagedArtifactSpec, NativePackageBackend, NativePackageSpec
)
from iwxa_universal_install.managed import _native_platform
from iwxa_universal_install.native import _command


class NativePackageContractTest(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name).resolve()
        self.platform = _native_platform()
        self.fmt = ("msi" if self.platform.startswith("windows") else
                    "pkg" if self.platform.startswith("macos") else "deb")
        self.package = self.root / ("test-package." + self.fmt)
        self.package.write_bytes(b"native-payload-mock-fixture")
        sha = hashlib.sha256(self.package.read_bytes()).hexdigest()
        self.artifact = ManagedArtifactSpec(
            program_id="NATIVE_FIXTURE_V1", slug="native-test",
            release_id="v1", platform=self.platform,
            executable_name=self.package.name, archive_name=self.package.name,
            archive_format=self.fmt, archive_sha256=sha, executable_sha256=sha,
            download_url="https://github.com/Faust-S/Br-test/releases/download/v1/" + self.package.name,
        )
        self.spec = NativePackageSpec(
            self.artifact, "{11111111-1111-1111-1111-111111111111}" if self.fmt == "msi"
            else "iwxa-native-test", installed_version="1.0.0"
        )
        self.backend = NativePackageBackend()
        self.staging = str(self.root / "staging")
        self.install = str(self.root / "host-receipt-root")
        self.approval = ApprovedInstall(self.spec.program_id, self.spec.fingerprint,
                                        "PINS-native-0001", self.install, True)
        self.removal = ApprovedRemoval(self.spec.program_id, self.spec.fingerprint,
                                       "PINS-native-remove", self.install, True)

    def test_native_manifest_roundtrip_requires_only_data(self):
        data = self.spec.as_dict()
        rebuilt = NativePackageSpec.from_mapping(data)
        self.assertEqual(rebuilt, self.spec)
        self.assertEqual(rebuilt.fingerprint, self.spec.fingerprint)
        self.assertIsInstance(rebuilt.artifact, ManagedArtifactSpec)
        for invalid in ({**data, "unknown_field": 1},
                        {**data, "probe_arguments": "not-an-array"}):
            with self.assertRaises(ValueError):
                NativePackageSpec.from_mapping(invalid)

    def test_native_stages_and_preserves_pinned_package(self):
        events = []
        result = self.backend.prepare(self.spec, self.staging, archive_path=str(self.package),
                                      on_progress=lambda n,total: events.append((n,total)))
        self.assertEqual(result.status, "STAGED_NOT_ACTIVATED")
        self.assertEqual(Path(result.executable).read_bytes(), self.package.read_bytes())
        self.assertEqual(events, [(1,1)])
        self.assertFalse((self.root / "host-receipt-root").exists())

    def test_native_never_runs_through_portable_copy_backend(self):
        with self.assertRaisesRegex(InstallError, "INSTALL_NATIVE_BACKEND_REQUIRED"):
            ManagedArtifactBackend().prepare(self.artifact, self.staging,
                                             archive_path=str(self.package))

    def test_damaged_package_rejected_before_execution(self):
        self.backend.prepare(self.spec, self.staging, archive_path=str(self.package))
        binary = Path(self.staging) / self.artifact.slug / self.artifact.release_id / self.platform / self.artifact.executable_name
        binary.chmod(0o700)
        binary.write_bytes(b"changed")
        with mock.patch("iwxa_universal_install.native._run") as runner:
            with self.assertRaises(Exception):
                self.backend.apply(self.spec, self.staging, self.approval)
            runner.assert_not_called()

    def test_owner_authorization_mandatory(self):
        with self.assertRaisesRegex(InstallError, "INSTALL_OWNER_APPROVAL_REQUIRED"):
            self.backend.apply(self.spec, self.staging,
                ApprovedInstall(self.spec.program_id,self.spec.fingerprint,"PINS-native-0001",self.install,False))
        with self.assertRaisesRegex(InstallError, "REMOVE_OWNER_APPROVAL_REQUIRED"):
            self.backend.remove(self.spec,
                ApprovedRemoval(self.spec.program_id,self.spec.fingerprint,"PINS-native-remove",self.install,False))

    def test_native_receipt_id_checked_before_install_or_remove(self):
        for bad in ("", "../unsafe", "x" * 129):
            bad_install = ApprovedInstall(
                self.spec.program_id, self.spec.fingerprint, bad, self.install, True)
            with self.subTest(bad=bad), self.assertRaisesRegex(InstallError, "INSTALL_APPROVAL_MISMATCH"):
                self.backend.apply(self.spec, self.staging, bad_install)
            bad_remove = ApprovedRemoval(
                self.spec.program_id, self.spec.fingerprint, bad, self.install, True)
            with self.subTest(bad=bad), self.assertRaisesRegex(InstallError, "REMOVE_APPROVAL_MISMATCH"):
                self.backend.remove(self.spec, bad_remove)

    def test_exe_uninstall_waits_for_completion_without_relaunch(self):
        from iwxa_universal_install.native import NativePackageBackend, NativePackageSpec
        mapping = {**self.artifact.as_dict(),
                   "platform": "windows-x86_64", "archive_format": "exe-installer",
                   "archive_name": "vendor.exe", "executable_name": "vendor.exe",
                   "download_url": "https://github.com/Faust-S/Br-test/releases/download/v1/vendor.exe"}
        spec = NativePackageSpec(
            ManagedArtifactSpec.from_mapping(mapping), "fixture-exe",
            install_arguments=("/S",), uninstall_arguments=(str(Path(sys.executable).resolve()), "/S"),
            probe_arguments=(str(Path(sys.executable).resolve()), "--version"))
        # Exercise the real backend on the current runner by substituting only
        # native platform detection and subprocess responses.
        with mock.patch("iwxa_universal_install.native._native_platform",
                        return_value="windows-x86_64"), \
             mock.patch("iwxa_universal_install.native._probe",
                        side_effect=[True, True, True, False, False]), \
             mock.patch("iwxa_universal_install.native._run") as runner, \
             mock.patch("iwxa_universal_install.native.time.sleep") as sleeper:
            runner.return_value.returncode = 0
            approved = ApprovedRemoval(spec.program_id, spec.fingerprint,
                                      "PINS-exe-remove", self.install, True)
            self.assertEqual(NativePackageBackend().remove(spec, approved).status, "REMOVED")
            runner.assert_called_once()
            self.assertGreaterEqual(sleeper.call_count, 1)

    def test_native_install_requires_post_install_probe(self):
        self.backend.prepare(self.spec, self.staging, archive_path=str(self.package))
        with mock.patch("iwxa_universal_install.native._run") as runner, mock.patch(
            "iwxa_universal_install.native._probe", return_value=False
        ):
            runner.return_value.returncode = 0
            with self.assertRaisesRegex(InstallError, "NATIVE_INSTALL_NOT_VERIFIED"):
                self.backend.apply(self.spec, self.staging, self.approval)
            runner.assert_called_once()

    def test_native_install_and_remove_round_trip_probe(self):
        self.backend.prepare(self.spec, self.staging, archive_path=str(self.package))
        with mock.patch("iwxa_universal_install.native._run") as runner, mock.patch(
            "iwxa_universal_install.native._probe", side_effect=[True, True, False]
        ):
            runner.return_value.returncode = 0
            self.assertTrue(self.backend.apply(self.spec, self.staging, self.approval).installed)
            if self.fmt == "pkg":
                with self.assertRaisesRegex(InstallError, "REMOVE_NATIVE_PKG_UNSUPPORTED"):
                    self.backend.remove(self.spec, self.removal)
            else:
                self.assertEqual(self.backend.remove(self.spec, self.removal).status, "REMOVED")
                self.assertEqual(runner.call_count, 2)

    def test_format_platform_and_hash_consistency(self):
        wrong = {**self.artifact.as_dict(), "archive_format": "binary"}
        if self.platform.startswith("windows"):
            with self.assertRaisesRegex(ValueError, "MANAGED_SPEC_WINDOWS_EXECUTABLE_INVALID"):
                ManagedArtifactSpec.from_mapping(wrong)
        else:
            self.assertEqual(ManagedArtifactSpec.from_mapping(wrong).archive_format, "binary")
        bad = {**self.artifact.as_dict(), "executable_sha256": "f" * 64}
        with self.assertRaisesRegex(ValueError, "MANAGED_SPEC_NATIVE_FORMAT_INVALID"):
            ManagedArtifactSpec.from_mapping(bad)
        bad = {**self.artifact.as_dict(), "platform":
               "linux-x86_64" if self.platform.startswith("windows") else "windows-x86_64"}
        with self.assertRaises(ValueError):
            ManagedArtifactSpec.from_mapping(bad)

    def test_all_native_command_shapes(self):
        specs = [
            ("msi", "windows-x86_64", "sample.msi", "{11111111-1111-1111-1111-111111111111}"),
            ("deb", "linux-x86_64", "sample.deb", "sample"),
            ("rpm", "linux-x86_64", "sample.rpm", "sample"),
            ("pkg", "macos-arm64", "sample.pkg", "org.example.sample"),
            ("exe-installer", "windows-arm64", "setup.exe", "sample"),
        ]
        for fmt, plat, filename, package_id in specs:
            with self.subTest(fmt=fmt):
                mapping = {**self.artifact.as_dict(),
                           "platform":plat, "archive_format":fmt,
                           "executable_name":filename,"archive_name":filename,
                           "download_url":"https://github.com/Faust-S/Br-test/releases/download/v1/" + filename}
                artifact = ManagedArtifactSpec.from_mapping(mapping)
                if fmt == "exe-installer":
                    obj = NativePackageSpec(artifact, package_id,
                        install_arguments=("/S",),
                        probe_arguments=(str(Path(sys.executable).resolve()), "--version"))
                else:
                    obj = NativePackageSpec(artifact, package_id,
                        installed_version="1.0.0-1" if fmt == "rpm" else "1.0.0")
                command = _command(obj, "install", Path("/tmp/pinned-package"))
                self.assertTrue(command)
                self.assertNotIn("sh", command[0])
                if fmt == "pkg":
                    with self.assertRaisesRegex(InstallError, "REMOVE_NATIVE_PKG_UNSUPPORTED"):
                        _command(obj, "remove")
                elif fmt == "exe-installer":
                    with self.assertRaisesRegex(InstallError, "REMOVE_VENDOR_COMMAND_REQUIRED"):
                        _command(obj, "remove")
                else:
                    self.assertTrue(_command(obj, "remove"))

    def test_pkg_uninstall_requires_explicit_vendor_command(self):
        pkg = {**self.artifact.as_dict(), "platform": "macos-arm64",
               "archive_format": "pkg", "archive_name": "vendor.pkg",
               "executable_name": "vendor.pkg",
               "download_url": "https://github.com/Faust-S/Br-test/releases/download/v1/vendor.pkg"}
        artifact = ManagedArtifactSpec.from_mapping(pkg)
        legacy = NativePackageSpec(artifact, "org.example.vendor", installed_version="1.0.0")
        with self.assertRaisesRegex(InstallError, "REMOVE_NATIVE_PKG_UNSUPPORTED"):
            _command(legacy, "remove")
        vendor = NativePackageSpec(
            artifact, "org.example.vendor", installed_version="1.0.0",
            uninstall_arguments=(str(Path(sys.executable).resolve()), "vendor-uninstall.py"))
        self.assertEqual(_command(vendor, "remove"), list(vendor.uninstall_arguments))
        self.assertEqual(NativePackageSpec.from_mapping(vendor.as_dict()), vendor)
        with self.assertRaisesRegex(ValueError, "NATIVE_SPEC_COMMAND_PATH_INVALID"):
            NativePackageSpec(artifact, "org.example.vendor", installed_version="1.0.0",
                              uninstall_arguments=("relative/vendor-uninstall.sh",))

    def test_generic_native_adapter_has_no_obscura_dependency(self):
        self.assertIsNotNone(NativePackageBackend)
        self.assertNotIn("iwxa_obscura", NativePackageBackend.__module__)


if __name__ == "__main__":
    unittest.main()
