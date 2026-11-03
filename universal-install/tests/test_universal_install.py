"""Offline on-disk universal installer tests against the public Obscura preparer.

Fixtures are synthetic bytes. They are NOT a qualified native Obscura Worker.
No private Agent source, shell installer, user profile, or network is required.
"""
from __future__ import annotations

import hashlib
import io
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "universal-install"))
sys.path.insert(0, str(ROOT / "agent-headless"))

from iwxa_obscura.preparer import ArtifactSpec, GUARD_PROTOCOL, UPSTREAM_COMMIT
from iwxa_universal_install import ApprovedInstall, InstallError, ObscuraManagedBackend
from iwxa_universal_install.obscura import _native_platform

PAYLOAD = b"MZ - synthetic integration fixture, NOT native Obscura"
RELEASE = "v0.2.6-iwxa.1-test"
HOST_PLATFORM = _native_platform()


class PortableInstallTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.staging = self.root / "staging"
        self.destination = self.root / "isolated-product-software"
        self.backend = ObscuraManagedBackend()

    def package(self, platform=None, payload=PAYLOAD, release=RELEASE):
        platform = platform or HOST_PLATFORM
        name = "obscura-worker.exe" if platform.startswith("windows") else "obscura-worker"
        extension = ".zip" if platform.startswith("windows") else ".tar.gz"
        archive_name = "obscura-" + platform + extension
        archive = self.root / (platform + extension)
        if extension == ".zip":
            with zipfile.ZipFile(archive, "w") as z:
                z.writestr(name, payload)
        else:
            with tarfile.open(archive, "w:gz") as t:
                record = tarfile.TarInfo(name)
                record.size = len(payload)
                t.addfile(record, io.BytesIO(payload))
        spec = ArtifactSpec(
            release_id=release, platform=platform,
            archive_sha256=hashlib.sha256(archive.read_bytes()).hexdigest(),
            executable_sha256=hashlib.sha256(payload).hexdigest(),
            download_url="https://github.com/Faust-S/Br-test/releases/download/" + release + "/" + archive_name,
            archive_name=archive_name, archive_format="zip" if extension == ".zip" else "tar.gz",
            source_commit=UPSTREAM_COMMIT, origin_guard_patch_sha256="e" * 64,
            guard_protocol=GUARD_PROTOCOL,
        )
        return archive, spec

    def approval(self, spec, *, allowed=True, fingerprint=None, installation_root=None):
        return ApprovedInstall(
            program_id=self.backend.program_id,
            spec_fingerprint=fingerprint or spec.fingerprint,
            receipt_id="PINS-test-0001",
            installation_root=str(installation_root or self.destination),
            confirmed=allowed,
        )

    def install_fixture(self, platform=None):
        archive, spec = self.package(platform)
        prepared = self.backend.prepare(spec, str(self.staging), archive_path=str(archive))
        installed = self.backend.apply(spec, str(self.staging), self.approval(spec))
        return spec, prepared, installed

    def test_public_contract_and_host_install(self):
        for name in ("inspect", "prepare", "apply", "verify", "recover"):
            self.assertTrue(callable(getattr(self.backend, name)))
        self.assertEqual(self.backend.family, "IWXA_MANAGED_ARTIFACT_V1")
        spec, prepared, result = self.install_fixture()
        self.assertEqual(prepared.status, "STAGED_NOT_ACTIVATED")
        self.assertEqual(result.status, "INSTALLED_NOT_CONNECTED")
        self.assertTrue(result.installed)
        self.assertFalse(result.connected)
        self.assertFalse(result.receipt_written)
        self.assertFalse(result.external_process_started)
        self.assertEqual(Path(result.executable).read_bytes(), PAYLOAD)
        self.assertEqual(result.sha256, hashlib.sha256(PAYLOAD).hexdigest())
        self.assertNotEqual(Path(result.executable), Path(prepared.executable))
        self.assertEqual(self.backend.recover(spec, str(self.destination)), result)

    def test_unix_tarball_install(self):
        if HOST_PLATFORM.startswith("windows"):
            self.skipTest("Windows uses ZIP, not tar.gz")
        spec, prepared, installed = self.install_fixture(HOST_PLATFORM)
        self.assertEqual(installed.status, "INSTALLED_NOT_CONNECTED")
        self.assertEqual(installed.sha256, prepared.executable_sha256)
        self.assertEqual(Path(installed.executable).read_bytes(), PAYLOAD)

    def test_staging_alone_never_counts_as_installed(self):
        archive, spec = self.package()
        self.backend.prepare(spec, str(self.staging), archive_path=str(archive))
        self.assertEqual(self.backend.inspect(spec, str(self.destination)).status, "NOT_INSTALLED")
        self.assertFalse(self.destination.exists())

    def test_requires_host_approval_and_matching_plan(self):
        archive, spec = self.package()
        self.backend.prepare(spec, str(self.staging), archive_path=str(archive))
        for approval, error in [
            (self.approval(spec, allowed=False), "INSTALL_OWNER_APPROVAL_REQUIRED"),
            (self.approval(spec, fingerprint="f" * 64), "INSTALL_APPROVAL_MISMATCH"),
            (ApprovedInstall("WRONG_PROGRAM", spec.fingerprint, "PINS-test-0001", str(self.destination), True),
             "INSTALL_APPROVAL_MISMATCH"),
        ]:
            with self.subTest(error=error), self.assertRaisesRegex(InstallError, error):
                self.backend.apply(spec, str(self.staging), approval)
        self.assertFalse(self.destination.exists())

    def test_install_and_staging_root_must_differ(self):
        archive, spec = self.package()
        self.backend.prepare(spec, str(self.staging), archive_path=str(archive))
        with self.assertRaisesRegex(InstallError, "INSTALL_STAGING_EQUALS_TARGET"):
            self.backend.apply(spec, str(self.staging),
                               self.approval(spec, installation_root=self.staging))

    def test_recover_never_installs_or_downloads(self):
        archive, spec = self.package()
        self.backend.prepare(spec, str(self.staging), archive_path=str(archive))
        self.assertEqual(self.backend.recover(spec, str(self.destination)).status, "NOT_INSTALLED")
        self.assertFalse(self.destination.exists())
        self.backend.apply(spec, str(self.staging), self.approval(spec))
        self.assertTrue(self.backend.recover(spec, str(self.destination)).installed)

    def test_tampered_installed_file_is_not_adopted_or_overwritten(self):
        spec, _, installed = self.install_fixture()
        os.chmod(installed.executable, 0o600)
        Path(installed.executable).write_bytes(b"tampered")
        status = self.backend.recover(spec, str(self.destination))
        self.assertEqual(status.status, "INSTALL_MISMATCH")
        self.assertFalse(status.installed)
        with self.assertRaisesRegex(InstallError, "INSTALL_TARGET_CONFLICT"):
            self.backend.apply(spec, str(self.staging), self.approval(spec))
        self.assertEqual(Path(installed.executable).read_bytes(), b"tampered")

    def test_modified_stage_rejected_before_install(self):
        archive, spec = self.package()
        prepared = self.backend.prepare(spec, str(self.staging), archive_path=str(archive))
        os.chmod(prepared.executable, 0o600)
        Path(prepared.executable).write_bytes(b"modified stage")
        with self.assertRaises(Exception):
            self.backend.apply(spec, str(self.staging), self.approval(spec))
        self.assertEqual(self.backend.verify(spec, str(self.destination)).status, "NOT_INSTALLED")

    def test_existing_versions_remain_side_by_side(self):
        spec, _, initial = self.install_fixture()
        archive, second_spec = self.package(release="v0.2.6-iwxa.2-test")
        self.backend.prepare(second_spec, str(self.staging), archive_path=str(archive))
        another = self.backend.apply(second_spec, str(self.staging), self.approval(second_spec))
        self.assertTrue(self.backend.verify(spec, str(self.destination)).installed)
        self.assertTrue(self.backend.verify(second_spec, str(self.destination)).installed)
        self.assertNotEqual(initial.executable, another.executable)

    def test_idempotent_and_interrupted_install_no_auto_retry(self):
        spec, _, first = self.install_fixture()
        self.assertEqual(self.backend.apply(spec, str(self.staging), self.approval(spec)), first)
        archive, spec2 = self.package(release="v0.2.6-iwxa.3-test")
        folder = self.destination / "obscura-worker" / spec2.release_id / spec2.platform
        folder.mkdir(parents=True)
        self.assertEqual(self.backend.recover(spec2, str(self.destination)).status, "INSTALL_INCOMPLETE")
        with self.assertRaisesRegex(InstallError, "INSTALL_TARGET_CONFLICT"):
            self.backend.apply(spec2, str(self.staging), self.approval(spec2))
        self.assertFalse((folder / spec2.executable_name).exists())

    def test_invalid_root_fails_before_writes(self):
        archive, spec = self.package()
        self.backend.prepare(spec, str(self.staging), archive_path=str(archive))
        with self.assertRaisesRegex(InstallError, "INSTALL_ROOT_INVALID"):
            self.backend.apply(spec, str(self.staging),
                               self.approval(spec, installation_root="relative/root"))
        self.assertFalse(self.destination.exists())



    def test_nested_staging_and_installation_roots_are_rejected(self):
        for nested_stage in (True, False):
            with self.subTest(nested_stage=nested_stage):
                root = self.root / ("nested-stage" if nested_stage else "nested-install")
                destination = root / "product"
                stage = destination / "staging" if nested_stage else root / "staging"
                if not nested_stage:
                    destination = stage / "product"
                archive, spec = self.package()
                self.backend.prepare(spec, str(stage), archive_path=str(archive))
                with self.assertRaisesRegex(InstallError, "INSTALL_STAGING_EQUALS_TARGET"):
                    self.backend.apply(spec, str(stage),
                                       self.approval(spec, installation_root=destination))
                self.assertFalse(
                    (destination / "obscura-worker" / spec.release_id / spec.platform).exists()
                )

    def test_missing_prepared_payload_does_not_write_to_product(self):
        _, spec = self.package()
        with self.assertRaisesRegex(InstallError, "INSTALL_PREPARED_UNAVAILABLE"):
            self.backend.apply(spec, str(self.staging), self.approval(spec))
        self.assertFalse(self.destination.exists())

    def test_unwritable_destination_is_stable_install_error(self):
        from unittest import mock
        archive, spec = self.package()
        self.backend.prepare(spec, str(self.staging), archive_path=str(archive))
        with mock.patch("iwxa_universal_install.obscura.Path.mkdir",
                        side_effect=PermissionError("fixture denied")):
            with self.assertRaisesRegex(InstallError, "INSTALL_DESTINATION_UNAVAILABLE"):
                self.backend.apply(spec, str(self.staging), self.approval(spec))
        self.assertEqual(self.backend.recover(spec, str(self.destination)).status,
                         "NOT_INSTALLED")

    def test_symlink_in_installed_path_is_rejected(self):
        archive, spec = self.package()
        self.backend.prepare(spec, str(self.staging), archive_path=str(archive))
        destination = self.destination / "obscura-worker"
        destination.parent.mkdir(parents=True)
        try:
            destination.symlink_to(self.root, target_is_directory=True)
        except (OSError, NotImplementedError):
            self.skipTest("directory symlink unavailable on this runner")
        with self.assertRaisesRegex(InstallError, "ARTIFACT_PATH_REPARSE_DENIED"):
            self.backend.recover(spec, str(self.destination))
        with self.assertRaisesRegex(InstallError, "ARTIFACT_PATH_REPARSE_DENIED"):
            self.backend.apply(spec, str(self.staging), self.approval(spec))

    def test_all_six_platform_artifact_shapes(self):
        cases = (("windows-x86_64", ".zip"), ("windows-arm64", ".zip"),
                 ("linux-x86_64", ".tar.gz"), ("linux-arm64", ".tar.gz"),
                 ("macos-x86_64", ".tar.gz"), ("macos-arm64", ".tar.gz"))
        for platform, suffix in cases:
            with self.subTest(platform=platform):
                archive, spec = self.package(platform)
                self.assertTrue(archive.name.endswith(suffix))
                prepared = self.backend.prepare(
                    spec, str(self.staging / platform), archive_path=str(archive))
                if platform != HOST_PLATFORM:
                    with self.assertRaisesRegex(InstallError, "INSTALL_PLATFORM_MISMATCH"):
                        self.backend.apply(
                            spec, str(self.staging / platform),
                            self.approval(spec, installation_root=self.destination / platform))
                    with self.assertRaisesRegex(InstallError, "INSTALL_PLATFORM_MISMATCH"):
                        self.backend.recover(spec, str(self.destination / platform))
                    self.assertFalse((self.destination / platform).exists())
                else:
                    installed = self.backend.apply(
                        spec, str(self.staging / platform),
                        self.approval(spec, installation_root=self.destination / platform))
                    self.assertEqual(installed.sha256, prepared.executable_sha256)
                    self.assertTrue(self.backend.recover(spec, str(self.destination / platform)).installed)

    def test_reject_mismatched_os_archive_format(self):
        archive, spec = self.package("linux-x86_64")
        for candidate in ("linux-x86_64", "linux-arm64", "macos-x86_64", "macos-arm64"):
            mapping = {**spec.as_dict(), "platform": candidate, "archive_format": "zip"}
            with self.subTest(platform=candidate), self.assertRaises(ValueError):
                ArtifactSpec(**mapping)
        for candidate in ("windows-x86_64", "windows-arm64"):
            mapping = {**spec.as_dict(), "platform": candidate, "archive_format": "tar.gz"}
            with self.subTest(platform=candidate), self.assertRaises(ValueError):
                ArtifactSpec(**mapping)

    def test_unicode_and_spaces_in_product_folder(self):
        archive, spec = self.package()
        self.backend.prepare(spec, str(self.staging), archive_path=str(archive))
        destination = self.root / "User Programs" / "Тест установки IWXA"
        installed = self.backend.apply(
            spec, str(self.staging), self.approval(spec, installation_root=destination))
        self.assertTrue(installed.installed)
        self.assertEqual(self.backend.recover(spec, str(destination)), installed)

    def test_recheck_after_deleted_binary_does_not_claim_installed(self):
        spec, _, installed = self.install_fixture()
        os.chmod(installed.executable, 0o600)
        Path(installed.executable).unlink()
        self.assertEqual(self.backend.recover(spec, str(self.destination)).status,
                         "INSTALL_INCOMPLETE")
        with self.assertRaisesRegex(InstallError, "INSTALL_TARGET_CONFLICT"):
            self.backend.apply(spec, str(self.staging), self.approval(spec))


    def test_disk_full_after_write_leaves_no_claim_of_installation(self):
        # fsync failure emulates exhausted storage after a file was created.
        from unittest import mock
        archive, spec = self.package()
        self.backend.prepare(spec, str(self.staging), archive_path=str(archive))
        with mock.patch("iwxa_universal_install.obscura.os.fsync",
                        side_effect=OSError("simulated disk full")):
            with self.assertRaisesRegex(InstallError, "INSTALL_WRITE_FAILED"):
                self.backend.apply(spec, str(self.staging), self.approval(spec))
        self.assertEqual(self.backend.recover(spec, str(self.destination)).status,
                         "NOT_INSTALLED")

    def test_directory_traversal_in_requested_install_root_is_denied(self):
        archive, spec = self.package()
        self.backend.prepare(spec, str(self.staging), archive_path=str(archive))
        root_with_dotdot = self.destination / "child" / ".."
        with self.assertRaisesRegex(InstallError, "INSTALL_ROOT_INVALID"):
            self.backend.apply(
                spec, str(self.staging),
                self.approval(spec, installation_root=root_with_dotdot))
        self.assertFalse(self.destination.exists())

if __name__ == "__main__":
    unittest.main()
