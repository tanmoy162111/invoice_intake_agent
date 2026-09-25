import pytest

from intake.core.exceptions import (
    CHECK_SUBJECTS,
    SPECS,
    VARIANTS,
    ExceptionCode,
    Severity,
    explain,
    explain_unchecked,
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


# ---- variants and "could not be checked" wording (M7) ----------------------------------------


@pytest.mark.parametrize("key", list(VARIANTS))
def test_every_variant_renders_with_its_sample_params(key: tuple[ExceptionCode, str]) -> None:
    code, name = key
    variant = VARIANTS[key]
    text = explain(code, variant=name, **variant.sample_params)
    assert text and "{" not in text and "}" not in text


def test_an_unknown_variant_is_an_error() -> None:
    with pytest.raises(KeyError):
        explain(ExceptionCode.QTY_VARIANCE, variant="nope")


@pytest.mark.parametrize("code", list(ExceptionCode))
def test_every_code_can_say_it_could_not_be_checked(code: ExceptionCode) -> None:
    text = explain_unchecked(code, "the total was not read")
    assert text.startswith(CHECK_SUBJECTS[code][0].upper())
    assert "could not be checked" in text and "the total was not read" in text
    assert "{" not in text and "a person" in text


def test_changed_templates_still_read_like_the_playbook_examples() -> None:
    assert (
        explain(
            ExceptionCode.TOTAL_MISMATCH,
            basis="The lines add up to",
            expected="USD 2,140.00",
            field="total",
            actual="USD 2,410.00",
        )
        == "The lines add up to USD 2,140.00, but the invoice total is USD 2,410.00."
    )
    assert (
        explain(ExceptionCode.PO_NOT_FOUND, po="PO-7781", problem="isn't in the system")
        == "PO-7781 isn't in the system."
    )
