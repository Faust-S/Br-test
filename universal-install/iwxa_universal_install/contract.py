"""A small reusable backend contract, not another installer/registry/store.

The embedding application owns its catalog, approval, plan digest, receipts,
concurrency control, update policy, and the connection to a program provider.
Backends receive an approved operation and return verifiable observations.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Protocol


class InstallError(RuntimeError):
    """Stable error code; do not include user paths or secrets."""

    def __init__(self, code: str):
        self.code = code
        super().__init__(code)


@dataclass(frozen=True)
class ApprovedInstall:
    """Host-issued input. Construction alone does NOT prove real user consent.

    The host MUST validate the user decision and current immutable plan before
    issuing this value and must record STARTED before invoking apply().
    """
    program_id: str
    spec_fingerprint: str
    receipt_id: str
    installation_root: str
    confirmed: bool


@dataclass(frozen=True)
class ApprovedRemoval:
    """Owner-authorized removal of exactly one pinned installed version.

    The embedding product MUST obtain explicit consent, record its removal
    receipt and stop the Worker before calling the backend. This object is an
    input contract, not a new authority or a proof of user identity.
    """
    program_id: str
    spec_fingerprint: str
    receipt_id: str
    installation_root: str
    confirmed: bool


@dataclass(frozen=True)
class InstallObservation:
    """Readback of actual files, not provider readiness or a durable receipt."""
    status: str
    program_id: str
    executable: str | None = None
    sha256: str | None = None
    source: str = "IWXA_MANAGED_ARTIFACT_V1"
    installed: bool = False
    connected: bool = False
    receipt_written: bool = False
    external_process_started: bool = False


class ProgramInstallBackend(Protocol):
    family: str

    def inspect(self, spec: Any, installation_root: str) -> InstallObservation: ...
    def prepare(self, spec: Any, staging_root: str, *,
                archive_path: str | None = None,
                on_progress: Callable[[int, int | None], None] | None = None,
                is_cancelled: Callable[[], bool] | None = None) -> Any: ...
    def apply(self, spec: Any, staging_root: str, approval: ApprovedInstall) -> InstallObservation: ...
    def remove(self, spec: Any, approval: ApprovedRemoval) -> InstallObservation: ...
    def verify(self, spec: Any, installation_root: str) -> InstallObservation: ...
    def recover(self, spec: Any, installation_root: str) -> InstallObservation: ...
