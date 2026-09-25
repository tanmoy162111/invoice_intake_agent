import time
from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from intake.api.auth import Caller, resolve_caller
from intake.config import Settings
from intake.ingest.service import UploadIngestor
from intake.ingest.storage import LocalStorage


def settings_dep(request: Request) -> Settings:
    settings: Settings = request.app.state.settings
    return settings


def session_dep(request: Request) -> Iterator[Session]:
    with Session(request.app.state.engine) as session:
        yield session


def ingestor_dep(request: Request) -> UploadIngestor:
    ingestor: UploadIngestor = request.app.state.ingestor
    return ingestor


def storage_dep(request: Request) -> LocalStorage:
    storage: LocalStorage = request.app.state.storage
    return storage


def require_token(
    settings: Annotated[Settings, Depends(settings_dep)],
    authorization: Annotated[str | None, Header()] = None,
) -> Caller:
    """Every route but /health and the login: the static API token (scripts) or a reviewer's
    signed session. Fails closed."""
    caller = resolve_caller(settings, authorization, int(time.time()))
    if isinstance(caller, Caller):
        return caller
    status, message = caller
    headers = {"WWW-Authenticate": "Bearer"} if status == 401 else None
    raise HTTPException(status, message, headers=headers)


def require_user(caller: Annotated[Caller, Depends(require_token)]) -> str:
    """Review actions need a signed-in reviewer so the history names who acted."""
    if caller.kind != "user" or not caller.user:
        raise HTTPException(403, "sign in as a reviewer to do this")
    return caller.user
