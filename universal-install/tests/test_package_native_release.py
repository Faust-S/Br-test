"""Public release packager contract checks; no network and no real release."""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "universal-install" / "qualification"))
sys.path.insert(0, str(ROOT / "agent-headless"))
from iwxa_obscura.preparer import GUARD_PROTOCOL, UPSTREAM_COMMIT
import package_native_release as packaging

PATCH_SHA = "e" * 64
SOURCE_SHA = "a" * 40

class ReleasePackageTest(unittest.TestCase):
    def setUp(self):
        self.ctx = tempfile.TemporaryDirectory()
        self.addCleanup(self.ctx.cleanup)
        self.root = Path(self.ctx.name).resolve()
        self.archives = self.root / "native-source"
        self.output = self.root / "published-candidate"

    def fixture(self, runner: str, platform: str, filename: str):
        root = self.archives / ("universal-native-obscura-" + runner)
        root.mkdir(parents=True)
        binary = root / "upstream" / "target" / "release" / filename
        binary.parent.mkdir(parents=True)
        binary.write_bytes((b"IWXA TEST ONLY" * 99000)[:1200000])
        sha = hashlib.sha256(binary.read_bytes()).hexdigest()
        os_label = "Windows" if platform.startswith("windows") else ("macOS" if platform.startswith("macos") else "Linux")
        report = {
            "schema": "IWXA_UNIVERSAL_NATIVE_INSTALL_CI/1.0",
            "platform": platform,
            "source_commit": UPSTREAM_COMMIT,
            "patch_sha256": PATCH_SHA,
            "status": "PASS",
            "real_native_binary": True,
            "published_release": False,
            "tool_runtime_connected": False,
            "new_version_removed_after_corruption": True,
            "old_version_survived_new_version_removal": True,
            "old_version_removed_after_worker_shutdown": True,
            "native_attestation": "IWXA_ORIGIN_NET_GUARD_V1_NO_RENDER",
            "fresh_process_greeting": GUARD_PROTOCOL,
            "build_binary_sha256": sha,
            "installed_binary_sha256": sha,
        }
        evidence = root / "artifacts" / "universal-install"
        evidence.mkdir(parents=True)
        (evidence / ("native-" + os_label + ".json")).write_text(
            json.dumps(report), encoding="utf-8")
        return binary

    def test_package_one_pinned_linux_binary(self):
        self.fixture("ubuntu-24.04","linux-x86_64","obscura-worker")
        with mock.patch.dict(packaging.TARGETS,
                             {"ubuntu-24.04":("linux-x86_64","Linux","obscura-worker")},
                             clear=True):
            record = packaging.package(self.archives,self.output,"ci-test-one",123,SOURCE_SHA,PATCH_SHA)
        spec = record["platforms"]["linux-x86_64"]
        archive = self.output / spec["archive_name"]
        self.assertTrue(archive.is_file())
        self.assertEqual(hashlib.sha256(archive.read_bytes()).hexdigest(),
                         spec["archive_sha256"])
        self.assertEqual(record["native_source_head_sha"],SOURCE_SHA)
        self.assertEqual(record["schema"],packaging.TEST_RELEASE_SCHEMA)

    def test_package_one_pinned_windows_binary(self):
        self.fixture("windows-2022","windows-x86_64","obscura-worker.exe")
        with mock.patch.dict(packaging.TARGETS,
                             {"windows-2022":("windows-x86_64","Windows","obscura-worker.exe")},
                             clear=True):
            record = packaging.package(self.archives,self.output,"ci-test-one",123,SOURCE_SHA,PATCH_SHA)
        spec = record["platforms"]["windows-x86_64"]
        self.assertTrue((self.output / spec["archive_name"]).is_file())

    def test_missing_or_forged_evidence_blocks_package(self):
        self.fixture("ubuntu-24.04","linux-x86_64","obscura-worker")
        path = self.archives / "universal-native-obscura-ubuntu-24.04" / "artifacts" / "universal-install" / "native-Linux.json"
        report = json.loads(path.read_text())
        report["old_version_removed_after_worker_shutdown"] = False
        path.write_text(json.dumps(report))
        with mock.patch.dict(packaging.TARGETS,
                             {"ubuntu-24.04":("linux-x86_64","Linux","obscura-worker")},
                             clear=True):
            with self.assertRaisesRegex(ValueError,"CI_EVIDENCE_INVALID"):
                packaging.package(self.archives,self.output,"ci-test-one",123,SOURCE_SHA,PATCH_SHA)

    def test_unknown_artifact_directory_is_not_silently_accepted(self):
        with mock.patch.dict(packaging.TARGETS,
                             {"ubuntu-24.04":("linux-x86_64","Linux","obscura-worker")},
                             clear=True):
            with self.assertRaisesRegex(ValueError,"NATIVE_ARTIFACT_MISSING"):
                packaging.package(self.archives,self.output,"ci-test-one",123,SOURCE_SHA,PATCH_SHA)

if __name__ == "__main__":
    unittest.main()
