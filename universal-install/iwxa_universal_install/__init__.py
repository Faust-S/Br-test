"""Portable IWXA program-install backends; host owns consent, receipts and activation."""
from .contract import ApprovedInstall, ApprovedRemoval, ApprovedUpdate, InstallError, InstallObservation, UpdateObservation, ProgramInstallBackend
from .manifest import ManagedArtifactSpec
from .managed import ManagedArtifactBackend

__all__ = [
    "ApprovedInstall", "ApprovedRemoval", "ApprovedUpdate", "InstallError", "InstallObservation",
    "UpdateObservation", "ProgramInstallBackend",
    "ManagedArtifactSpec", "ManagedArtifactBackend", "ObscuraManagedBackend",
    "NativePackageSpec", "NativePackageBackend",
]

def __getattr__(name):
    """Obscura is optional; generic consumers must not import its preparer."""
    if name == "ObscuraManagedBackend":
        from .obscura import ObscuraManagedBackend
        return ObscuraManagedBackend
    if name in ("NativePackageSpec", "NativePackageBackend"):
        from .native import NativePackageSpec, NativePackageBackend
        return {"NativePackageSpec": NativePackageSpec,
                "NativePackageBackend": NativePackageBackend}[name]
    raise AttributeError(name)
