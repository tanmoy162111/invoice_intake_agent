from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from intake.api.health import router as health_router
from intake.config import get_settings


def create_app() -> FastAPI:
    settings = get_settings()
    app = FastAPI(title="Invoice Intake Agent API", version="0.1.0")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[o.strip() for o in settings.cors_origins.split(",") if o.strip()],
        allow_methods=["GET"],
        allow_headers=["*"],
    )
    app.include_router(health_router)
    return app


app = create_app()
