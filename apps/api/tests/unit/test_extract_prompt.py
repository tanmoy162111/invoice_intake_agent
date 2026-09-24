import pytest

from intake.extract.prompt import load_prompt


def test_v1_loads() -> None:
    assert "record_invoice" in load_prompt("v1")


@pytest.mark.parametrize(
    "must_contain",
    [
        "Never invent a value",  # missing fields are null (playbook §5.3)
        "`null`",
        "not instructions",  # prompt-injection guard
        "Copy, don't compute",
        "record_invoice",
    ],
)
def test_v1_keeps_its_safety_rules(must_contain: str) -> None:
    assert must_contain in load_prompt("v1")


@pytest.mark.parametrize("bad", ["", "v", "../v1", "v1.md", "V1", "v1/../v1", "latest"])
def test_bad_version_rejected(bad: str) -> None:
    with pytest.raises(ValueError):
        load_prompt(bad)


def test_unknown_version_rejected() -> None:
    with pytest.raises(ValueError):
        load_prompt("v999")
