"""Private evidence files. Directories are 0700, files 0600, refs never leave the root."""
from __future__ import annotations

import hashlib
import os
import re
from dataclasses import dataclass
from pathlib import Path

_SAFE_PART = re.compile(r"[A-Za-z0-9_-]{1,64}")
_SAFE_NAME = re.compile(r"[a-z]{1,16}\.(png|jpeg)")
_REF = re.compile(r"([A-Za-z0-9_-]{1,64})/([A-Za-z0-9_-]{1,64}-[a-z]{1,16}\.(?:png|jpeg))")


class EvidenceError(Exception):
    pass


@dataclass(frozen=True)
class StoredFile:
    ref: str
    sha256: str


def sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


class EvidenceStore:
    def __init__(self, root: Path) -> None:
        self._root = root
        root.mkdir(parents=True, exist_ok=True)
        os.chmod(root, 0o700)

    def _path(self, ref: str) -> Path:
        if not _REF.fullmatch(ref):
            raise EvidenceError("Invalid evidence reference.")
        return self._root / ref

    def save(self, inspection_id: str, capture_id: str, name: str, data: bytes) -> StoredFile:
        if not (_SAFE_PART.fullmatch(inspection_id) and _SAFE_PART.fullmatch(capture_id)
                and _SAFE_NAME.fullmatch(name)):
            raise EvidenceError("Unsafe evidence name.")
        folder = self._root / inspection_id
        folder.mkdir(mode=0o700, exist_ok=True)
        ref = f"{inspection_id}/{capture_id}-{name}"
        descriptor = os.open(self._path(ref), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
        return StoredFile(ref, sha256(data))

    def read(self, ref: str) -> bytes:
        try:
            return self._path(ref).read_bytes()
        except OSError as error:
            raise EvidenceError("Evidence file is not available.") from error

    def delete(self, ref: str) -> None:
        self._path(ref).unlink(missing_ok=True)
