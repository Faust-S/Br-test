"""Portable IWXA program-install backends; host owns consent, receipts and activation."""
from .contract import ApprovedInstall, ApprovedRemoval, InstallError, InstallObservation, ProgramInstallBackend
from .manifest import ManagedArtifactSpec
from .managed import ManagedArtifactBackend

__all__ = [
    "ApprovedInstall", "ApprovedRemoval", "InstallError", "InstallObservation", "ProgramInstallBackend",
    "ManagedArtifactSpec", "ManagedArtifactBackend", "ObscuraManagedBackend",
]

def __getattr__(name):
    """Obscura is optional; generic consumers must not import its preparer."""
    if name == "ObscuraManagedBackend":
        from .obscura import ObscuraManagedBackend
        return ObscuraManagedBackend
    raise AttributeError(name)
