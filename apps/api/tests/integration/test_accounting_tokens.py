"""Stored OAuth tokens: encrypted at rest, refreshed once under a row lock, never shown."""

import threading
import time
import uuid

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import Engine, text
from sqlalchemy.orm import Session

from intake.connectors.accounting import ProviderError
from intake.connectors.tokens import DbTokenProvider, Tokens, TokenVault
from intake.core.accounting import Failure
from intake.db.models import AccountingConnection, Tenant

KEY = Fernet.generate_key().decode()
NOW = 1_800_000_000.0


def connect(engine: Engine, vault: TokenVault, tokens: Tokens) -> uuid.UUID:
    with Session(engine) as s:
        t = Tenant(name="tok")
        s.add(t)
        s.flush()
        s.add(
            AccountingConnection(
                tenant_id=t.id,
                provider="xero",
                organisation_id="o1",
                tokens_encrypted=vault.seal(tokens),
            )  # fmt: skip
        )
        s.commit()
        return t.id


def test_tokens_are_encrypted_in_the_database(engine: Engine) -> None:
    vault = TokenVault(KEY)
    tenant = connect(engine, vault, Tokens("plain-access", "plain-refresh", int(NOW) + 1800))
    with engine.connect() as c:
        stored = c.execute(
            text("select tokens_encrypted from accounting_connections where tenant_id=:t"),
            {"t": tenant},
        ).scalar_one()
    assert "plain-access" not in stored and "plain-refresh" not in stored
    assert vault.open(stored).refresh_token == "plain-refresh"


def test_the_wrong_key_cannot_read_them() -> None:
    sealed = TokenVault(KEY).seal(Tokens("a", "r", 1))
    with pytest.raises(ProviderError) as err:
        TokenVault(Fernet.generate_key().decode()).open(sealed)
    assert err.value.failure is Failure.UNAUTHORISED


def test_a_missing_key_is_refused() -> None:
    with pytest.raises(ValueError):
        TokenVault("")


def test_a_valid_token_is_used_without_calling_the_provider(engine: Engine) -> None:
    vault, calls = TokenVault(KEY), []
    tenant = connect(engine, vault, Tokens("A1", "R1", int(NOW) + 1800))

    def refresher(r: str) -> Tokens:
        calls.append(r)
        return Tokens("A2", "R2", int(NOW) + 3600)

    p = DbTokenProvider(engine, tenant, "xero", "o1", vault, refresher, clock=lambda: NOW)
    assert p.access_token() == "A1" and calls == []


def test_an_expired_token_is_refreshed_and_the_rotated_pair_is_saved(engine: Engine) -> None:
    vault, calls = TokenVault(KEY), []
    tenant = connect(engine, vault, Tokens("A1", "R1", int(NOW) - 10))

    def refresher(r: str) -> Tokens:
        calls.append(r)
        return Tokens("A2", "R2", int(NOW) + 1800)

    p = DbTokenProvider(engine, tenant, "xero", "o1", vault, refresher, clock=lambda: NOW)
    assert p.access_token() == "A2" and calls == ["R1"]
    assert p.access_token() == "A2" and calls == ["R1"]  # the saved one is used next time


def test_two_workers_refreshing_at_once_refresh_once(engine: Engine) -> None:
    vault, calls = TokenVault(KEY), []
    tenant = connect(engine, vault, Tokens("A1", "R1", int(NOW) + 1800))

    def refresher(r: str) -> Tokens:
        calls.append(r)
        time.sleep(0.3)
        return Tokens("A2", "R2", int(NOW) + 1800)

    results: list[str] = []
    barrier = threading.Barrier(2)

    def go() -> None:
        p = DbTokenProvider(engine, tenant, "xero", "o1", vault, refresher, clock=lambda: NOW)
        barrier.wait()
        results.append(p.refresh("A1"))  # both were told A1 was refused

    threads = [threading.Thread(target=go) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert results == ["A2", "A2"] and calls == ["R1"]


def test_a_refused_refresh_keeps_the_old_tokens_and_leaks_nothing(engine: Engine) -> None:
    vault = TokenVault(KEY)
    tenant = connect(engine, vault, Tokens("A1", "R1", int(NOW) - 10))

    def refresher(r: str) -> Tokens:
        raise ProviderError(Failure.UNAUTHORISED, detail="token request refused (HTTP 400)")

    p = DbTokenProvider(engine, tenant, "xero", "o1", vault, refresher, clock=lambda: NOW)
    with pytest.raises(ProviderError) as err:
        p.access_token()
    assert "R1" not in str(err.value)
    with Session(engine) as s:
        row = s.query(AccountingConnection).filter_by(tenant_id=tenant).one()
        assert vault.open(row.tokens_encrypted).refresh_token == "R1"


def test_a_tenant_that_never_connected_is_unauthorised(engine: Engine) -> None:
    p = DbTokenProvider(
        engine, uuid.uuid4(), "xero", "o1", TokenVault(KEY), lambda r: Tokens("a", "r", 1)
    )
    with pytest.raises(ProviderError) as err:
        p.access_token()
    assert err.value.failure is Failure.UNAUTHORISED
