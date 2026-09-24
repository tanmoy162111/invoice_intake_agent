"""Acceptance checks on the committed dataset (manifest and ground truth)."""

import json
from collections import Counter

from intake.core.exceptions import ExceptionCode
from intake.seed import DEFAULT_SEED_DIR

MANIFEST = json.loads((DEFAULT_SEED_DIR / "manifest.json").read_text())


def test_manifest_covers_every_exception_code() -> None:
    codes = {c for row in MANIFEST for c in row["must_raise"]}
    assert codes == {c.value for c in ExceptionCode}


def test_two_deliberately_unreadable_files() -> None:
    unreadable = [r for r in MANIFEST if "UNREADABLE_DOCUMENT" in r["must_raise"]]
    assert len(unreadable) == 2


def test_every_manifest_row_has_a_file_and_truth() -> None:
    for row in MANIFEST:
        assert (DEFAULT_SEED_DIR / "invoices" / row["file"]).is_file()
        truth = json.loads((DEFAULT_SEED_DIR / "truth" / f"{row['id']}.json").read_text())
        assert truth["file"] == row["file"]
        assert truth["expected"]["must_raise"] == row["must_raise"]


def test_dataset_size_and_quality_mix() -> None:
    assert len(MANIFEST) == 120
    q = Counter(r["doc_quality"] for r in MANIFEST)
    assert {"clean", "scanned", "photo"} <= set(q)


def test_golden_set_is_a_copy_of_the_golden_split() -> None:
    golden_dir = DEFAULT_SEED_DIR.parent / "golden"
    golden_ids = {r["id"] for r in MANIFEST if r["split"] == "golden"}
    on_disk = {p.stem for p in (golden_dir / "truth").glob("*.json")}
    assert on_disk == golden_ids
    assert 55 <= len(golden_ids) <= 65
    codes = {c for r in MANIFEST if r["split"] == "golden" for c in r["must_raise"]}
    assert codes == {c.value for c in ExceptionCode}
