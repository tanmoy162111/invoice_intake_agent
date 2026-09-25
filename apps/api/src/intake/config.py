from datetime import date
from decimal import Decimal
from functools import lru_cache
from typing import Literal

from pydantic import Field, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings, read from the environment (see .env.example)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    # "production" turns on guards: for example the as-of date override below is refused.
    app_env: Literal["development", "test", "production"] = "development"

    database_url: str = "postgresql+psycopg://intake:intake@localhost:5432/intake"
    cors_origins: str = "http://localhost:3000"
    storage_dir: str = "./storage"
    anthropic_api_key: str = ""
    extraction_model: str = "claude-sonnet-5"
    bank_encryption_key: str = ""

    # Auth (interim static token until real login in M8). Empty means all protected routes refuse.
    api_token: str = ""
    default_tenant_id: str = "00000000-0000-4000-8000-00000000d3a0"

    # Ingestion limits (playbook §6.1)
    max_upload_bytes: int = 15 * 1024 * 1024
    max_pages: int = 10
    render_dpi: int = 200
    max_image_pixels: int = 25_000_000

    # Which model backend reads invoices. "ollama" is an optional local, demo-only backend.
    llm_provider: Literal["anthropic", "ollama"] = "anthropic"
    ollama_base_url: str = "http://localhost:11434"
    ollama_num_ctx: int = 8192

    # Extraction (playbook §6.2). Money is Decimal dollars here, integer micros everywhere else.
    extraction_prompt_version: str = "v1"
    extract_max_output_tokens: int = 8000
    extract_max_input_tokens: int = 100_000
    extract_timeout_s: float = 120.0
    daily_spend_cap_usd: Decimal = Decimal("5")  # 0 pauses all extraction
    extract_not_configured_retry_s: int = 300
    field_confidence_min: Decimal = Decimal("0.8")  # playbook §6.3

    # Validation (M4). The rule settings (tolerances, age limit) live in each tenant's settings.
    # A fixed "as of" date for the date checks; empty means today. Keeps tests and demos stable.
    validation_today: date | None = None

    # Duplicate check (M5): wait for earlier invoices that are not read yet, then give up waiting.
    dedupe_poll_s: int = Field(default=10, ge=1)
    dedupe_max_wait_s: int = Field(default=300, ge=1)

    # Job queue
    job_max_attempts: int = 3
    job_backoff_base_s: int = 10
    job_backoff_cap_s: int = 600
    job_visibility_timeout_s: int = 300
    worker_poll_interval_s: float = 2.0

    @model_validator(mode="after")
    def _no_date_override_in_production(self) -> "Settings":
        if self.app_env == "production" and self.validation_today is not None:
            raise ValueError("VALIDATION_TODAY must not be set when APP_ENV=production")
        return self

    @property
    def daily_spend_cap_micros(self) -> int:
        return int(self.daily_spend_cap_usd * 1_000_000)


@lru_cache
def get_settings() -> Settings:
    return Settings()
