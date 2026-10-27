"""Which accounting system a tenant is connected to, as a ready connector. Nothing here writes."""

import json
import uuid
from dataclasses import dataclass

import httpx
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from intake.config import Settings
from intake.connectors.accounting import AccountingConnector
from intake.connectors.mock import MockAccounting
from intake.connectors.tokens import DbTokenProvider, TokenVault
from intake.connectors.xero import TIMEOUT, XeroConnector, make_refresher
from intake.db.models import AccountingConnection

# Used only by the in-memory "mock" provider, so the offline demo works without configuration.
MOCK_TAX_TYPES = {1000: "MOCK_GST", 0: "MOCK_GST_FREE"}


def parse_tax_types(raw: str) -> dict[int, str]:
    """`{"1000": "INPUT"}` -> `{1000: "INPUT"}`. Anything unusable is an empty mapping, which stops
    every handoff at "tax type missing" rather than guessing one."""
    try:
        data = json.loads(raw) if raw.strip() else {}
        return {int(k): str(v) for k, v in data.items() if str(v).strip()}
    except (ValueError, TypeError, AttributeError):
        return {}


def tax_types(settings: Settings) -> dict[int, str]:
    if settings.accounting_provider == "mock" and not settings.accounting_tax_types.strip():
        return dict(MOCK_TAX_TYPES)
    return parse_tax_types(settings.accounting_tax_types)


@dataclass(frozen=True)
class Connected:
    connector: AccountingConnector
    organisation_id: str
    organisation_name: str | None


def xero_connection(
    session: Session, settings: Settings, tenant_id: uuid.UUID
) -> AccountingConnection | None:
    stmt = select(AccountingConnection).where(
        AccountingConnection.tenant_id == tenant_id, AccountingConnection.provider == "xero"
    )
    if settings.xero_organisation_id:
        stmt = stmt.where(AccountingConnection.organisation_id == settings.xero_organisation_id)
    return session.scalars(stmt.order_by(AccountingConnection.created_at)).first()


def connect(
    session: Session,
    engine: Engine,
    settings: Settings,
    mock: MockAccounting,
    tenant_id: uuid.UUID,
    http: httpx.Client | None = None,
) -> Connected | None:
    """The tenant's connector, or None when no accounting system is connected."""
    if settings.accounting_provider == "mock":
        if settings.app_env == "production":
            return None  # a stand-in would report bills that do not exist
        return Connected(mock, mock.organisation_id, "Offline demo (nothing is sent anywhere)")
    row = xero_connection(session, settings, tenant_id)
    if row is None or not settings.accounting_encryption_key or not settings.xero_client_id:
        return None
    client = http or httpx.Client(timeout=TIMEOUT)
    tokens = DbTokenProvider(
        engine,
        tenant_id,
        "xero",
        row.organisation_id,
        TokenVault(settings.accounting_encryption_key),
        make_refresher(client, settings.xero_client_id, settings.xero_client_secret),
    )
    return Connected(
        XeroConnector(row.organisation_id, tokens, client),
        row.organisation_id,
        row.organisation_name,
    )
