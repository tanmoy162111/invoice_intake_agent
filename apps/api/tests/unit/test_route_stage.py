from intake.checks.routing import _version_number


def test_rule_versions_are_compared_as_numbers_not_text() -> None:
    assert _version_number("v10") > _version_number("v9") > _version_number("v1")
    assert _version_number("V2") == 2
    assert _version_number("draft") == 0
