import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field

from intake.api.auth import Caller, credentials_match
from intake.api.deps import require_token, settings_dep
from intake.config import Settings
from intake.core.session import LoginThrottle, sign_session

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


@router.post("/login", response_model=LoginOut)
def login(
    body: LoginIn, request: Request, settings: Annotated[Settings, Depends(settings_dep)]
) -> LoginOut:
    """The demo reviewer signs in. This is the one route besides /health that needs no token."""
    if not settings.reviewer_password or not settings.session_secret:
        raise HTTPException(503, "login is not configured on the server")
    throttle: LoginThrottle = request.app.state.throttle
    now = int(time.time())
    if not throttle.allowed(now):
        raise HTTPException(429, "too many attempts; wait a minute and try again")
    if not credentials_match(settings, body.username, body.password):
        throttle.record_failure(now)
        raise HTTPException(401, "wrong username or password")
    throttle.record_success()
    token = sign_session(settings.session_secret, body.username, now, settings.session_ttl_s)
    return LoginOut(token=token, user=body.username, expires_in=settings.session_ttl_s)


@router.get("/me", response_model=MeOut, dependencies=[Depends(require_token)])
def me(caller: Annotated[Caller, Depends(require_token)]) -> MeOut:
    return MeOut(user=caller.user)
