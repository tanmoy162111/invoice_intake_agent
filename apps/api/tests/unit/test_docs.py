"""Keeps the report and manual honest: they must not drift from the code."""

import re
from pathlib import Path

import pytest

from intake.core.exceptions import ExceptionCode
from intake.core.ingest import IngestErrorCode

ROOT = Path(__file__).resolve().parents[4]
DOCS = ROOT / "docs"
REPORT = (DOCS / "report.md").read_text()
MANUAL = (DOCS / "manual.md").read_text()


@pytest.mark.parametrize("code", [c.value for c in ExceptionCode])
def test_manual_explains_every_exception_code(code: str) -> None:
    assert f"`{code}`" in MANUAL


@pytest.mark.parametrize("code", [c.value for c in IngestErrorCode])
def test_manual_explains_every_upload_error(code: str) -> None:
    assert f"`{code}`" in MANUAL


def test_every_make_command_in_the_manual_exists() -> None:
    makefile = (ROOT / "Makefile").read_text()
    targets = set(re.findall(r"^([a-z][a-z-]*):", makefile, flags=re.MULTILINE))
    used = set(re.findall(r"`make ([a-z-]+)`", MANUAL))
    assert used and used <= targets, used - targets


@pytest.mark.parametrize("doc", ["report.md", "manual.md"])
def test_relative_links_resolve(doc: str) -> None:
    text = (DOCS / doc).read_text()
    for target in re.findall(r"\]\((?!http|#)([^)#]+)", text):
        assert (DOCS / target).exists(), f"{doc} links to missing {target}"


def test_report_covers_every_milestone_and_is_dated() -> None:
    for n in range(14):
        assert f"| M{n} |" in REPORT, f"M{n} missing from the status board"
    assert "Last updated:" in REPORT and "Describes:" in MANUAL


def test_report_chapters_have_all_three_layers() -> None:
    chapters = re.split(r"^### (M\d+):", REPORT, flags=re.MULTILINE)[1:]
    built = chapters[0::2]
    bodies = chapters[1::2]
    assert built  # at least M0
    for name, body in zip(built, bodies, strict=True):
        for layer in ("**In plain words.**", "**How it works.**", "**Under the hood.**"):
            assert layer in body, f"{name} is missing {layer}"
