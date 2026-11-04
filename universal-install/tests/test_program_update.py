"""Versioned managed updates. Filesystem fixtures are NOT actual vendor releases."""
from __future__ import annotations

from dataclasses import replace
import hashlib
import os
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "universal-install"))
from iwxa_universal_install import (
    ApprovedInstall, ApprovedRemoval, ApprovedUpdate, InstallError,
    ManagedArtifactBackend, ManagedArtifactSpec, UpdateObservation,
)
from iwxa_universal_install.managed import _native_platform


class ManagedUpdateTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name).resolve()
        self.stage = self.root / "stage"
        self.software = self.root / "software"
        self.software.mkdir()
        self.user_data = self.software / "user-data.txt"
        self.user_data.write_text("KEEP", encoding="utf-8")
        self.backend = ManagedArtifactBackend()
        self.old, self.old_source = self.package("1.0.0", b"version-one-fixture")
        self.new, self.new_source = self.package("2.0.0", b"version-two-fixture")

    def package(self, release, content, *, program_id="EXAMPLE_TOOL_V1", slug="example"):
        name = "example.exe" if os.name == "nt" else "example"
        archive_name = f"example-{release}.bin"
        source = self.root / archive_name
        source.write_bytes(content)
        digest = hashlib.sha256(content).hexdigest()
        spec = ManagedArtifactSpec(
            program_id=program_id, slug=slug, release_id=release,
            platform=_native_platform(), executable_name=name,
            archive_name=archive_name, archive_format="binary",
            archive_sha256=digest, executable_sha256=digest,
            download_url=f"https://github.com/Faust-S/Br-test/releases/download/{release}/{archive_name}",
        )
        return spec, source

    def install_old(self):
        self.backend.prepare(self.old, str(self.stage), archive_path=str(self.old_source))
        observation = self.backend.apply(
            self.old, str(self.stage),
            ApprovedInstall(self.old.program_id, self.old.fingerprint,
                            "PINS-update-first", str(self.software), True),
        )
        self.assertTrue(observation.installed)
        return observation

    def approve(self, **changes):
        values = dict(program_id=self.old.program_id,
                      current_fingerprint=self.old.fingerprint,
                      target_fingerprint=self.new.fingerprint,
                      receipt_id="PINS-update-second",
                      installation_root=str(self.software),
                      confirmed=True)
        return ApprovedUpdate(**(values | changes))

    def assert_preserved(self):
        self.assertEqual(self.user_data.read_text(encoding="utf-8"), "KEEP")
        self.assertEqual(self.backend.verify(self.old, str(self.software)).sha256,
                         self.old.executable_sha256)

    def test_side_by_side_update_and_host_switch_after_readback(self):
        before = self.install_old()
        self.assertEqual(self.backend.plan_update(self.old, self.new, str(self.software)).status,
                         "UPDATE_AVAILABLE")
        self.backend.prepare(self.new, str(self.stage), archive_path=str(self.new_source))
        active = self.software / "host-selected-version.txt"
        active.write_text(self.old.release_id, encoding="utf-8")
        ready = self.backend.apply_update(self.old, self.new, str(self.stage), self.approve())
        self.assertIsInstance(ready, UpdateObservation)
        self.assertEqual(ready.status, "UPDATE_READY_FOR_ACTIVATION")
        self.assertTrue(ready.current_installed and ready.target_installed)
        self.assertTrue(ready.rollback_available)
        self.assertFalse(ready.activated or ready.receipt_written)
        self.assertEqual(active.read_text(encoding="utf-8"), self.old.release_id)
        self.assertEqual(Path(ready.current_executable).read_bytes(), b"version-one-fixture")
        self.assertEqual(Path(ready.target_executable).read_bytes(), b"version-two-fixture")
        self.assertNotEqual(ready.current_executable, ready.target_executable)
        self.assertEqual(self.backend.verify(self.old, str(self.software)), before)
        self.assert_preserved()
        restarted = ManagedArtifactBackend()
        self.assertEqual(restarted.recover_update(self.old, self.new, str(self.software)), ready)
        active.write_text(self.new.release_id, encoding="utf-8")  # Only the host switches.
        self.assertEqual(active.read_text(encoding="utf-8"), self.new.release_id)
        active.write_text(self.old.release_id, encoding="utf-8")  # Host rollback selection.
        self.assert_preserved()
        self.assertEqual(restarted.apply_update(self.old, self.new,
                                                str(self.stage), self.approve()), ready)

    def test_no_authorization_no_update(self):
        self.install_old()
        self.backend.prepare(self.new, str(self.stage), archive_path=str(self.new_source))
        for approval, code in (
            (self.approve(confirmed=False), "UPDATE_OWNER_APPROVAL_REQUIRED"),
            (self.approve(current_fingerprint="0" * 64), "UPDATE_APPROVAL_MISMATCH"),
            (self.approve(target_fingerprint="f" * 64), "UPDATE_APPROVAL_MISMATCH"),
            (self.approve(program_id="FOREIGN_PROGRAM"), "UPDATE_APPROVAL_MISMATCH"),
        ):
            with self.subTest(code=code), self.assertRaisesRegex(InstallError, code):
                self.backend.apply_update(self.old, self.new, str(self.stage), approval)
            self.assertEqual(self.backend.verify(self.new, str(self.software)).status,
                             "NOT_INSTALLED")
        self.assert_preserved()

    def test_wrong_program_and_release_collision_rejected(self):
        self.install_old()
        for target in (
            replace(self.new, slug="foreign"),
            replace(self.new, program_id="OTHER_PROGRAM"),
        ):
            with self.assertRaisesRegex(InstallError, "UPDATE_PROGRAM_MISMATCH"):
                self.backend.plan_update(self.old, target, str(self.software))
        with self.assertRaisesRegex(InstallError, "UPDATE_RELEASE_UNCHANGED"):
            self.backend.plan_update(self.old, replace(self.new, release_id=self.old.release_id,
                download_url=self.new.download_url.replace("/2.0.0/", "/1.0.0/")),
                str(self.software))

    def test_interrupted_install_and_corrupt_new_release_never_change_old(self):
        self.install_old()
        self.backend.prepare(self.new, str(self.stage), archive_path=str(self.new_source))
        self.assertEqual(self.backend.recover_update(self.old, self.new,
                         str(self.software)).status, "UPDATE_AVAILABLE")
        target = self.software / self.new.slug / self.new.release_id / self.new.platform
        target.mkdir(parents=True)
        (target / self.new.executable_name).write_bytes(b"incomplete")
        state = ManagedArtifactBackend().recover_update(self.old, self.new, str(self.software))
        self.assertEqual(state.status, "UPDATE_TARGET_INVALID")
        with self.assertRaisesRegex(InstallError, "UPDATE_TARGET_CONFLICT"):
            self.backend.apply_update(self.old, self.new, str(self.stage), self.approve())
        self.assert_preserved()
        self.assertTrue((target / self.new.executable_name).exists())

    def test_missing_old_prevents_upgrade(self):
        self.backend.prepare(self.new, str(self.stage), archive_path=str(self.new_source))
        self.assertEqual(self.backend.plan_update(self.old, self.new,
                         str(self.software)).status, "UPDATE_SOURCE_NOT_VERIFIED")
        with self.assertRaisesRegex(InstallError, "UPDATE_CURRENT_NOT_VERIFIED"):
            self.backend.apply_update(self.old, self.new, str(self.stage), self.approve())
        self.assertEqual(self.backend.verify(self.new, str(self.software)).status,
                         "NOT_INSTALLED")

    def test_wrong_staged_bytes_preserve_old_release(self):
        self.install_old()
        self.backend.prepare(self.new, str(self.stage), archive_path=str(self.new_source))
        stage_file = self.stage / self.new.slug / self.new.release_id / self.new.platform / self.new.executable_name
        stage_file.chmod(0o600)
        stage_file.write_bytes(b"tampered stage")
        with self.assertRaises(InstallError):
            self.backend.apply_update(self.old, self.new, str(self.stage), self.approve())
        self.assertEqual(self.backend.verify(self.new, str(self.software)).status,
                         "NOT_INSTALLED")
        self.assert_preserved()

    def test_updated_release_is_removed_independently(self):
        self.install_old()
        self.backend.prepare(self.new, str(self.stage), archive_path=str(self.new_source))
        self.backend.apply_update(self.old, self.new, str(self.stage), self.approve())
        removed = self.backend.remove(self.new, ApprovedRemoval(
            self.new.program_id, self.new.fingerprint, "PINS-update-remove",
            str(self.software), True))
        self.assertEqual(removed.status, "REMOVED")
        self.assertEqual(self.backend.recover_update(self.old, self.new,
                         str(self.software)).status, "UPDATE_AVAILABLE")
        self.assert_preserved()


if __name__ == "__main__":
    unittest.main()
