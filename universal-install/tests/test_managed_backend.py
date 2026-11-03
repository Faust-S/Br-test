"""Data-only two-program qualification for the shared installer.

The second program requires a new manifest, not an installer subclass.
All operations are restricted to disposable temporary directories.
"""
from __future__ import annotations

import hashlib
import io
import json
import os
from pathlib import Path
import platform
import sys
import tarfile
import tempfile
import unittest
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "universal-install"))
sys.path.insert(0, str(ROOT / "agent-headless"))

from iwxa_universal_install import (ApprovedInstall, ApprovedRemoval, InstallError,
                                    ManagedArtifactBackend, ManagedArtifactSpec,
                                    ObscuraManagedBackend)
from iwxa_universal_install.managed import _native_platform

PROGRAMS = {"JQ_CLI_V1":("jq-tool","jq.exe" if os.name=="nt" else "jq"),
            "SECOND_TOOL_V1":("second-tool","second.exe" if os.name=="nt" else "second")}
BINARY = b"independent-pinned-program-v1-not-obscura"


class GenericProgramTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name).resolve()
        self.staging = self.root / "stage"
        self.software = self.root / "product-owned-software"
        self.backend = ManagedArtifactBackend()

    def package(self, program_id: str, data: bytes = BINARY, release: str = "1.0.0"):
        slug, name = PROGRAMS[program_id]
        platform_name = _native_platform()
        fmt = "zip" if os.name == "nt" else "tar.gz"
        file_name = slug+"-"+platform_name+("." + fmt)
        archive = self.root / file_name
        if fmt == "zip":
            with zipfile.ZipFile(archive,"w",compression=zipfile.ZIP_DEFLATED) as z:
                z.writestr(name,data)
        else:
            with tarfile.open(archive,"w:gz") as t:
                header=tarfile.TarInfo(name)
                header.size=len(data)
                t.addfile(header,io.BytesIO(data))
        archive_sha=hashlib.sha256(archive.read_bytes()).hexdigest()
        binary_sha=hashlib.sha256(data).hexdigest()
        spec=ManagedArtifactSpec(
            program_id=program_id,slug=slug,release_id=release,
            platform=platform_name,executable_name=name,archive_name=file_name,
            archive_format=fmt,archive_sha256=archive_sha,executable_sha256=binary_sha,
            download_url="https://github.com/Faust-S/Br-test/releases/download/"
                         +release+"/"+file_name,
        )
        return archive,spec

    def install(self,program_id: str):
        archive,spec=self.package(program_id)
        prep=self.backend.prepare(spec,str(self.staging),archive_path=str(archive))
        approval=ApprovedInstall(spec.program_id,spec.fingerprint,
                                 "PINS-generic-install",str(self.software),True)
        observed=self.backend.apply(spec,str(self.staging),approval)
        self.assertTrue(observed.installed)
        self.assertEqual(observed.program_id,program_id)
        self.assertFalse(observed.connected)
        return spec,prep,observed

    def test_two_programs_share_one_backend_instance(self):
        jq,jq_prepared,jq_installed=self.install("JQ_CLI_V1")
        second,second_prepared,other=self.install("SECOND_TOOL_V1")
        self.assertNotEqual(jq.fingerprint,second.fingerprint)
        self.assertNotEqual(jq_installed.executable,other.executable)
        self.assertTrue(Path(jq_prepared.executable).exists())
        self.assertTrue(Path(second_prepared.executable).exists())
        self.assertEqual(self.backend.recover(jq,str(self.software)),jq_installed)
        self.assertEqual(self.backend.verify(second,str(self.software)),other)
        removal=ApprovedRemoval(jq.program_id,jq.fingerprint,
                                "PINS-generic-remove",str(self.software),True)
        self.assertEqual(self.backend.remove(jq,removal).status,"REMOVED")
        self.assertEqual(self.backend.recover(jq,str(self.software)).status,"NOT_INSTALLED")
        self.assertEqual(self.backend.recover(second,str(self.software)),other)
        self.assertEqual(self.backend.remove(jq,removal).status,"NOT_INSTALLED")

    def test_official_standalone_binary_is_supported_by_manifest_only(self):
        # No archive transformation. A pinned downloadable binary can be
        # staged and installed directly on any supported host architecture.
        target=_native_platform()
        data=b"actual-binary-format-fixture-no-custom-adapter"
        file_name="jq-standalone.exe" if os.name=="nt" else "jq-standalone"
        source=self.root/file_name
        source.write_bytes(data)
        sha=hashlib.sha256(data).hexdigest()
        spec=ManagedArtifactSpec(
            program_id="JQ_CLI_V1",slug="jq-tool",release_id="jq-1.8.1",
            platform=target,executable_name="jq.exe" if os.name=="nt" else "jq",
            archive_name=file_name,archive_format="binary",
            archive_sha256=sha,executable_sha256=sha,
            download_url=("https://github.com/jqlang/jq/releases/download/"
                          "jq-1.8.1/"+file_name),
        )
        prepared=self.backend.prepare(spec,str(self.staging),archive_path=str(source))
        self.assertEqual(Path(prepared.executable).read_bytes(),data)
        approval=ApprovedInstall(spec.program_id,spec.fingerprint,
                                 "PINS-direct-binary",str(self.software),True)
        installed=self.backend.apply(spec,str(self.staging),approval)
        self.assertEqual(Path(installed.executable).read_bytes(),data)
        self.assertTrue(self.backend.verify(spec,str(self.software)).installed)
        removal=ApprovedRemoval(spec.program_id,spec.fingerprint,
                                "PINS-direct-remove",str(self.software),True)
        self.assertEqual(self.backend.remove(spec,removal).status,"REMOVED")

    def test_direct_binary_must_pin_identical_archive_and_executable_hashes(self):
        _,spec=self.package("JQ_CLI_V1")
        bad={**spec.as_dict(),"archive_format":"binary",
             "archive_sha256":"0"*64}
        with self.assertRaisesRegex(ValueError,"MANAGED_SPEC_BINARY_DIGEST_MISMATCH"):
            ManagedArtifactSpec.from_mapping(bad)

    def test_second_program_requires_no_backend_subclass(self):
        archive,spec=self.package("JQ_CLI_V1")
        self.assertEqual(type(self.backend),ManagedArtifactBackend)
        stages=[]
        prepared=self.backend.prepare(spec,str(self.staging),archive_path=str(archive),
                                      on_progress=lambda n,total:stages.append((n,total)))
        self.assertEqual(prepared.status,"STAGED_NOT_ACTIVATED")
        self.assertEqual(stages,[(1,1)])
        self.assertEqual(self.backend.inspect(spec,str(self.software)).status,"NOT_INSTALLED")

    def test_wrong_program_approval_and_manifest_digests_rejected(self):
        archive,spec=self.package("JQ_CLI_V1")
        self.backend.prepare(spec,str(self.staging),archive_path=str(archive))
        approval=ApprovedInstall("SECOND_TOOL_V1",spec.fingerprint,
                                 "PINS-generic-install",str(self.software),True)
        with self.assertRaisesRegex(InstallError,"INSTALL_APPROVAL_MISMATCH"):
            self.backend.apply(spec,str(self.staging),approval)
        removal=ApprovedRemoval("SECOND_TOOL_V1",spec.fingerprint,
                                "PINS-generic-remove",str(self.software),True)
        with self.assertRaisesRegex(InstallError,"REMOVE_APPROVAL_MISMATCH"):
            self.backend.remove(spec,removal)
        self.assertFalse(self.software.exists())

    def test_generic_manifest_rejects_traversal_and_insecure_source(self):
        _,spec=self.package("JQ_CLI_V1")
        for field,value in (("slug","../escape"),("executable_name","../jq"),
                            ("download_url","http://github.com/attacker/file"),
                            ("archive_sha256","0"*64),
                            ("platform","freebsd-x86_64")):
            bad={**spec.as_dict(),field:value}
            if field=="archive_sha256":
                # Well-formed but incorrect SHA must fail when staging.
                changed=ManagedArtifactSpec.from_mapping(bad)
                with self.assertRaises(Exception):
                    self.backend.prepare(changed,str(self.staging),
                                         archive_path=str(self.root/spec.archive_name))
            else:
                with self.subTest(field=field),self.assertRaises(ValueError):
                    ManagedArtifactSpec.from_mapping(bad)

    def test_program_specific_obscura_adapter_has_no_duplicated_lifecycle(self):
        self.assertTrue(issubclass(ObscuraManagedBackend,ManagedArtifactBackend))
        self.assertIs(ObscuraManagedBackend.apply,ManagedArtifactBackend.apply)
        self.assertIs(ObscuraManagedBackend.remove,ManagedArtifactBackend.remove)
        self.assertIs(ObscuraManagedBackend.verify,ManagedArtifactBackend.verify)
        self.assertIs(ObscuraManagedBackend.recover,ManagedArtifactBackend.recover)

if __name__=="__main__":
    unittest.main()
