"""Handing approved invoices to an accounting system as draft bills (Project B).

Creates draft bills only. A human must review and approve bills in the accounting platform.

Every route needs a signed-in reviewer except the read-only status. No route returns a token, a
secret or a provider response body; a failure is reported as a code and one plain sentence.
"""

import time
import uuid
from typing import Annotated, Literal

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import delete
from sqlalchemy.orm import Session

from intake.api.deps import require_token, require_user, session_dep, settings_dep
from intake.audit.writer import record_event
from intake.config import Settings
from intake.connectors.accounting import ProviderError
from intake.connectors.tokens import Tokens, TokenVault
from intake.connectors.xero import (
    AUTHORIZE_URL,
    TIMEOUT,
    exchange_code,
    list_organisations,
)
from intake.core.accounting import Problem, explain, handoff_problem
from intake.core.oauth import (
    CallbackError,
    CallbackOk,
    authorize_url,
    check_callback,
    make_state,
    pkce_challenge,
    pkce_verifier,
)
from intake.core.statuses import ActorType, HandoffStatus, InvoiceStatus
from intake.db.models import AccountingConnection, Invoice
from intake.handoff import factory
from intake.handoff.service import (
    HandoffRefused,
    HandoffView,
    NotFound,
    get_handoff,
    reconcile,
    recover_stale,
    request_handoff,
    resolve_unknown,
    view,
)

router = APIRouter(prefix="/accounting", tags=["accounting"])
invoice_router = APIRouter(prefix="/invoices", tags=["accounting"])

DRAFT_ONLY = (
    "Creates draft bills only. A human must review and approve bills in the accounting platform."
)


class ConnectionOut(BaseModel):
    provider: Literal["mock", "xero"]
    connected: bool
    organisation_name: str | None
    tax_types_configured: bool
    note: str = DRAFT_ONLY


class HandoffOut(BaseModel):
    status: Literal["not_started", "pending", "in_progress", "succeeded", "failed", "unknown"]
    provider: str | None
    organisation_name: str | None
    external_bill_id: str | None
    failure_code: str | None
    manual_action: bool
    attempts: int
    started_at: str | None
    finished_at: str | None
    message: str
    can_request: bool
    can_reconcile: bool
    connected: bool
    note: str = DRAFT_ONLY


class ResolveIn(BaseModel):
    """The bill a person found in the accounting system, or none if they confirmed there is none."""

    bill_id: str | None = Field(default=None, max_length=100)


class CallbackIn(BaseModel):
    code: str | None = Field(default=None, max_length=2000)
    state: str | None = Field(default=None, max_length=2000)
    error: str | None = Field(default=None, max_length=200)


class AuthorizeOut(BaseModel):
    url: str


def _tenant(settings: Settings) -> uuid.UUID:
    return uuid.UUID(settings.default_tenant_id)


def _http(request: Request) -> httpx.Client:
    maker = getattr(request.app.state, "http_client", None)
    client: httpx.Client = maker() if maker else httpx.Client(timeout=TIMEOUT)
    return client


def _connected(request: Request, session: Session, settings: Settings) -> factory.Connected | None:
    return factory.connect(
        session,
        request.app.state.engine,
        settings,
        request.app.state.mock_accounting,
        _tenant(settings),
        http=_http(request) if settings.accounting_provider == "xero" else None,
    )


def _out(
    h: HandoffView | None, invoice_status: InvoiceStatus, connected: factory.Connected | None
) -> HandoffOut:
    status = h.status if h is not None else None
    eligible = handoff_problem(invoice_status, status) is None
    code = h.failure_code if h is not None else None
    return HandoffOut(
        status=status.value if status is not None else "not_started",
        provider=h.provider
        if h is not None
        else (connected.connector.provider if connected else None),
        organisation_name=connected.organisation_name if connected else None,
        external_bill_id=h.external_bill_id if h is not None else None,
        failure_code=code,
        manual_action=h.manual_action if h is not None else False,
        attempts=h.attempts if h is not None else 0,
        started_at=h.started_at.isoformat() if h is not None and h.started_at else None,
        finished_at=h.finished_at.isoformat() if h is not None and h.finished_at else None,
        message=explain(status, code)
        if status is not None
        else (
            "Ready to send as a draft bill."
            if eligible
            else explain(HandoffStatus.FAILED, Problem.NOT_APPROVED.value)
        ),
        can_request=eligible and connected is not None,
        can_reconcile=status is HandoffStatus.UNKNOWN and connected is not None,
        connected=connected is not None,
    )


def _invoice(session: Session, tenant_id: uuid.UUID, invoice_id: uuid.UUID) -> Invoice:
    inv = session.get(Invoice, invoice_id)
    if inv is None or inv.tenant_id != tenant_id:
        raise HTTPException(404, "not found")
    return inv


