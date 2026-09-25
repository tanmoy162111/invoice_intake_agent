"""Keeps the report and manual honest: they must not drift from the code."""

import re
from pathlib import Path

import pytest

from intake.config import Settings
from intake.core.dedupe import DedupeSettings
from intake.core.exceptions import ExceptionCode
from intake.core.extraction import NormalizeError
from intake.core.ingest import IngestErrorCode
from intake.core.llm_budget import ExtractionFailure
from intake.core.validate import CheckCode, ValidationSettings
from intake.main import create_app

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


@pytest.mark.parametrize("code", [c.value for c in NormalizeError])
def test_manual_explains_every_reason_a_value_is_left_empty(code: str) -> None:
    assert f"`{code}`" in MANUAL


@pytest.mark.parametrize("code", [c.value for c in ExtractionFailure])
def test_manual_explains_every_reason_an_invoice_can_fail(code: str) -> None:
    assert f"`{code}" in MANUAL  # JOB_FAILED is written `JOB_FAILED:<Class>`


EXTRACTION_SETTING_PREFIXES = (
    "llm_", "ollama_", "extraction_", "extract_", "daily_", "field_", "validation_", "app_",
    "dedupe_",
)  # fmt: skip


@pytest.mark.parametrize(
    "name",
    [n for n in Settings.model_fields if n.startswith(EXTRACTION_SETTING_PREFIXES)],
)
def test_manual_lists_every_extraction_setting(name: str) -> None:
    assert name.upper() in MANUAL, f"{name.upper()} is missing from manual section 5.4"


def test_manual_lists_every_api_route() -> None:
    app = create_app(Settings(database_url="postgresql+psycopg://x:y@localhost/none"))
    for path, methods in app.openapi()["paths"].items():
        for method in methods:
            assert f"`{method.upper()} {path}`" in MANUAL, f"{method.upper()} {path} not documented"


@pytest.mark.parametrize("code", [c.value for c in CheckCode])
def test_manual_explains_every_validation_check(code: str) -> None:
    section = MANUAL.split("### 4.10")[1].split("### 4.11")[0]
    assert f"`{code}`" in section


@pytest.mark.parametrize("name", list(ValidationSettings.__dataclass_fields__))
def test_manual_lists_every_tenant_validation_setting(name: str) -> None:
    section = MANUAL.split("### 4.11")[1].split("## 5. Reference")[0]
    assert f"`{name}`" in section, f"{name} is missing from manual section 4.11"


@pytest.mark.parametrize("key", ["dedupe_window_days", "dedupe_number_similarity_min"])
def test_manual_lists_the_tenant_duplicate_settings(key: str) -> None:
    from dataclasses import fields

    assert {f.name for f in fields(DedupeSettings)} == {"window_days", "number_similarity_min"}
    section = MANUAL.split("### 4.11")[1].split("## 5. Reference")[0]
    assert f"`{key}`" in section


def test_manual_explains_every_reason_a_duplicate_check_is_skipped() -> None:
    section = MANUAL.split("### 4.12")[1].split("## 5. Reference")[0]
    for reason in ("NO_SUPPLIER", "NO_INVOICE_NUMBER", "INCOMPLETE_FOR_SOFT_MATCH",
                   "EARLIER_INVOICES_STILL_PENDING", "SUPPLIER_UNCERTAIN",
                   "TOO_MANY_TO_COMPARE"):  # fmt: skip
        assert f"`{reason}`" in section
