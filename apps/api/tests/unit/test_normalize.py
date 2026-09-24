from intake.core.normalize import normalize_bank_account


def test_bank_account_strips_spaces_and_dashes_and_uppercases() -> None:
    assert normalize_bank_account("gb29 nwbk-6016 1331 9268 19") == "GB29NWBK60161331926819"


def test_bank_account_empty() -> None:
    assert normalize_bank_account(" - ") == ""
