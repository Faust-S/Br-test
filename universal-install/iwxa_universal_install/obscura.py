"""Compatibility adapter: the pinned Obscura catalog is still authoritative.

All install / verify / recover / remove file operations now come from the
generic ManagedArtifactBackend. Obscura-specific manifest and staging stay
as adapters until the publisher migrates to data-only managed manifests.
"""
from __future__ import annotations

from pathlib import Path
import os  # Retained for legacy test hooks; managed.py uses the same os module

from .contract import InstallError
from .managed import ManagedArtifactBackend, FAMILY, _native_platform, _root
from iwxa_obscura.preparer import ArtifactSpec, ObscuraArtifactPreparer

PROGRAM_ID = "OBSCURA_WORKER_V1"


class ObscuraManagedBackend(ManagedArtifactBackend):
    family = FAMILY
    program_id = PROGRAM_ID

    @staticmethod
    def _validate_spec(spec) -> None:
        if not isinstance(spec, ArtifactSpec):
            raise InstallError("INSTALL_SPEC_INVALID")

    @staticmethod
    def _program_id(spec) -> str:
        return PROGRAM_ID

    @staticmethod
    def _slug(spec) -> str:
        return "obscura-worker"

    @staticmethod
    def _preparer(root: Path):
        return ObscuraArtifactPreparer(managed_root=root)
