import time
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from intake.api.auth import Caller, credentials_match
from intake.api.deps import require_token, session_dep, settings_dep
from intake.audit.writer import record_event
from intake.config import Settings
from intake.core.session import LoginThrottle, sign_session
from intake.core.statuses import ActorType

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginIn(BaseModel):
    username: str = Field(max_length=200)
    password: str = Field(max_length=1000)


class LoginOut(BaseModel):
    token: str
    user: str
    expires_in: int  # seconds


class MeOut(BaseModel):
    user: str | None  # None for a caller using the static API token


def _client(request: Request) -> str:
    """Who is knocking: the connection's address. A forwarded-for header is not trusted, since
    anyone can send one; behind a proxy, configure the proxy to set the real client address."""
    return request.client.host if request.client else "unknown"


@router.post("/login", response_model=LoginOut)
def login(
    body: LoginIn,
    request: Request,
    session: Annotated[Session, Depends(session_dep)],
    settings: Annotated[Settings, Depends(settings_dep)],
) -> LoginOut:
    """The demo reviewer signs in. This is the one route besides /health that needs no token."""
    if not settings.reviewer_password or not settings.session_secret:
        raise HTTPException(503, "login is not configured on the server")
    throttle: LoginThrottle = request.app.state.throttle
    now = int(time.time())
    client = _client(request)
    tenant_id = uuid.UUID(settings.default_tenant_id)
    if not throttle.attempt(client, now):  # a slot is reserved before the password is checked
        wait = max(throttle.retry_after(client, now), 1)
        raise HTTPException(
            429,
            "too many attempts; wait a minute and try again",
            headers={"Retry-After": str(wait)},
        )
    if not credentials_match(settings, body.username, body.password):
        # the name typed is not recorded: it is attacker-controlled text
        record_event(
            session, tenant_id=tenant_id, event_type="login_failed", actor_type=ActorType.USER
        )
        session.commit()
        raise HTTPException(401, "wrong username or password")
    throttle.succeeded(client)
    record_event(
        session, tenant_id=tenant_id, event_type="login_succeeded", actor_type=ActorType.USER,
        actor_id=settings.reviewer_username,
    )  # fmt: skip
    session.commit()
    token = sign_session(
        settings.session_secret, settings.reviewer_username, now, settings.session_ttl_s
    )
    return LoginOut(token=token, user=settings.reviewer_username, expires_in=settings.session_ttl_s)


@router.get("/me", response_model=MeOut, dependencies=[Depends(require_token)])
def me(caller: Annotated[Caller, Depends(require_token)]) -> MeOut:
    return MeOut(user=caller.user)
