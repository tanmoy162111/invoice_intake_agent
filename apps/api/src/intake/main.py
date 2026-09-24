from fastapi import Depends, FastAPI
from fastapi.middleware.cors import CORSMiddleware

from intake.api import documents, jobs
from intake.api.deps import require_token
from intake.api.guard import UploadGuard
from intake.api.health import router as health_router
from intake.config import Settings, get_settings
from intake.db.session import make_engine
from intake.ingest.service import UploadIngestor
from intake.ingest.storage import LocalStorage


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or get_settings()
    # Interactive docs and the schema are not public: /openapi.json below needs the token.
    app = FastAPI(
        title="Invoice Intake Agent API",
        version="0.1.0",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    storage = LocalStorage(settings.storage_dir)
    app.state.settings = settings
    app.state.engine = make_engine(settings.database_url)
    app.state.storage = storage
    app.state.ingestor = UploadIngestor(settings, storage)
    app.add_middleware(UploadGuard, settings=settings)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
        allow_methods=["GET", "POST"],
        allow_headers=["Authorization", "Content-Type"],
    )
    app.include_router(health_router)  # the only public route
    protected = [Depends(require_token)]
    app.include_router(documents.router, dependencies=protected)
    app.include_router(jobs.router, dependencies=protected)

    @app.get("/openapi.json", include_in_schema=False, dependencies=protected)
    def openapi_schema() -> dict[str, object]:
        return app.openapi()

    return app


app = create_app()
