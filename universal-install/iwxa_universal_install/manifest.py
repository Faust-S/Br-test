"""Data-only, pinned manifest for any single-executable managed program.

A trusted embedding product is responsible for approving the catalog entry.
This class validates the package shape; it does not grant permission to install.
"""
from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
import re
from urllib.parse import urlsplit

SCHEMA = "IWXA_MANAGED_ARTIFACT/1.0"
PLATFORMS = frozenset({
    "windows-x86_64", "windows-arm64", "linux-x86_64", "linux-arm64",
    "macos-x86_64", "macos-arm64",
})
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,79}\Z")
_PROGRAM_ID = re.compile(r"[A-Z][A-Z0-9_]{2,79}\Z")
_SHA = re.compile(r"[0-9a-f]{64}\Z")
_REPO = re.compile(r"/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/releases/download/")
# Native package payloads are *staged* by the same pinned downloader, but
# must be applied through NativePackageBackend, never the portable backend.
NATIVE_FORMATS = {"msi": ".msi", "deb": ".deb", "rpm": ".rpm", "pkg": ".pkg", "exe-installer": ".exe"}


@dataclass(frozen=True)
class ManagedArtifactSpec:
    program_id: str
    slug: str
    release_id: str
    platform: str
    executable_name: str
    archive_name: str
    archive_format: str
    archive_sha256: str
    executable_sha256: str
    download_url: str
    schema: str = SCHEMA

    def __post_init__(self):
        if self.schema != SCHEMA:
            raise ValueError("MANAGED_SPEC_SCHEMA_INVALID")
        if not isinstance(self.program_id, str) or not _PROGRAM_ID.fullmatch(self.program_id):
            raise ValueError("MANAGED_SPEC_PROGRAM_ID_INVALID")
        if self.platform not in PLATFORMS:
            raise ValueError("MANAGED_SPEC_PLATFORM_INVALID")
        for token in (self.slug, self.release_id, self.executable_name, self.archive_name):
            if not isinstance(token, str) or not _TOKEN.fullmatch(token) or token in (".", ".."):
                raise ValueError("MANAGED_SPEC_TOKEN_INVALID")
        if not all(isinstance(d, str) and _SHA.fullmatch(d)
                   for d in (self.archive_sha256, self.executable_sha256)):
            raise ValueError("MANAGED_SPEC_DIGEST_INVALID")
        expected = "zip" if self.platform.startswith("windows") else "tar.gz"
        native_suffix = NATIVE_FORMATS.get(self.archive_format)
        if self.archive_format not in (expected, "binary") and native_suffix is None:
            raise ValueError("MANAGED_SPEC_FORMAT_INVALID")
        if self.archive_format == expected and not self.archive_name.endswith("." + expected):
            raise ValueError("MANAGED_SPEC_FORMAT_INVALID")
        if self.archive_format == "binary" and self.archive_sha256 != self.executable_sha256:
            raise ValueError("MANAGED_SPEC_BINARY_DIGEST_MISMATCH")
        if native_suffix:
            native_os = ("windows" if self.archive_format in ("msi", "exe-installer") else
                         "macos" if self.archive_format == "pkg" else "linux")
            if (not self.platform.startswith(native_os + "-") or
                    not self.archive_name.lower().endswith(native_suffix) or
                    not self.executable_name.lower().endswith(native_suffix) or
                    self.archive_sha256 != self.executable_sha256):
                raise ValueError("MANAGED_SPEC_NATIVE_FORMAT_INVALID")
        elif self.platform.startswith("windows") and not self.executable_name.endswith(".exe"):
            raise ValueError("MANAGED_SPEC_WINDOWS_EXECUTABLE_INVALID")
        u = self.download_url
        if not isinstance(u, str) or not 1 <= len(u) <= 2048 or any(
                ord(char) < 33 or ord(char) >= 127 or char == "\\" for char in u):
            raise ValueError("MANAGED_SPEC_URL_INVALID")
        try:
            p = urlsplit(u)
            valid = (p.scheme == "https" and p.hostname == "github.com" and
                     p.port in (None, 443) and not p.username and not p.password and
                     not p.query and not p.fragment and
                     _REPO.match(p.path) is not None and
                     p.path.endswith("/" + self.release_id + "/" + self.archive_name))
        except (ValueError, TypeError):
            valid = False
        if not valid:
            raise ValueError("MANAGED_SPEC_URL_INVALID")

    @classmethod
    def from_mapping(cls, data: dict) -> "ManagedArtifactSpec":
        if not isinstance(data, dict) or set(data) != set(cls.__dataclass_fields__):
            raise ValueError("MANAGED_SPEC_FIELDS_INVALID")
        return cls(**data)

    def as_dict(self) -> dict:
        return {name: getattr(self, name) for name in self.__dataclass_fields__}

    @property
    def fingerprint(self) -> str:
        data = json.dumps(self.as_dict(), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return hashlib.sha256(data.encode("utf-8")).hexdigest()
