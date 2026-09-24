from decimal import Decimal
from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings, read from the environment (see .env.example)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

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

    # Extraction (playbook §6.2). Money is Decimal dollars here, integer micros everywhere else.
    extraction_prompt_version: str = "v1"
    extract_max_output_tokens: int = 8000
    extract_max_input_tokens: int = 100_000
    extract_timeout_s: float = 120.0
    daily_spend_cap_usd: Decimal = Decimal("5")  # 0 pauses all extraction
    extract_not_configured_retry_s: int = 300

    # Job queue
    job_max_attempts: int = 3
    job_backoff_base_s: int = 10
    job_backoff_cap_s: int = 600
    job_visibility_timeout_s: int = 300
    worker_poll_interval_s: float = 2.0

    @property
    def daily_spend_cap_micros(self) -> int:
        return int(self.daily_spend_cap_usd * 1_000_000)


@lru_cache
def get_settings() -> Settings:
    return Settings()
