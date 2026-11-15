"""Fast, fully offline checks for the unsigned Agent06 U01 release handoff."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "universal-install" / "qualification"))
sys.path.insert(0, str(ROOT / "agent-headless"))
from iwxa_obscura.preparer import ArtifactSpec, GUARD_PROTOCOL, UPSTREAM_COMMIT
from package_native_release import create_archive
from agent06_unsigned_release_candidate import (
    CANDIDATE_INTENT,
    PLATFORMS,
    candidate_from_native,
)

SHA = "a" * 40


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


class U01UnsignedCandidateTests(unittest.TestCase):
    def setUp(self):
        self.working = tempfile.TemporaryDirectory()
        self.addCleanup(self.working.cleanup)
        self.root = Path(self.working.name)
        self.inputs = self.root / "native"
        self.output = self.root / "assembled"
        self.patch = self.root / "obscura.patch"
        self.patch.write_bytes(b"--- a/example\n+++ b/example\n")
        self.license = self.root / "LICENSE"
        self.license.write_text("Apache License\n", encoding="utf-8")
        self.release = "obscura-06-rc-a123456789ab"
        self._prepare()

    def _prepare(self):
        for runner, (platform, executable, ext) in PLATFORMS.items():
            folder = self.inputs / ("agent06-headless-" + runner) / "candidate"
            folder.mkdir(parents=True)
            binary = self.root / (platform + "-binary")
            binary.write_bytes(b"IWXA PUBLIC TEST BINARY!" * 54000)
            archive_name = "obscura-" + platform + ext
            create_archive(binary, folder / archive_name, executable)
            patch_sha = digest(self.patch)
            manifest = {
                "status": "BUILT_INACTIVE_NOT_RELEASED",
                "platform": platform,
                "upstream_commit": UPSTREAM_COMMIT,
                "guard_protocol": GUARD_PROTOCOL,
                "origin_guard_patch_sha256": patch_sha,
                "archive_name": archive_name,
                "archive_sha256": digest(folder / archive_name),
                "executable_name": executable,
                "executable_sha256": digest(binary),
                "agent_private_source_included": False,
            }
            (folder / "candidate-manifest.json").write_text(
                json.dumps(manifest), encoding="utf-8",
            )

    def _run(self, **changes):
        kwargs = {
            "artifacts": self.inputs, "output": self.output,
            "release_id": self.release, "source_sha": SHA,
            "source_run": 12345, "patch": self.patch,
            "upstream_license": self.license,
        }
        kwargs.update(changes)
        return candidate_from_native(**kwargs)

    def _change(self, runner: str, **fields):
        manifest = (self.inputs / ("agent06-headless-" + runner)
                    / "candidate" / "candidate-manifest.json")
        record = json.loads(manifest.read_text(encoding="utf-8"))
        record.update(fields)
        manifest.write_text(json.dumps(record), encoding="utf-8")

    def test_two_real_sha_verified_platform_packages_are_unsigned(self):
        result = self._run()
        self.assertEqual(result["schema"], "IWXA_OBSCURA_U01_RELEASE_CANDIDATE/1")
        self.assertEqual(result["intent"], CANDIDATE_INTENT)
        self.assertFalse(result["signed"])
        self.assertFalse(result["published"])
        self.assertFalse(result["approved_for_end_user_install"])
        self.assertFalse(result["private_agent_artifacts_included"])
        self.assertEqual(result["source_ci_head_sha"], SHA)
        self.assertEqual(set(result["platforms"]), {"windows-x86_64", "linux-x86_64"})
        for platform, spec_data in result["platforms"].items():
            spec = ArtifactSpec.from_mapping(spec_data)
            self.assertEqual(spec.executable_sha256, spec_data["executable_sha256"])
            self.assertEqual(digest(self.output / spec.archive_name),
                             spec.archive_sha256)
        self.assertEqual(digest(self.output / "LICENSE-OBSCURA.txt"),
                         result["license_sha256"])
        self.assertEqual(
            json.loads((self.output / "manifest.candidate.json")
                       .read_text(encoding="utf-8")), result,
        )

    def test_missing_other_platform_is_not_emitted_as_release(self):
        folder = self.inputs / "agent06-headless-ubuntu-24.04" / "candidate"
        (folder / "candidate-manifest.json").unlink()
        with self.assertRaisesRegex(ValueError, "CANDIDATE_NATIVE_MANIFEST_MISSING"):
            self._run()
        self.assertFalse(self.output.exists())

    def test_archive_tampering_rejected_before_output(self):
        archive = (self.inputs / "agent06-headless-windows-2022" / "candidate"
                   / "obscura-windows-x86_64.zip")
        with archive.open("ab") as stream:
            stream.write(b"surreptitious bytes")
        with self.assertRaisesRegex(ValueError, "CANDIDATE_ARCHIVE_DIGEST_MISMATCH"):
            self._run()
        self.assertFalse(self.output.exists())

    def test_wrong_internal_archive_member_fails_even_with_rehashed_archive(self):
        folder = self.inputs / "agent06-headless-windows-2022" / "candidate"
        archive = folder / "obscura-windows-x86_64.zip"
        wrong = self.root / "wrong-member"
        wrong.write_bytes(b"invalid executable" * 90000)
        archive.unlink()
        create_archive(wrong, archive, "unrelated.exe")
        self._change("windows-2022", archive_sha256=digest(archive),
                     executable_sha256=digest(wrong))
        with self.assertRaisesRegex(ValueError, "CANDIDATE_ARCHIVE_SHAPE_INVALID"):
            self._run()
        self.assertFalse(self.output.exists())

    def test_wrong_guard_and_untrusted_private_claim_rejected(self):
        self._change("ubuntu-24.04", origin_guard_patch_sha256="f" * 64)
        with self.assertRaisesRegex(ValueError, "CANDIDATE_PATCH_DIGEST_MISMATCH"):
            self._run()
        self._change("ubuntu-24.04",
                     origin_guard_patch_sha256=digest(self.patch),
                     agent_private_source_included=True)
        with self.assertRaisesRegex(ValueError, "CANDIDATE_NATIVE_IDENTITY_INVALID"):
            self._run()

    def test_current_guard_version_required(self):
        self._change("windows-2022", guard_protocol="IWXA_ORIGIN_NET_GUARD_V1_NO_RENDER")
        with self.assertRaisesRegex(ValueError, "CANDIDATE_NATIVE_IDENTITY_INVALID"):
            self._run()

    def test_release_identity_and_test_tags_not_promoted(self):
        with self.assertRaisesRegex(ValueError, "CANDIDATE_TEST_RELEASE_ID_FORBIDDEN"):
            self._run(release_id="iwxa-obscura-native-ci-test2")
        with self.assertRaisesRegex(ValueError, "CANDIDATE_SOURCE_IDENTITY_INVALID"):
            self._run(source_sha="short")
        self.assertFalse(self.output.exists())

    def test_crlf_windows_patch_digest_is_only_platform_specific_allowance(self):
        raw = self.patch.read_bytes()
        windows_crlf_sha = hashlib.sha256(raw.replace(b"\n", b"\r\n")).hexdigest()
        self._change("windows-2022",
                     origin_guard_patch_sha256=windows_crlf_sha)
        result = self._run()
        self.assertEqual(
            result["platforms"]["windows-x86_64"]["origin_guard_patch_sha256"],
            windows_crlf_sha,
        )

    def test_existing_destination_not_overwritten(self):
        self.output.mkdir()
        (self.output / "foreign.txt").write_text("must survive", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "CANDIDATE_OUTPUT_NOT_EMPTY"):
            self._run()
        self.assertEqual((self.output / "foreign.txt").read_text(), "must survive")


if __name__ == "__main__":
    unittest.main()
