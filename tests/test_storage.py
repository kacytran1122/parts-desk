import stat
from pathlib import Path

import pytest

from partsdesk.storage.evidence import EvidenceError, EvidenceStore


def test_evidence_files_are_private_and_hashed(tmp_path: Path) -> None:
    store = EvidenceStore(tmp_path / "evidence")
    stored = store.save("abc123", "cap1", "original.png", b"\x89PNGdata")
    assert stored.ref == "abc123/cap1-original.png"
    assert len(stored.sha256) == 64
    assert store.read(stored.ref) == b"\x89PNGdata"
    assert stat.S_IMODE((tmp_path / "evidence").stat().st_mode) == 0o700
    assert stat.S_IMODE((tmp_path / "evidence" / stored.ref).stat().st_mode) == 0o600


@pytest.mark.parametrize("ref", ["../escape.png", "/etc/passwd", "a/../../b", "a/b/c.png"])
def test_evidence_refs_cannot_escape_the_store(tmp_path: Path, ref: str) -> None:
    store = EvidenceStore(tmp_path / "evidence")
    with pytest.raises(EvidenceError):
        store.read(ref)


def test_unsafe_names_are_refused(tmp_path: Path) -> None:
    store = EvidenceStore(tmp_path / "evidence")
    with pytest.raises(EvidenceError):
        store.save("../x", "cap", "original.png", b"x")


def test_delete_is_idempotent(tmp_path: Path) -> None:
    store = EvidenceStore(tmp_path / "evidence")
    stored = store.save("abc", "cap", "crop.png", b"x")
    store.delete(stored.ref)
    store.delete(stored.ref)
    with pytest.raises(EvidenceError):
        store.read(stored.ref)
