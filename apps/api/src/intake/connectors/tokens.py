"""OAuth tokens for one accounting organisation: kept encrypted, refreshed under a row lock.

Tokens are encrypted with ACCOUNTING_ENCRYPTION_KEY (Fernet) before they reach the database and are
never logged, returned by the API or put into an error message. Providers rotate refresh tokens, so
two workers refreshing at once would invalidate each other: a refresh takes a lock on the connection
row, re-reads it, and uses a newer token if another worker already refreshed.
"""

import json
import time
import uuid
from collections.abc import Callable
from dataclasses import dataclass
from typing import Protocol

from cryptography.fernet import Fernet, InvalidToken
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from intake.connectors.accounting import ProviderError
from intake.core.accounting import Failure
from intake.db.models import AccountingConnection

EXPIRY_MARGIN_S = 60


@dataclass(frozen=True)
class Tokens:
    access_token: str
    refresh_token: str
    expires_at: int  # epoch seconds

    def __repr__(self) -> str:  # never let a token reach a log or a traceback
        return "Tokens(<redacted>)"


class TokenProvider(Protocol):
    def access_token(self) -> str: ...

    def refresh(self, rejected: str) -> str:
        """A new access token after `rejected` was refused. If another worker already replaced it,
        that one is returned without calling the provider again."""
        ...


class TokenVault:
    def __init__(self, key: str) -> None:
        if not key:
            raise ValueError(
                "ACCOUNTING_ENCRYPTION_KEY is not set (generate with Fernet.generate_key)"
            )
        self._fernet = Fernet(key.encode())

    def seal(self, tokens: Tokens) -> str:
        body = json.dumps(
            {"a": tokens.access_token, "r": tokens.refresh_token, "e": tokens.expires_at}
        )
        return self._fernet.encrypt(body.encode()).decode()

    def open(self, sealed: str) -> Tokens:
        try:
            d = json.loads(self._fernet.decrypt(sealed.encode()))
            return Tokens(str(d["a"]), str(d["r"]), int(d["e"]))
        except (InvalidToken, ValueError, KeyError, TypeError):
            raise ProviderError(Failure.UNAUTHORISED, detail="stored tokens unreadable") from None


Refresher = Callable[[str], Tokens]  # refresh token -> new tokens; raises ProviderError


class DbTokenProvider:
    def __init__(
        self,
        engine: Engine,
        tenant_id: uuid.UUID,
        provider: str,
        organisation_id: str,
        vault: TokenVault,
        refresher: Refresher,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._engine, self._vault, self._refresher, self._clock = engine, vault, refresher, clock
        self._where = (tenant_id, provider, organisation_id)

    def _row(self, session: Session, lock: bool) -> AccountingConnection:
        stmt = select(AccountingConnection).where(
            AccountingConnection.tenant_id == self._where[0],
            AccountingConnection.provider == self._where[1],
            AccountingConnection.organisation_id == self._where[2],
        )
        row = session.scalars(stmt.with_for_update() if lock else stmt).first()
        if row is None:
            raise ProviderError(Failure.UNAUTHORISED, detail="not connected")
        return row

    def access_token(self) -> str:
        with Session(self._engine) as session:
            tokens = self._vault.open(self._row(session, lock=False).tokens_encrypted)
        if tokens.expires_at - self._clock() > EXPIRY_MARGIN_S:
            return tokens.access_token
        return self.refresh(tokens.access_token)

    def refresh(self, rejected: str) -> str:
        with Session(self._engine) as session:
            row = self._row(session, lock=True)
            current = self._vault.open(row.tokens_encrypted)
            fresh_enough = current.expires_at - self._clock() > EXPIRY_MARGIN_S
            if current.access_token != rejected and fresh_enough:
                return current.access_token  # another worker got there first
            renewed = self._refresher(current.refresh_token)
            row.tokens_encrypted = self._vault.seal(renewed)
            session.commit()
            return renewed.access_token
