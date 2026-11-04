"""Manifest-driven qualification of independent upstream single-file programs.

No product-specific backend or executable handling is permitted here.
The catalog supplies the command and version proof.
"""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "universal-install"))
from iwxa_universal_install import (
    ApprovedInstall, ApprovedRemoval, ManagedArtifactBackend, ManagedArtifactSpec
)
from iwxa_universal_install.managed import _native_platform

def digest(path: Path) -> str:
    value = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            value.update(chunk)
    return value.hexdigest()

def qualify(catalog_path: Path) -> dict:
    catalog = json.loads(catalog_path.read_text(encoding="utf-8"))
    if catalog.get("schema") != "IWXA_INDEPENDENT_BINARY_CATALOG/1":
        raise ValueError("CATALOG_SCHEMA_INVALID")
    target = _native_platform()
    platform_data = catalog["platforms"].get(target)
    if platform_data is None:
        raise ValueError("PROGRAM_PLATFORM_UNSUPPORTED")
    proof = catalog["qualification"]
    arguments = proof["arguments"]
    needle = proof["stdout_contains"]
    if (not isinstance(arguments, list) or len(arguments) > 8 or
        any(not isinstance(a, str) or not a or len(a) > 128 for a in arguments) or
        not isinstance(needle, str) or not 1 <= len(needle) <= 128):
        raise ValueError("CATALOG_QUALIFICATION_INVALID")
    repo = catalog["release_repository"]
    if not isinstance(repo, str) or not re.fullmatch(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+", repo):
        raise ValueError("CATALOG_REPOSITORY_INVALID")
    asset = platform_data["asset"]
    pinned = platform_data["sha256"]
    spec = ManagedArtifactSpec(
        program_id=catalog["program_id"], slug=catalog["slug"],
        release_id=catalog["upstream_tag"], platform=target,
        executable_name=platform_data["executable"], archive_name=asset,
        archive_format="binary", archive_sha256=pinned, executable_sha256=pinned,
        download_url=f"https://github.com/{repo}/releases/download/{catalog['upstream_tag']}/{asset}",
    )
    backend = ManagedArtifactBackend()
    with tempfile.TemporaryDirectory(prefix="iwxa-manifest-qualification-") as temp:
        root = Path(temp).resolve()
        staging = root / "stage"
        installation = root / "software"
        user_data = installation / "user-data.txt"
        installation.mkdir()
        user_data.write_text("never delete", encoding="utf-8")
        events = []
        prepared = backend.prepare(spec, str(staging), on_progress=lambda n,t: events.append((n,t)))
        if prepared.status != "STAGED_NOT_ACTIVATED" or not events or events[-1][0] <= 0:
            raise RuntimeError("MANIFEST_PREPARATION_FAILED")
        install = ApprovedInstall(spec.program_id, spec.fingerprint,
                                  "PINS-independent-install", str(installation), True)
        result = backend.apply(spec, str(staging), install)
        if not result.installed or result.connected or result.receipt_written:
            raise RuntimeError("MANIFEST_INSTALL_FAILED")
        binary = Path(result.executable)
        if digest(binary) != pinned:
            raise RuntimeError("MANIFEST_INSTALLED_DIGEST_MISMATCH")
        executed = subprocess.run([str(binary), *arguments], capture_output=True,
                                  text=True, timeout=25, check=True)
        if needle not in executed.stdout:
            raise RuntimeError("MANIFEST_EXECUTION_PROOF_MISMATCH")
        if not ManagedArtifactBackend().recover(spec, str(installation)).installed:
            raise RuntimeError("MANIFEST_RECOVER_FAILED")
        removal = ApprovedRemoval(spec.program_id, spec.fingerprint,
                                  "PINS-independent-remove", str(installation), True)
        if backend.remove(spec, removal).status != "REMOVED":
            raise RuntimeError("MANIFEST_REMOVE_FAILED")
        if backend.recover(spec, str(installation)).status != "NOT_INSTALLED":
            raise RuntimeError("MANIFEST_REMOVE_READBACK_FAILED")
        if user_data.read_text(encoding="utf-8") != "never delete" or not Path(prepared.executable).exists():
            raise RuntimeError("MANIFEST_UNRELATED_DATA_CHANGED")
        result = {
            "schema": "IWXA_MANAGED_THIRD_PROGRAM/1", "status": "PASS",
            "program_id": spec.program_id, "version": catalog["version"],
            "platform": target, "asset": asset, "sha256": pinned,
            "official_https": True, "downloaded_bytes": events[-1][0],
            "executed": True, "readback": True, "removed": True,
            "staging_preserved": True, "user_data_preserved": True,
            "generic_backend": "ManagedArtifactBackend",
            "program_adapter_added": False,
        }
        print(json.dumps(result, sort_keys=True))
        return result

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--catalog", type=Path, required=True)
    args = parser.parse_args()
    qualify(args.catalog)
