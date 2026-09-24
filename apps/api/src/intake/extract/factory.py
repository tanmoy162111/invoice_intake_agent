"""Build the configured model client. Returns None when extraction isn't configured yet (no key or
model), so the worker pauses extraction jobs visibly instead of failing them."""

from functools import lru_cache

from intake.config import Settings
from intake.extract.llm import AnthropicClient, LlmClient
from intake.extract.ollama import OllamaClient


@lru_cache(maxsize=4)
def _anthropic(model: str, api_key: str, timeout_s: float) -> AnthropicClient:
    return AnthropicClient(model=model, api_key=api_key, timeout_s=timeout_s)


@lru_cache(maxsize=4)
def _ollama(model: str, base_url: str, num_ctx: int, timeout_s: float) -> OllamaClient:
    return OllamaClient(model=model, base_url=base_url, num_ctx=num_ctx, timeout_s=timeout_s)


def build_client(settings: Settings) -> LlmClient | None:
    if not settings.extraction_model:
        return None
    if settings.llm_provider == "ollama":  # local, demo only: no API key involved
        return _ollama(
            settings.extraction_model, settings.ollama_base_url, settings.ollama_num_ctx,
            max(settings.extract_timeout_s, 300.0),
        )  # fmt: skip
    if not settings.anthropic_api_key:
        return None
    return _anthropic(
        settings.extraction_model, settings.anthropic_api_key, settings.extract_timeout_s
    )
