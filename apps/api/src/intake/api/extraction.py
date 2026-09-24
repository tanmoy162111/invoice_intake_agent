import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from intake.api.deps import session_dep, settings_dep
from intake.config import Settings
from intake.core.llm_budget import cap_reached, next_utc_midnight
from intake.extract.service import spend_today_micros
from intake.worker import queue

router = APIRouter(prefix="/extraction", tags=["extraction"])


class ExtractionStatus(BaseModel):
    configured: bool  # a model, an API key and a bank-detail key are all set
    model: str
    prompt_version: str
    spent_today_usd_micros: int
    daily_cap_usd_micros: int
    cap_reached: bool  # extraction is paused until `resumes_at`
    paused_jobs: int
    resumes_at: datetime | None


@router.get("/status", response_model=ExtractionStatus)
def extraction_status(
    session: Annotated[Session, Depends(session_dep)],
    settings: Annotated[Settings, Depends(settings_dep)],
) -> ExtractionStatus:
    """Model spend against today's cap, and whether extraction is paused."""
    tenant_id = uuid.UUID(settings.default_tenant_id)
    spent, now = spend_today_micros(session, tenant_id)
    paused = cap_reached(spent, settings.daily_spend_cap_micros)
    st = queue.stats(session, timeout_s=settings.job_visibility_timeout_s, tenant_id=tenant_id)
    return ExtractionStatus(
        configured=bool(
            settings.anthropic_api_key
            and settings.extraction_model
            and settings.bank_encryption_key
        ),
        model=settings.extraction_model,
        prompt_version=settings.extraction_prompt_version,
        spent_today_usd_micros=spent,
        daily_cap_usd_micros=settings.daily_spend_cap_micros,
        cap_reached=paused,
        paused_jobs=st.paused,
        resumes_at=next_utc_midnight(now) if paused else None,
    )
