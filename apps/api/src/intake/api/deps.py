from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Header, HTTPException, Request
from sqlalchemy.orm import Session

from intake.api.auth import token_problem
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
) -> None:
    """Interim auth (static bearer token) until real login lands in M8. Fails closed."""
    problem = token_problem(settings, authorization)
    if problem:
        status, message = problem
        headers = {"WWW-Authenticate": "Bearer"} if status == 401 else None
        raise HTTPException(status, message, headers=headers)
