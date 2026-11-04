# IWXA Universal Install — native qualification, 2026-11-04

Public branch: `work/2026-11-03-universal-install-module`. This is a reusable backend for future Agent 0.3 integration; Agent 0.3 was not modified.

## Confirmed GitHub-hosted qualifications

- Generic contract, native adapters and isolated fixtures: 6/6 PASS — [run 41809338273](https://github.com/Faust-S/Br-test/actions/runs/41809338273).
- Genuine MSI on Windows x64: install, verify, remove PASS.
- Genuine NSIS vendor EXE on Windows x64: install, verify, delayed uninstall, remove/readback PASS. The generic backend gained no NSIS-specific logic. The confirmed asynchronous removal regression was fixed with bounded observation, never a repeated uninstall.
- Genuine DEB and RPM on Linux x64 and ARM64: install, verify, remove PASS.
- Genuine PKG on macOS Intel and ARM64: install, verify and vendor-provided uninstall PASS; without an explicit vendor uninstall command, removal is rejected.
- Existing Obscura native regression: 6/6 PASS — [run 41806079608](https://github.com/Faust-S/Br-test/actions/runs/41806079608).
- Direct jq network install: 5/5 PASS — [run 41806079921](https://github.com/Faust-S/Br-test/actions/runs/41806079921).
- Direct yq network install: 6/6 PASS — [run 41806079676](https://github.com/Faust-S/Br-test/actions/runs/41806079676).

Native installation/removal now validates the owner receipt ID. The component still delegates authority, elevation, receipts, serial execution and user-facing lifecycle decisions to its embedding host.

## Honest limits

This does **not** mean every MSI or EXE installer will work. Native MSI/EXE ARM64, non-NSIS EXE conventions, package conflicts and complex system-level operations remain unqualified. macOS `pkgutil --forget` alone is **not** removal: for PKG uninstall the native manifest must provide a working vendor command, followed by readback. System-installed packages do not necessarily preserve old versions side-by-side, unlike portable binaries.

No new manager, registry, package Store, external Python dependency or private Agent changes were introduced. Only the single public branch was updated.
