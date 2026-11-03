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

UPSTREAM_TAG = "jq-1.8.1"
OFFICIAL = {
    "windows-x86_64": ("jq-win64.exe", "23cb60a1354eed6bcc8d9b9735e8c7b388cd1fdcb75726b93bc299ef22dd9334"),
    "linux-x86_64": ("jq-linux-amd64", "020468de7539ce70ef1bceaf7cde2e8c4f2ca6c3afb84642aabc5c97d9fc2a0d"),
    "linux-arm64": ("jq-linux-arm64", "6bc62f25981328edd3cfcfe6fe51b073f2d7e7710d7ef7fcdac28d4e384fc3d4"),
    "macos-x86_64": ("jq-macos-amd64", "e80dbe0d2a2597e3c11c404f03337b981d74b4a8504b70586c354b7697a7c27f"),
    "macos-arm64": ("jq-macos-arm64", "a9fe3ea2f86dfc72f6728417521ec9067b343277152b114f4e98d8cb0e263603"),
}


def hash_file(path):
    h=hashlib.sha256()
    with path.open("rb") as fd:
        for chunk in iter(lambda:fd.read(1024*1024),b""):
            h.update(chunk)
    return h.hexdigest()


def execute() -> dict:
    target = _native_platform()
    if target not in OFFICIAL:
        raise RuntimeError("INDEPENDENT_PROGRAM_PLATFORM_NOT_PUBLISHED")
    asset, expected_sha = OFFICIAL[target]
    with tempfile.TemporaryDirectory(prefix="iwxa-generic-jq-") as temp:
        root=Path(temp).resolve()
        raw=root/asset
        url=f"https://github.com/jqlang/jq/releases/download/{UPSTREAM_TAG}/{asset}"
        with urlopen(Request(url,headers={"User-Agent":"IWXA-generic-install-qualification"}), timeout=45) as response:
            with raw.open("xb") as fd:
                total=0
                while block:=response.read(1024*1024):
                    total+=len(block)
                    if total>20*1024*1024:
                        raise RuntimeError("INDEPENDENT_BINARY_OVERSIZE")
                    fd.write(block)
        if hash_file(raw)!=expected_sha:
            raise RuntimeError("INDEPENDENT_OFFICIAL_DIGEST_MISMATCH")
        filename="jq.exe" if os.name=="nt" else "jq"
        format_name="zip" if os.name=="nt" else "tar.gz"
        archive_name=f"jq-{target}.{format_name}"
        archive=root/archive_name
        if format_name=="zip":
            with zipfile.ZipFile(archive,"x",compression=zipfile.ZIP_DEFLATED) as z:
                z.write(raw,arcname=filename)
        else:
            with tarfile.open(archive,"w:gz") as t:
                info=tarfile.TarInfo(filename)
                info.size=raw.stat().st_size
                info.mode=0o755
                with raw.open("rb") as fd:
                    t.addfile(info,fd)
        # Nothing below this line contains jq-specific installer code.
        spec=ManagedArtifactSpec(
            program_id="JQ_CLI_V1",slug="jq-cli",release_id="1.8.1",platform=target,
            executable_name=filename,archive_name=archive_name,
            archive_format=format_name,archive_sha256=hash_file(archive),
            executable_sha256=expected_sha,
            download_url=("https://github.com/Faust-S/Br-test/releases/download/"
                          "jq-managed-qualification-1.8.1/"+archive_name),
        )
        backend=ManagedArtifactBackend()
        staging=root/"staging"
        install_root=root/"installed"
        staged=backend.prepare(spec,str(staging),archive_path=str(archive))
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
            "status":"PASS","platform":target,"program":"jq","version":"1.8.1",
            "official_source_url":url,"official_binary_sha256":expected_sha,
            "managed_archive_sha256":spec.archive_sha256,
            "generic_backend_class":"ManagedArtifactBackend",
            "adapter_added":False,
            "executed":True,"removed":True,"private_agent_involved":False,
        }
        print(json.dumps(report,sort_keys=True))
        return report


if __name__=="__main__":
    execute()
