from pathlib import Path

import pytest
from pydantic import ValidationError

from intake.config import Settings
from intake.core.llm_budget import UnknownModelError
from intake.extract.factory import build_client
from intake.extract.llm import AnthropicClient
from intake.extract.ollama import OllamaClient
from intake.extract.recorded import RecordedClient


def settings(**kw: object) -> Settings:
    base: dict[str, object] = {"anthropic_api_key": "", "extraction_model": "claude-sonnet-5"}
    return Settings(**{**base, **kw})  # type: ignore[arg-type]


def test_anthropic_without_a_key_is_not_configured() -> None:
    assert build_client(settings()) is None


def test_anthropic_with_a_key_builds_the_client() -> None:
    client = build_client(settings(anthropic_api_key="sk-test"))
    assert isinstance(client, AnthropicClient) and client.model == "claude-sonnet-5"


def test_no_model_is_not_configured() -> None:
    assert build_client(settings(anthropic_api_key="sk-test", extraction_model="")) is None


def test_an_unpriced_model_is_refused_not_guessed() -> None:
    with pytest.raises(UnknownModelError):
        build_client(settings(anthropic_api_key="sk-test", extraction_model="mystery-1"))


def test_ollama_needs_no_key() -> None:
    client = build_client(
        settings(llm_provider="ollama", extraction_model="qwen-vl", ollama_num_ctx=4096)
    )
    assert isinstance(client, OllamaClient) and client.model == "qwen-vl"


def test_ollama_without_a_model_is_not_configured() -> None:
    assert build_client(settings(llm_provider="ollama", extraction_model="")) is None


def test_the_provider_must_be_a_known_one() -> None:
    with pytest.raises(ValidationError):
        settings(llm_provider="openai")


def test_the_default_provider_is_anthropic() -> None:
    assert Settings().llm_provider == "anthropic"


def test_recorded_replays_from_a_directory_and_needs_no_key(tmp_path: Path) -> None:
    client = build_client(settings(llm_provider="recorded", recorded_dir=str(tmp_path)))
    assert isinstance(client, RecordedClient) and client.model == "claude-sonnet-5"


def test_recorded_without_a_directory_is_not_configured() -> None:
    assert build_client(settings(llm_provider="recorded", recorded_dir="")) is None


def test_recorded_is_refused_in_production() -> None:
    with pytest.raises(ValidationError, match="recorded"):
        settings(llm_provider="recorded", recorded_dir="/x", app_env="production")
