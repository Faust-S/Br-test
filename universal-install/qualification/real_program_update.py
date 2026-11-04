"""Real IWXA U01 update qualification with two official, SHA-pinned yq binaries.

Isolated CI host: old -> new -> separate host process restart -> corrupt new ->
failed restart -> explicitly select and use old -> reinstall new -> readback.
No product Core, no new registry, no automatic activation, no vendor-specific backend.
"""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "universal-install"))

from iwxa_universal_install import (
    ApprovedInstall, ApprovedRemoval, ApprovedUpdate, InstallError,
    ManagedArtifactBackend, ManagedArtifactSpec,
)
from iwxa_universal_install.managed import _native_platform
from iwxa_universal_install.program_connection import CliConnection

CATALOG = ROOT / "universal-install" / "catalog"
REPO = "mikefarah/yq"
PROGRAM_ID = "YQ_CLI_V1"
VERSION_PAIR = ("v4.53.3", "v4.54.1")


def require(condition, error):
    if not condition:
        raise RuntimeError(error)


def spec_from_catalog(filename, platform):
    data = json.loads((CATALOG / filename).read_text(encoding="utf-8"))
    require(data["program_id"] == PROGRAM_ID and
            data["release_repository"] == REPO and
            data["upstream_tag"] in VERSION_PAIR,
            "UPDATE_CATALOG_NOT_PINNED")
    asset = data["platforms"].get(platform)
    require(asset is not None, "UPDATE_PLATFORM_ASSET_MISSING")
    digest = asset["sha256"]
    require(isinstance(digest, str) and len(digest) == 64,
            "UPDATE_DIGEST_INVALID")
    return ManagedArtifactSpec(
        program_id=PROGRAM_ID, slug=data["slug"],
        release_id=data["upstream_tag"], platform=platform,
        executable_name=asset["executable"],
        archive_name=asset["asset"], archive_format="binary",
        archive_sha256=digest, executable_sha256=digest,
        download_url=f"https://github.com/{REPO}/releases/download/"
                     f"{data['upstream_tag']}/{asset['asset']}",
    )


def manifest(spec):
    return {
        "schema": "IWXA_CLI_PROGRAM/1", "program_id": spec.program_id,
        "executable_name": spec.executable_name,
        "version_args": ["--version"], "version_contains": spec.release_id,
        "operations": {"evaluate": ["-n", "{expression}"]},
    }


def connect_and_run(spec, executable, expression, answer):
    connection = CliConnection(manifest(spec), str(executable))
    require(connection.inspect()["status"] == "DISCOVERED",
            "UPDATE_EXECUTABLE_NOT_DISCOVERED")
    require(connection.connect()["status"] == "CONNECTED",
            "UPDATE_VERSION_HANDSHAKE_FAILED")
    before = connection.capabilities()
    require(not before["operations"][0]["verified"],
            "UPDATE_CAPABILITY_FALSE_POSITIVE")
    result = connection.invoke("evaluate", {"expression": expression})
    require(result["status"] == "EXECUTED" and
            result["output"].strip() == str(answer), "UPDATE_REAL_EXECUTION_FAILED")
    require(connection.capabilities()["operations"][0]["verified"],
            "UPDATE_CAPABILITY_NOT_VERIFIED")
    connection.disconnect()
    require(connection.capabilities()["connected"] is False,
            "UPDATE_CONNECTION_NOT_DISCONNECTED")
    return result


def write_choice(file, current, target, selected, installation):
    # This is the disposable host's selection, NOT an installer-owned registry.
    data = {
        "schema": "IWXA_U01_TEST_HOST_CHOICE/1",
        "program_id": PROGRAM_ID,
        "current": current.as_dict(), "target": target.as_dict(),
        "installation": str(installation), "active_release": selected.release_id,
    }
    temporary = file.with_name(file.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(data, stream, sort_keys=True)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, file)


def inspect_choice(file):
    data = json.loads(file.read_text(encoding="utf-8"))
    require(data["schema"] == "IWXA_U01_TEST_HOST_CHOICE/1" and
            data["program_id"] == PROGRAM_ID, "UPDATE_HOST_STATE_INVALID")
    current = ManagedArtifactSpec.from_mapping(data["current"])
    target = ManagedArtifactSpec.from_mapping(data["target"])
    require(data["active_release"] in (current.release_id, target.release_id),
            "UPDATE_HOST_SELECTED_UNKNOWN_VERSION")
    selected = current if data["active_release"] == current.release_id else target
    backend = ManagedArtifactBackend()
    installed = backend.verify(selected, data["installation"])
    require(installed.installed and installed.sha256 == selected.executable_sha256,
            "ACTIVE_RELEASE_UNVERIFIED")
    result = connect_and_run(selected, installed.executable, "7 * 8", 56)
    return {
        "schema": "IWXA_U01_RESUMED_HOST_RESULT/1", "status": "PASS",
        "pid": os.getpid(), "active_release": selected.release_id,
        "sha256": installed.sha256, "output": result["output"].strip(),
    }


