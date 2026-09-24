import uuid
from pathlib import Path

import pytest

from intake.ingest.storage import LocalStorage

SHA = "a" * 64


def test_original_path_is_content_addressed() -> None:
    t = uuid.UUID(int=1)
    assert LocalStorage.original_rel(t, SHA) == f"{t}/originals/aa/{SHA}"


@pytest.mark.parametrize("bad", ["../x", "A" * 64, "a" * 63, "a" * 64 + "/..", ""])
def test_original_path_rejects_non_hash_input(bad: str) -> None:
    with pytest.raises(ValueError):
        LocalStorage.original_rel(uuid.UUID(int=1), bad)


def test_put_read_and_keep_existing(tmp_path: Path) -> None:
    s = LocalStorage(tmp_path)
    s.put("t/originals/aa/x", b"one")
    s.put("t/originals/aa/x", b"two")  # content-addressed: the first write is kept
    assert s.read("t/originals/aa/x") == b"one"
    s.overwrite("t/pages/p", b"1")
    s.overwrite("t/pages/p", b"2")
    assert s.read("t/pages/p") == b"2"
    assert not list(tmp_path.rglob("*.tmp"))


def test_path_traversal_is_blocked(tmp_path: Path) -> None:
    s = LocalStorage(tmp_path / "store")
    with pytest.raises(ValueError):
        s.put("../evil", b"x")
    with pytest.raises(ValueError):
        s.read("/etc/passwd")
