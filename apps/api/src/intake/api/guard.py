"""Refuse bad upload requests before the framework parses (and spools) the multipart body.

FastAPI reads the body before it runs route dependencies, so without this an unauthenticated
client could make the server buffer a huge upload only to get a 401.
"""

import time

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from intake.api.auth import Caller, resolve_caller
from intake.config import Settings

MULTIPART_OVERHEAD = 64 * 1024


class UploadGuard:
    def __init__(self, app: ASGIApp, settings: Settings) -> None:
        self.app = app
        self.settings = settings

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"] == "POST" and scope["path"] == "/documents":
            headers = {k.decode().lower(): v.decode("latin-1") for k, v in scope["headers"]}
            caller = resolve_caller(self.settings, headers.get("authorization"), int(time.time()))
            if not isinstance(caller, Caller):
                status, message = caller
                extra = {"WWW-Authenticate": "Bearer"} if status == 401 else None
                await JSONResponse({"detail": message}, status, extra)(scope, receive, send)
                return
            length = headers.get("content-length")
            if length is None or not length.isdigit():
                # A length-less (chunked) body cannot be bounded here.
                await JSONResponse({"detail": "Content-Length is required"}, 411)(
                    scope, receive, send
                )
                return
            limit = self.settings.max_upload_bytes + MULTIPART_OVERHEAD
            if int(length) > limit:
                detail = {
                    "code": "FILE_TOO_LARGE",
                    "message": "The request is larger than the upload limit.",
                    "fix": "Compress or split the file, or scan at a lower resolution",
                }
                await JSONResponse({"detail": detail}, 413)(scope, receive, send)
                return
        await self.app(scope, receive, send)