def _current(
    request: Request, session: Session, settings: Settings, invoice_id: uuid.UUID
) -> tuple[Invoice, factory.Connected | None, HandoffView | None]:
    tenant = _tenant(settings)
    recover_stale(session, tenant_id=tenant)
    inv = _invoice(session, tenant, invoice_id)
    connected = _connected(request, session, settings)
    h = (
        get_handoff(
            session, tenant, invoice_id, connected.connector.provider, connected.organisation_id
        )
        if connected
        else None
    )
    return inv, connected, view(h) if h is not None else None


def _not_connected() -> HTTPException:
    return HTTPException(
        409, {"code": "NOT_CONNECTED", "message": "No accounting system is connected yet."}
    )


# ---- connection --------------------------------------------------------------------------------


@router.get("/connection", response_model=ConnectionOut, dependencies=[Depends(require_token)])
def connection(
    request: Request,
    session: Annotated[Session, Depends(session_dep)],
    settings: Annotated[Settings, Depends(settings_dep)],
) -> ConnectionOut:
    connected = _connected(request, session, settings)
    return ConnectionOut(
        provider=settings.accounting_provider,
        connected=connected is not None,
        organisation_name=connected.organisation_name if connected else None,
        tax_types_configured=bool(factory.tax_types(settings)),
    )


def _xero_ready(settings: Settings) -> None:
    if settings.accounting_provider != "xero":
        raise HTTPException(
            409, {"code": "NOT_XERO", "message": "Xero is not the selected provider."}
        )
    if not (
        settings.xero_client_id
        and settings.xero_redirect_uri
        and settings.accounting_encryption_key
        and settings.session_secret
    ):
        raise HTTPException(
            409,
            {"code": "XERO_NOT_CONFIGURED", "message": "Xero connection settings are incomplete."},
        )


@router.post("/xero/connect", response_model=AuthorizeOut)
def xero_connect(
    settings: Annotated[Settings, Depends(settings_dep)],
    user: Annotated[str, Depends(require_user)],
) -> AuthorizeOut:
    """Where to send the reviewer to sign in to Xero (authorization code flow with PKCE)."""
    _xero_ready(settings)
    state = make_state(settings.session_secret, user, int(time.time()))
    url = authorize_url(
        AUTHORIZE_URL,
        settings.xero_client_id,
        settings.xero_redirect_uri,
        settings.xero_scopes.split(),
        state,
        pkce_challenge(pkce_verifier(settings.session_secret, state)),
    )
    return AuthorizeOut(url=url)


_CALLBACK_TEXT = {
    CallbackError.DENIED: "Xero sign-in was cancelled or refused.",
    CallbackError.MISSING: "The sign-in answer was incomplete.",
    CallbackError.BAD_STATE: "The sign-in answer did not come from a sign-in started here.",
    CallbackError.EXPIRED: "The sign-in took too long. Start again.",
    CallbackError.WRONG_USER: "The sign-in was started by someone else.",
}


@router.post("/xero/callback", response_model=ConnectionOut)
def xero_callback(
    body: CallbackIn,
    request: Request,
    session: Annotated[Session, Depends(session_dep)],
    settings: Annotated[Settings, Depends(settings_dep)],
    user: Annotated[str, Depends(require_user)],
) -> ConnectionOut:
    _xero_ready(settings)
    params = {k: v for k, v in body.model_dump().items() if v}
    checked = check_callback(params, settings.session_secret, int(time.time()), user)
    if not isinstance(checked, CallbackOk):
        raise HTTPException(400, {"code": checked.name, "message": _CALLBACK_TEXT[checked]})
    client = _http(request)
    try:
        tokens = exchange_code(
            client,
            client_id=settings.xero_client_id,
            client_secret=settings.xero_client_secret,
            redirect_uri=settings.xero_redirect_uri,
            code=checked.code,
            verifier=checked.verifier,
        )
        orgs = list_organisations(client, tokens.access_token)
    except ProviderError as exc:
        raise HTTPException(
            502, {"code": exc.failure.value, "message": "Xero did not complete the connection."}
        ) from None
    chosen = [
        o
        for o in orgs
        if not settings.xero_organisation_id or o[0] == settings.xero_organisation_id
    ]
    if len(chosen) != 1:
        raise HTTPException(
            409,
            {
                "code": "CHOOSE_ORGANISATION",
                "message": "More than one organisation was authorised (or the configured one was "
                "not). "
                "Set XERO_ORGANISATION_ID to the one to use, then connect again.",
            },
        )
    org_id, org_name = chosen[0]
    tenant = _tenant(settings)
    # One connection per tenant: its refresh token rotates, so it must not be stored twice.
    session.execute(
        delete(AccountingConnection).where(
            AccountingConnection.tenant_id == tenant, AccountingConnection.provider == "xero"
        )
    )
    session.add(
        AccountingConnection(
            tenant_id=tenant,
            provider="xero",
            organisation_id=org_id,
            organisation_name=org_name,
            tokens_encrypted=TokenVault(settings.accounting_encryption_key).seal(
                Tokens(tokens.access_token, tokens.refresh_token, tokens.expires_at)
            ),
            scopes=settings.xero_scopes,
        )
    )
    record_event(
        session,
        tenant_id=tenant,
        event_type="accounting_connected",
        actor_type=ActorType.USER,
        actor_id=user,
        data={"provider": "xero", "organisation": org_id},
    )
    session.commit()
    return ConnectionOut(
        provider="xero",
        connected=True,
        organisation_name=org_name,
        tax_types_configured=bool(factory.tax_types(settings)),
    )


