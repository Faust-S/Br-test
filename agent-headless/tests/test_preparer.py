"""Offline portable artifact preparation checks: do not require Agent or network."""
from __future__ import annotations
import hashlib
import io
import json
import os
from pathlib import Path
import sys
import tarfile
import tempfile
import unittest
from unittest import mock
import zipfile

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from iwxa_obscura.preparer import (
    ArtifactSpec, ObscuraArtifactPreparer, ArtifactPreparationError,
    UPSTREAM_COMMIT, GUARD_PROTOCOL, SCHEMA, _SafeRedirect,
)

EXE=b'fake executable fixture -- not production Obscura'
VERSION='v0.2.6-iwxa.1'


class TestArtifactPreparation(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base=Path(self.tmp.name)
        self.archive=self.base/'windows.zip'
        with zipfile.ZipFile(self.archive,'w') as z:
            z.writestr('obscura-worker.exe',EXE)
        self.preparer=ObscuraArtifactPreparer(managed_root=self.base/'software')

    def spec(self, path=None, platform='windows-x86_64', **overrides):
        path=path or self.archive
        fmt='zip' if platform=='windows-x86_64' else 'tar.gz'
        name='obscura-'+platform+('.zip' if fmt=='zip' else '.tar.gz')
        data=dict(release_id=VERSION,platform=platform,
                  archive_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),
                  executable_sha256=hashlib.sha256(EXE).hexdigest(),
                  archive_name=name,archive_format=fmt,
                  download_url='https://github.com/Faust-S/Br-test/releases/download/'+VERSION+'/'+name,
                  source_commit=UPSTREAM_COMMIT, origin_guard_patch_sha256='e'*64,
                  guard_protocol=GUARD_PROTOCOL)
        data.update(overrides)
        return ArtifactSpec(**data)

    def rejected(self, code, fn):
        with self.assertRaises(ArtifactPreparationError) as ctx:
            fn()
        self.assertEqual(ctx.exception.code,code)

    def test_zip_prepare_verify_and_no_activation(self):
        spec=self.spec()
        prepared=self.preparer.prepare_local(self.archive,spec)
        self.assertEqual(prepared.status,'STAGED_NOT_ACTIVATED')
        self.assertEqual(Path(prepared.executable).read_bytes(),EXE)
        self.assertEqual(self.preparer.verify_prepared(spec),prepared)
        self.assertFalse((self.base/'software'/'current').exists())

    def test_idempotent_staging(self):
        spec=self.spec()
        self.assertEqual(self.preparer.prepare_local(self.archive,spec),
                         self.preparer.prepare_local(self.archive,spec))

    def test_linux_tarball(self):
        path=self.base/'linux.tar.gz'
        with tarfile.open(path,'w:gz') as z:
            info=tarfile.TarInfo('obscura-worker')
            info.size=len(EXE)
            z.addfile(info,io.BytesIO(EXE))
        spec=self.spec(path,platform='linux-x86_64')
        prepared=self.preparer.prepare_local(path,spec)
        self.assertEqual(Path(prepared.executable).read_bytes(),EXE)

    def test_archive_hash_mismatch(self):
        self.rejected('ARTIFACT_ARCHIVE_DIGEST_MISMATCH',
                      lambda:self.preparer.prepare_local(self.archive,
                          self.spec(archive_sha256='0'*64)))

    def test_executable_hash_mismatch(self):
        self.rejected('ARTIFACT_EXECUTABLE_DIGEST_MISMATCH',
                      lambda:self.preparer.prepare_local(self.archive,
                          self.spec(executable_sha256='0'*64)))

    def test_installed_binary_tampered(self):
        spec=self.spec()
        artifact=self.preparer.prepare_local(self.archive,spec)
        Path(artifact.executable).write_bytes(b'tampered')
        self.rejected('ARTIFACT_PREPARED_DIGEST_MISMATCH',
                      lambda:self.preparer.verify_prepared(spec))

    def test_extra_archive_member_denied(self):
        with zipfile.ZipFile(self.archive,'a') as z:
            z.writestr('unexpected.dll',b'X')
        self.rejected('ARTIFACT_ARCHIVE_CONTENTS_INVALID',
                      lambda:self.preparer.prepare_local(self.archive,self.spec()))

    def test_zip_traversal_denied(self):
        with zipfile.ZipFile(self.archive,'w') as z:
            z.writestr('../../obscura-worker.exe',EXE)
        self.rejected('ARTIFACT_ARCHIVE_CONTENTS_INVALID',
                      lambda:self.preparer.prepare_local(self.archive,self.spec()))

    def test_tar_symlink_denied(self):
        path=self.base/'linux.tar.gz'
        with tarfile.open(path,'w:gz') as z:
            info=tarfile.TarInfo('obscura-worker')
            info.type=tarfile.SYMTYPE
            info.linkname='/etc/passwd'
            z.addfile(info)
        self.rejected('ARTIFACT_ARCHIVE_CONTENTS_INVALID',
                      lambda:self.preparer.prepare_local(path,self.spec(path,platform='linux-x86_64')))

    def test_existing_release_conflict_denied(self):
        spec=self.spec()
        self.preparer.prepare_local(self.archive,spec)
        self.rejected('ARTIFACT_PREPARED_MARKER_INVALID',
                      lambda:self.preparer.prepare_local(self.archive,
                          self.spec(origin_guard_patch_sha256='f'*64)))

    def test_manifest_rejects_unknown_fields(self):
        spec=self.spec()
        self.assertEqual(ArtifactSpec.from_mapping(spec.as_dict()),spec)
        self.rejected('ARTIFACT_SPEC_FIELDS_INVALID',
                      lambda:ArtifactSpec.from_mapping({**spec.as_dict(),'privilege':'admin'}))

    def test_provenance_pins_mandatory(self):
        for fields in ({'source_commit':'0'*40},{'guard_protocol':'UNPATCHED'},
                       {'origin_guard_patch_sha256':'not-a-hash'}):
            with self.subTest(fields=fields),self.assertRaises(ValueError):
                self.spec(**fields)

    def test_release_url_bound_to_identity(self):
        with self.assertRaises(ValueError):
            self.spec(download_url='https://github.com/other/unknown/releases/download/'+VERSION+'/x.zip')

    def test_reparse_root_denied(self):
        root=self.base/'symlink-software'
        try:
            root.symlink_to(self.base/'software',target_is_directory=True)
        except (OSError,NotImplementedError):
            self.skipTest('symlinks unavailable')
        self.rejected('ARTIFACT_PATH_REPARSE_DENIED',
                      lambda:ObscuraArtifactPreparer(managed_root=root))

    def test_modified_marker_denied(self):
        spec=self.spec()
        artifact=self.preparer.prepare_local(self.archive,spec)
        (Path(artifact.executable).parent/'artifact-manifest.json').write_text('{}')
        self.rejected('ARTIFACT_PREPARED_MARKER_INVALID',
                      lambda:self.preparer.verify_prepared(spec))

    def test_download_digest_denied(self):
        class FakeOpener:
            def open(self,*args,**kwargs):
                return io.BytesIO(b'invalid release')
        with mock.patch('iwxa_obscura.preparer.build_opener',return_value=FakeOpener()):
            self.rejected('ARTIFACT_ARCHIVE_DIGEST_MISMATCH',
                          lambda:self.preparer.download_and_prepare(self.spec()))

    def test_download_verified_offline_fixture(self):
        content=self.archive.read_bytes()
        class FakeOpener:
            def open(self,*args,**kwargs):
                return io.BytesIO(content)
        with mock.patch('iwxa_obscura.preparer.build_opener',return_value=FakeOpener()):
            result=self.preparer.download_and_prepare(self.spec())
        self.assertEqual(result.status,'STAGED_NOT_ACTIVATED')

    def test_untrusted_redirects_denied(self):
        handler=_SafeRedirect()
        for url in ('http://example.org/','https://127.0.0.1/',
                    'https://other.example.org/','file:///etc/passwd'):
            self.rejected('ARTIFACT_DOWNLOAD_REDIRECT_DENIED',
                          lambda:handler.redirect_request(None,None,302,'',{},url))


if __name__=='__main__':
    unittest.main()