def restart_host(file, *, must_succeed=True):
    cmd = [sys.executable, str(Path(__file__).resolve()), "--resume", str(file)]
    options = {"creationflags": subprocess.CREATE_NO_WINDOW} if os.name == "nt" else {}
    result = subprocess.run(cmd, text=True, capture_output=True,
                            timeout=35, check=False, **options)
    if not must_succeed:
        require(result.returncode != 0 and
                "ACTIVE_RELEASE_UNVERIFIED" in result.stderr,
                "UPDATE_DAMAGED_ACTIVE_VERSION_WAS_ACCEPTED")
        return {"status": "REFUSED_DAMAGED_ACTIVE", "returncode": result.returncode}
    require(result.returncode == 0,
            "UPDATE_RESTART_FAILED_" + result.stderr[-200:])
    report = json.loads(result.stdout.strip())
    require(report["status"] == "PASS" and report["pid"] != os.getpid(),
            "UPDATE_CROSS_PROCESS_READBACK_FAILED")
    return report


def approve_update(current, target, root):
    return ApprovedUpdate(
        current.program_id, current.fingerprint, target.fingerprint,
        "PINS-real-yq-update", str(root), True,
    )


def qualify():
    started = time.monotonic()
    platform = _native_platform()
    current = spec_from_catalog("yq-4.53.3.json", platform)
    target = spec_from_catalog("yq-4.54.1.json", platform)
    require((current.release_id, target.release_id) == VERSION_PAIR,
            "UPDATE_VERSION_PAIR_INVALID")
    require(current.executable_name == target.executable_name and
            current.executable_sha256 != target.executable_sha256,
            "UPDATE_BINARY_IDENTITY_INVALID")
    backend = ManagedArtifactBackend()
    with tempfile.TemporaryDirectory(prefix="iwxa-u01-real-update-") as temp:
        root = Path(temp).resolve()
        installation, stage = root / "software", root / "stage"
        installation.mkdir()
        user_file = installation / "personal-user-file.txt"
        user_file.write_text("KEEP", encoding="utf-8")
        choice_file = installation / "host-selected-version.json"

        require(backend.verify(current, str(installation)).status == "NOT_INSTALLED",
                "UPDATE_OLD_PREEXISTS")
        old_stage = backend.prepare(current, str(stage))
        require(old_stage.status == "STAGED_NOT_ACTIVATED",
                "UPDATE_OLD_NOT_STAGED")
        old_installed = backend.apply(
            current, str(stage),
            ApprovedInstall(PROGRAM_ID, current.fingerprint,
                            "PINS-real-yq-original", str(installation), True))
        require(old_installed.installed and
                old_installed.sha256 == current.executable_sha256,
                "UPDATE_OLD_INSTALL_FAILED")
        first = connect_and_run(current, old_installed.executable, "6 * 7", 42)
        write_choice(choice_file, current, target, current, installation)
        require(restart_host(choice_file)["active_release"] == current.release_id,
                "UPDATE_OLD_RESTART_FAILED")

        before = backend.plan_update(current, target, str(installation))
        require(before.status == "UPDATE_AVAILABLE" and before.rollback_available,
                "UPDATE_PLAN_INVALID")
        try:
            backend.apply_update(
                current, target, str(stage),
                ApprovedUpdate(PROGRAM_ID, current.fingerprint,
                               target.fingerprint, "PINS-update-denied",
                               str(installation), False))
            raise RuntimeError("UPDATE_WITHOUT_APPROVAL_ALLOWED")
        except InstallError as error:
            require(error.code == "UPDATE_OWNER_APPROVAL_REQUIRED",
                    "UPDATE_DENIAL_ERROR_INVALID")
        require(backend.verify(target, str(installation)).status == "NOT_INSTALLED",
                "UPDATE_UNAUTHORIZED_WRITES")

        newer_stage = backend.prepare(target, str(stage))
        require(newer_stage.status == "STAGED_NOT_ACTIVATED",
                "UPDATE_NEW_NOT_STAGED")
        ready = backend.apply_update(
            current, target, str(stage), approve_update(current, target, installation))
        require(ready.status == "UPDATE_READY_FOR_ACTIVATION" and
                ready.current_installed and ready.target_installed and
                ready.rollback_available and
                not ready.activated and not ready.receipt_written,
                "UPDATE_FALSE_ACTIVATION")
        require(ready.current_sha256 == current.executable_sha256 and
                ready.target_sha256 == target.executable_sha256,
                "UPDATE_POST_INSTALL_HASH_INVALID")
        require(json.loads(choice_file.read_text(encoding="utf-8"))
                ["active_release"] == current.release_id,
                "UPDATE_UNAUTHORIZED_ACTIVATION")
        connect_and_run(target, ready.target_executable, "7 * 8", 56)
        connect_and_run(current, ready.current_executable, "6 * 7", 42)

        write_choice(choice_file, current, target, target, installation)
        restarted_new = restart_host(choice_file)
        require(restarted_new["active_release"] == target.release_id,
                "UPDATE_SWITCH_NOT_PERSISTED")
        require(ManagedArtifactBackend().recover_update(
            current, target, str(installation)) == ready,
            "UPDATE_BACKEND_RESTART_READBACK_FAILED")

        # Corrupt only the new version, never the old or unrelated user data.
        updated_path = Path(ready.target_executable)
        updated_path.chmod(0o600)
        updated_path.write_bytes(b"damaged-new-release")
        damaged = backend.recover_update(current, target, str(installation))
        require(damaged.status == "UPDATE_TARGET_INVALID" and
                damaged.current_installed and not damaged.target_installed and
                damaged.rollback_available, "UPDATE_CORRUPTION_NOT_DETECTED")
        restart_host(choice_file, must_succeed=False)
        write_choice(choice_file, current, target, current, installation)
        recovered_old = restart_host(choice_file)
        require(recovered_old["active_release"] == current.release_id,
                "UPDATE_ROLLBACK_FAILED")
        require(user_file.read_text(encoding="utf-8") == "KEEP",
                "UPDATE_USER_DATA_DAMAGED")
        require(backend.remove(
            target, ApprovedRemoval(PROGRAM_ID, target.fingerprint,
                                    "PINS-remove-corrupt-new",
                                    str(installation), True)).status == "REMOVED",
                "UPDATE_DAMAGED_NEW_REMOVAL_FAILED")
        require(backend.verify(current, str(installation)).installed,
                "UPDATE_OLD_REMOVED_BY_ROLLBACK")

        # Retry is an explicit new host decision, using the original pinned
        # staging bytes, not an implicit download or unsafe in-place repair.
        retried = backend.apply_update(
            current, target, str(stage), approve_update(current, target, installation))
        require(retried.status == "UPDATE_READY_FOR_ACTIVATION",
                "UPDATE_RETRY_FAILED")
        connect_and_run(target, retried.target_executable, "7 * 8", 56)
        write_choice(choice_file, current, target, target, installation)
        require(restart_host(choice_file)["active_release"] == target.release_id,
                "UPDATE_RETRY_READBACK_FAILED")
        write_choice(choice_file, current, target, current, installation)

        removed_new = backend.remove(
            target, ApprovedRemoval(PROGRAM_ID, target.fingerprint,
                                    "PINS-remove-new", str(installation), True))
        require(removed_new.status == "REMOVED" and
                backend.verify(current, str(installation)).installed,
                "UPDATE_NEW_REMOVAL_DAMAGED_OLD")
        require(restart_host(choice_file)["active_release"] == current.release_id,
                "UPDATE_OLD_UNUSABLE_AFTER_NEW_REMOVAL")
        removed_old = backend.remove(
            current, ApprovedRemoval(PROGRAM_ID, current.fingerprint,
                                     "PINS-remove-old", str(installation), True))
        require(removed_old.status == "REMOVED" and
                backend.verify(current, str(installation)).status == "NOT_INSTALLED" and
                backend.verify(target, str(installation)).status == "NOT_INSTALLED",
                "UPDATE_REMOVAL_READBACK_INVALID")
        require(user_file.read_text(encoding="utf-8") == "KEEP" and
                choice_file.is_file() and
                Path(old_stage.executable).is_file() and
                Path(newer_stage.executable).is_file(),
                "UPDATE_UNRELATED_DATA_CHANGED")

        report = {
            "schema": "IWXA_U01_REAL_SOFTWARE_UPDATE/1", "status": "PASS",
            "platform": platform, "program_id": PROGRAM_ID,
            "current": current.release_id, "target": target.release_id,
            "old_sha256": current.executable_sha256,
            "new_sha256": target.executable_sha256,
            "old_real_output": first["output"].strip(),
            "new_real_output": restarted_new["output"],
            "separate_process_restart": True,
            "host_only_activation": True,
            "damaged_new_rejected_after_restart": True,
            "rollback_old_invoked_after_restart": True,
            "idempotent_backend_without_registry": True,
            "explicit_retry_after_corruption": True,
            "both_versions_removed_after_verification": True,
            "user_data_and_staging_preserved": True,
            "duration_ms": round((time.monotonic() - started) * 1000, 3),
        }
        print(json.dumps(report, sort_keys=True), flush=True)
        return report


if __name__ == "__main__":
    if len(sys.argv) == 3 and sys.argv[1] == "--resume":
        try:
            print(json.dumps(inspect_choice(Path(sys.argv[2])), sort_keys=True))
        except Exception as error:
            print(str(error), file=sys.stderr)
            raise SystemExit(2)
    else:
        qualify()
