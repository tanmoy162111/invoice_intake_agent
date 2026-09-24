"""Build the configured model client. Returns None when extraction isn't configured yet (no key or
model), so the worker pauses extraction jobs visibly instead of failing them."""

from functools import lru_cache

from intake.config import Settings
from intake.extract.llm import AnthropicClient, LlmClient


@lru_cache(maxsize=4)
def _anthropic(model: str, api_key: str, timeout_s: float) -> AnthropicClient:
    return AnthropicClient(model=model, api_key=api_key, timeout_s=timeout_s)


def build_client(settings: Settings) -> LlmClient | None:
    if not settings.anthropic_api_key or not settings.extraction_model:
        return None
    return _anthropic(
        settings.extraction_model, settings.anthropic_api_key, settings.extract_timeout_s
    )
