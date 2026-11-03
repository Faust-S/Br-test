"""Portable IWXA program-install backends; host owns consent, receipts and activation."""
from .contract import ApprovedInstall, ApprovedRemoval, InstallError, InstallObservation, ProgramInstallBackend
from .manifest import ManagedArtifactSpec
from .managed import ManagedArtifactBackend
from .obscura import ObscuraManagedBackend

__all__ = [
    "ApprovedInstall", "ApprovedRemoval", "InstallError", "InstallObservation", "ProgramInstallBackend",
    "ManagedArtifactSpec", "ManagedArtifactBackend", "ObscuraManagedBackend",
]
