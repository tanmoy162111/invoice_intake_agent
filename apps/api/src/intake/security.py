"""Application-level protection of bank details (playbook §10).

Accounts are stored encrypted (Fernet); comparisons use a keyed hash so plaintext is never needed.
"""

import hashlib
import hmac

from cryptography.fernet import Fernet

from intake.core.normalize import normalize_bank_account


class BankVault:
    def __init__(self, key: str) -> None:
        if not key:
            raise ValueError(
                "BANK_ENCRYPTION_KEY is not set (generate one with Fernet.generate_key)"
            )
        self._key = key.encode()
        self._fernet = Fernet(self._key)

    def encrypt(self, account: str) -> str:
        return self._fernet.encrypt(normalize_bank_account(account).encode()).decode()

    def decrypt(self, token: str) -> str:
        return self._fernet.decrypt(token.encode()).decode()

    def hash(self, account: str) -> str:
        digest = hmac.new(self._key, normalize_bank_account(account).encode(), hashlib.sha256)
        return digest.hexdigest()

    @staticmethod
    def mask(account: str) -> str:
        norm = normalize_bank_account(account)
        return "•" * max(len(norm) - 4, 0) + norm[-4:]