# ---- one invoice's handoff ---------------------------------------------------------------------


@invoice_router.get(
    "/{invoice_id}/handoff", response_model=HandoffOut, dependencies=[Depends(require_token)]
)
def handoff_status(
    invoice_id: uuid.UUID,
    request: Request,
    session: Annotated[Session, Depends(session_dep)],
    settings: Annotated[Settings, Depends(settings_dep)],
) -> HandoffOut:
    inv, connected, h = _current(request, session, settings, invoice_id)
    return _out(h, InvoiceStatus(inv.status), connected)


def _refusal(problem: Problem) -> HTTPException:
    return HTTPException(
        409,
        {"code": problem.value, "message": explain(HandoffStatus.FAILED, problem.value)},
    )


@invoice_router.post("/{invoice_id}/handoff", response_model=HandoffOut)
def handoff_request(
    invoice_id: uuid.UUID,
    request: Request,
    session: Annotated[Session, Depends(session_dep)],
    settings: Annotated[Settings, Depends(settings_dep)],
    actor: Annotated[str, Depends(require_user)],
) -> HandoffOut:
    """Send the approved invoice to the accounting system as a draft bill. Safe to repeat: it can
    never create a second bill."""
    connected = _connected(request, session, settings)
    if connected is None:
        raise _not_connected()
    try:
        done = request_handoff(
            session,
            connector=connected.connector,
            tax_types=factory.tax_types(settings),
            tenant_id=_tenant(settings),
            invoice_id=invoice_id,
            actor=actor,
        )
    except NotFound:
        raise HTTPException(404, "not found") from None
    except HandoffRefused as refused:
        raise _refusal(refused.problem) from None
    inv = _invoice(session, _tenant(settings), invoice_id)
    return _out(done, InvoiceStatus(inv.status), connected)


@invoice_router.post("/{invoice_id}/handoff/reconcile", response_model=HandoffOut)
def handoff_reconcile(
    invoice_id: uuid.UUID,
    request: Request,
    session: Annotated[Session, Depends(session_dep)],
    settings: Annotated[Settings, Depends(settings_dep)],
    actor: Annotated[str, Depends(require_user)],
) -> HandoffOut:
    """Look in the accounting system for a bill we may have created. Never writes a bill."""
    connected = _connected(request, session, settings)
    if connected is None:
        raise _not_connected()
    try:
        done = reconcile(
            session,
            connector=connected.connector,
            tax_types=factory.tax_types(settings),
            tenant_id=_tenant(settings),
            invoice_id=invoice_id,
            actor=actor,
        )
    except NotFound:
        raise HTTPException(404, "no unresolved handoff for this invoice") from None
    except ProviderError as exc:
        session.rollback()
        raise HTTPException(
            502,
            {
                "code": exc.failure.value,
                "message": explain(HandoffStatus.FAILED, exc.failure.value),
            },
        ) from None
    inv = _invoice(session, _tenant(settings), invoice_id)
    return _out(done, InvoiceStatus(inv.status), connected)


@invoice_router.post("/{invoice_id}/handoff/resolve", response_model=HandoffOut)
def handoff_resolve(
    invoice_id: uuid.UUID,
    body: ResolveIn,
    request: Request,
    session: Annotated[Session, Depends(session_dep)],
    settings: Annotated[Settings, Depends(settings_dep)],
    actor: Annotated[str, Depends(require_user)],
) -> HandoffOut:
    """A person checked the accounting system: give the bill they found, or none if there is none
    (which allows the handoff to be requested again)."""
    connected = _connected(request, session, settings)
    if connected is None:
        raise _not_connected()
    try:
        done = resolve_unknown(
            session,
            provider=connected.connector.provider,
            organisation_id=connected.organisation_id,
            tenant_id=_tenant(settings),
            invoice_id=invoice_id,
            actor=actor,
            bill_id=body.bill_id,
        )
    except NotFound:
        raise HTTPException(404, "no unresolved handoff for this invoice") from None
    inv = _invoice(session, _tenant(settings), invoice_id)
    return _out(done, InvoiceStatus(inv.status), connected)
