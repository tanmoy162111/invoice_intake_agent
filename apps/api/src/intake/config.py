from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Runtime settings, read from the environment (see .env.example)."""

    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://intake:intake@localhost:5432/intake"
    cors_origins: str = "http://localhost:3000"
    storage_dir: str = "./storage"
    anthropic_api_key: str = ""
    extraction_model: str = ""


@lru_cache
def get_settings() -> Settings:
    return Settings()
