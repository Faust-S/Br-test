"""Native vendor-EXE lifecycle test using a disposable NSIS installer.

The shared backend receives vendor-specific silent switches and a readback
command through a manifest. No product-specific code is added to the backend.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
import tempfile

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "universal-install"))
from iwxa_universal_install import (
    ApprovedInstall, ApprovedRemoval, ManagedArtifactSpec,
    NativePackageBackend, NativePackageSpec,
)
from iwxa_universal_install.managed import _native_platform


def nsis_tool() -> Path:
    found = shutil.which("makensis.exe") or shutil.which("makensis")
    if found:
        return Path(found)
    roots = [
        Path(os.environ.get("ProgramFiles(x86)", "C:/Program Files (x86)")) / "NSIS" / "makensis.exe",
        Path(os.environ.get("ProgramFiles", "C:/Program Files")) / "NSIS" / "makensis.exe",
    ]
    for path in roots:
        if path.is_file():
            return path
    raise RuntimeError("EXE_SMOKE_NSIS_NOT_INSTALLED")


def qualify():
    if sys.platform != "win32" or _native_platform() not in ("windows-x86_64", "windows-arm64"):
        raise RuntimeError("EXE_SMOKE_WINDOWS_ONLY")
    with tempfile.TemporaryDirectory(prefix="iwxa-native-exe-ci-") as temp:
        root = Path(temp).resolve()
        installed_dir = root / "installed-by-vendor"
        payload = root / "hello.txt"
        payload.write_text("verified native vendor EXE fixture\n", encoding="utf-8")
        nsis = root / "fixture.nsi"
        setup = root / "iwxa-native-exe-ci.exe"
        uninstall = installed_dir / "Uninstall.exe"
        expected = installed_dir / "hello.txt"
        nsis.write_text(
            'Unicode true\n'
            'Name "IWXA CI Vendor EXE"\n'
            'OutFile "' + str(setup) + '"\n'
            'InstallDir "' + str(installed_dir) + '"\n'
            'RequestExecutionLevel user\n'
            'SilentInstall silent\n'
            'Section "Install"\n'
            '  SetOutPath "$INSTDIR"\n'
            '  File "' + str(payload) + '"\n'
            '  WriteUninstaller "$INSTDIR\\Uninstall.exe"\n'
            'SectionEnd\n'
            'Section "Uninstall"\n'
            '  Delete "$INSTDIR\\hello.txt"\n'
            '  Delete "$INSTDIR\\Uninstall.exe"\n'
            '  RMDir "$INSTDIR"\n'
            'SectionEnd\n', encoding="utf-8"
        )
        subprocess.run([str(nsis_tool()), str(nsis)], check=True,
                       capture_output=True, text=True, timeout=60)
        check_script = root / "probe.py"
        check_script.write_text(
            "from pathlib import Path\nimport sys\n"
            "file=Path(sys.argv[1])\n"
            "sys.exit(0 if file.is_file() and "
            "file.read_text(encoding='utf-8')=='verified native vendor EXE fixture\\n' else 1)\n",
            encoding="utf-8"
        )
        sha = hashlib.sha256(setup.read_bytes()).hexdigest()
        artifact = ManagedArtifactSpec(
            program_id="NATIVE_EXE_CI_V1", slug="native-exe-ci",
            release_id="1.0.0", platform=_native_platform(),
            executable_name=setup.name, archive_name=setup.name,
            archive_format="exe-installer", archive_sha256=sha,
            executable_sha256=sha,
            download_url="https://github.com/Faust-S/Br-test/releases/download/1.0.0/" + setup.name
        )
        spec = NativePackageSpec(
            artifact, "native-exe-ci",
            install_arguments=("/S",),
            uninstall_arguments=(str(uninstall), "/S"),
            probe_arguments=(str(Path(sys.executable).resolve()), str(check_script), str(expected)),
        )
        backend = NativePackageBackend()
        stage = root / "stage"
        owned = root / "owner"
        owned.mkdir()
        user_data = owned / "unrelated-user-data"
        user_data.write_text("preserve", encoding="utf-8")
        try:
            backend.prepare(spec, str(stage), archive_path=str(setup))
            approval = ApprovedInstall(
                spec.program_id, spec.fingerprint, "PINS-native-exe-install", str(owned), True)
            if not backend.apply(spec, str(stage), approval).installed:
                raise RuntimeError("EXE_SMOKE_INSTALL_INVALID")
            if not backend.recover(spec, str(owned)).installed or not expected.exists():
                raise RuntimeError("EXE_SMOKE_READBACK_INVALID")
            removal = ApprovedRemoval(
                spec.program_id, spec.fingerprint, "PINS-native-exe-remove", str(owned), True)
            if backend.remove(spec, removal).status != "REMOVED":
                raise RuntimeError("EXE_SMOKE_REMOVE_INVALID")
            if backend.recover(spec, str(owned)).installed:
                raise RuntimeError("EXE_SMOKE_REMOVE_READBACK_FAILED")
            if user_data.read_text(encoding="utf-8") != "preserve":
                raise RuntimeError("EXE_SMOKE_USER_DATA_CHANGED")
            result = {
                "schema": "IWXA_NATIVE_EXE_SMOKE/1", "status": "PASS",
                "platform": _native_platform(), "installed": True,
                "verified": True, "removed": True, "user_data_preserved": True,
                "custom_backend_added": False,
            }
            print(json.dumps(result, sort_keys=True))
            return result
        finally:
            if uninstall.is_file():
                subprocess.run([str(uninstall), "/S"], capture_output=True,
                               text=True, timeout=60, check=False)


if __name__ == "__main__":
    qualify()
