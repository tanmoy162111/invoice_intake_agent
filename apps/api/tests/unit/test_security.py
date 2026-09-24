import pytest
from cryptography.fernet import Fernet

from intake.security import BankVault


@pytest.fixture
def vault() -> BankVault:
    return BankVault(Fernet.generate_key().decode())


def test_roundtrip(vault: BankVault) -> None:
    token = vault.encrypt("GB29 NWBK 6016 1331 9268 19")
    assert "GB29" not in token
    assert vault.decrypt(token) == "GB29NWBK60161331926819"


def test_hash_ignores_formatting_and_is_keyed(vault: BankVault) -> None:
    assert vault.hash("GB29 NWBK 6016") == vault.hash("gb29-nwbk-6016")
    other = BankVault(Fernet.generate_key().decode())
    assert vault.hash("GB29NWBK6016") != other.hash("GB29NWBK6016")


def test_mask_shows_last_four_only() -> None:
    assert BankVault.mask("GB29 NWBK 6016") == "••••••••6016"


def test_missing_key_is_rejected() -> None:
    with pytest.raises(ValueError):
        BankVault("")
