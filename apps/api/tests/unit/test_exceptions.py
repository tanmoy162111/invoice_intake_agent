import pytest

from intake.core.exceptions import (
    SPECS,
    ExceptionCode,
    Severity,
    explain,
    sample_params,
)


def test_every_code_has_a_spec() -> None:
    assert set(SPECS) == set(ExceptionCode)


def test_codes_are_upper_snake_case() -> None:
    for code in ExceptionCode:
        assert code.value == code.name
        assert code.value.isupper() and " " not in code.value


@pytest.mark.parametrize("code", list(ExceptionCode))
def test_explanation_renders_with_sample_params(code: ExceptionCode) -> None:
    text = explain(code, **sample_params(code))
    assert text and "{" not in text and "}" not in text


@pytest.mark.parametrize("code", list(ExceptionCode))
def test_spec_has_fix_and_severity(code: ExceptionCode) -> None:
    spec = SPECS[code]
    assert spec.suggested_fix.strip()
    assert isinstance(spec.severity, Severity)


def test_missing_param_is_an_error_not_a_blank() -> None:
    with pytest.raises(KeyError):
        explain(ExceptionCode.PRICE_VARIANCE)


@pytest.mark.parametrize(
    ("code", "severity"),
    [
        (ExceptionCode.UNREADABLE_DOCUMENT, Severity.BLOCK),
        (ExceptionCode.BANK_DETAILS_CHANGED, Severity.BLOCK),
        (ExceptionCode.POSSIBLE_DUPLICATE, Severity.BLOCK),
        (ExceptionCode.PO_OVERBILLED, Severity.BLOCK),
        (ExceptionCode.TOTAL_MISMATCH, Severity.REVIEW),
    ],
)
def test_severities_match_playbook(code: ExceptionCode, severity: Severity) -> None:
    assert SPECS[code].severity is severity


def test_bank_change_fix_mentions_phone_verification() -> None:
    assert "phone" in SPECS[ExceptionCode.BANK_DETAILS_CHANGED].suggested_fix.lower()
