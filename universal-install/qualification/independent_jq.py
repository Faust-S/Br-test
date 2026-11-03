"""Real independent-program qualification: jq 1.8.1, no jq installer/backend.

Official jq release binaries are pinned by SHA-256. The test packages a single
binary into a supported archive and passes only a ManagedArtifactSpec to the
same generic installer used by IWXA Obscura.
"""
from __future__ import annotations
import hashlib
import io
import json
import os
from pathlib import Path
import platform
import subprocess
import sys
import tarfile
import tempfile
from urllib.request import urlopen, Request
import zipfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0,str(ROOT/"universal-install"))
from iwxa_universal_install import (ApprovedInstall, ApprovedRemoval,
                                    ManagedArtifactBackend, ManagedArtifactSpec)
from iwxa_universal_install.managed import _native_platform

CATALOG = ROOT / "universal-install" / "catalog" / "jq-1.8.1.json"


def hash_file(path):
    h=hashlib.sha256()
    with path.open("rb") as fd:
        for chunk in iter(lambda:fd.read(1024*1024),b""):
            h.update(chunk)
    return h.hexdigest()


def execute() -> dict:
    target = _native_platform()
    catalog = json.loads(CATALOG.read_text(encoding="utf-8"))
    if catalog.get("schema") != "IWXA_INDEPENDENT_BINARY_CATALOG/1":
        raise RuntimeError("INDEPENDENT_CATALOG_INVALID")
    if target not in catalog["platforms"]:
        raise RuntimeError("INDEPENDENT_PROGRAM_PLATFORM_NOT_PUBLISHED")
    item = catalog["platforms"][target]
    asset, expected_sha = item["asset"], item["sha256"]
    with tempfile.TemporaryDirectory(prefix="iwxa-generic-jq-") as temp:
        root=Path(temp).resolve()
        url=f"https://github.com/jqlang/jq/releases/download/{catalog['upstream_tag']}/{asset}"
        filename=item["executable"]
        # A pinned official standalone binary is now an ordinary manifest
        # entry: the installer downloads it over HTTPS without jq-specific
        # repackaging, a jq backend, or separate install code.
        spec=ManagedArtifactSpec(
            program_id=catalog["program_id"],slug=catalog["slug"],
            release_id=catalog["upstream_tag"],platform=target,
            executable_name=filename,archive_name=asset,
            archive_format="binary",archive_sha256=expected_sha,
            executable_sha256=expected_sha,download_url=url,
        )
        backend=ManagedArtifactBackend()
        staging=root/"staging"
        install_root=root/"installed"
        staged=backend.prepare(spec,str(staging))
        if staged.status!="STAGED_NOT_ACTIVATED":
            raise RuntimeError("INDEPENDENT_STAGING_FAILED")
        approval=ApprovedInstall(spec.program_id,spec.fingerprint,
                                 "PINS-independent-jq",str(install_root),True)
        installed=backend.apply(spec,str(staging),approval)
        if not installed.installed or installed.connected or installed.receipt_written:
            raise RuntimeError("INDEPENDENT_INSTALL_OBSERVATION_INVALID")
        binary=Path(installed.executable)
        if hash_file(binary)!=expected_sha:
            raise RuntimeError("INDEPENDENT_INSTALLED_DIGEST_MISMATCH")
        result=subprocess.run([str(binary),"-n",'{"proof":"second-real-program","valid":true}'],
                              capture_output=True,text=True,timeout=25,check=True)
        parsed=json.loads(result.stdout)
        if parsed!={"proof":"second-real-program","valid":True}:
            raise RuntimeError("INDEPENDENT_JQ_EXECUTION_FAILED")
        if not ManagedArtifactBackend().recover(spec,str(install_root)).installed:
            raise RuntimeError("INDEPENDENT_READBACK_FAILED")
        removal=ApprovedRemoval(spec.program_id,spec.fingerprint,
                                "PINS-independent-jq-remove",str(install_root),True)
        if backend.remove(spec,removal).status!="REMOVED":
            raise RuntimeError("INDEPENDENT_REMOVE_FAILED")
        if backend.recover(spec,str(install_root)).status!="NOT_INSTALLED":
            raise RuntimeError("INDEPENDENT_REMOVE_READBACK_FAILED")
        report={
            "schema":"IWXA_MANAGED_SECOND_PROGRAM/1",
            "status":"PASS","platform":target,"program":"jq","version":catalog["version"],
            "official_source_url":url,"official_binary_sha256":expected_sha,
            "managed_archive_sha256":spec.archive_sha256,
            "direct_official_https_download":True,
            "generic_backend_class":"ManagedArtifactBackend",
            "adapter_added":False,
            "executed":True,"removed":True,"private_agent_involved":False,
        }
        print(json.dumps(report,sort_keys=True))
        return report


if __name__=="__main__":
    execute()
