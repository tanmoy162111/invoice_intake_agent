import base64
from types import SimpleNamespace
from typing import Any

import anthropic
import httpx2
import pytest

from intake.core.llm_budget import UnknownModelError
from intake.extract.llm import (
    TOOL_NAME,
    AnthropicClient,
    LlmOutputError,
    LlmRequest,
    PageInput,
    PermanentLlmError,
    TransientLlmError,
    build_content,
)


def request(**kw: Any) -> LlmRequest:
    base: dict[str, Any] = {
        "system": "sys",
        "pages": [PageInput(b"\x89PNGa", "image/png"), PageInput(b"\x89PNGb", "image/png")],
        "text_layers": ["hello", ""],
        "schema": {"type": "object"},
        "max_output_tokens": 4000,
    }
    base.update(kw)
    return LlmRequest(**base)


class FakeSdk:
    def __init__(self, response: Any = None, raises: Exception | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self.messages = SimpleNamespace(create=self._create)
        self._response, self._raises = response, raises

    def _create(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self._raises:
            raise self._raises
        return self._response


def tool_response(input_: dict[str, Any] | None = None, stop: str = "tool_use") -> Any:
    return SimpleNamespace(
        content=[SimpleNamespace(type="tool_use", name=TOOL_NAME, input=input_ or {"a": 1})],
        usage=SimpleNamespace(input_tokens=1200, output_tokens=300),
        stop_reason=stop,
        _request_id="req_123",
    )


def client(sdk: FakeSdk, model: str = "claude-sonnet-5") -> AnthropicClient:
    return AnthropicClient(model=model, api_key="k", sdk=sdk)


def status_error(cls: type[anthropic.APIStatusError], code: int) -> anthropic.APIStatusError:
    req = httpx2.Request("POST", "https://api.anthropic.com/v1/messages")
    return cls("boom", response=httpx2.Response(code, request=req), body=None)


def test_content_puts_labelled_images_before_text() -> None:
    content = build_content(request())
    kinds = [b["type"] for b in content]
    assert kinds == ["text", "image", "text", "image", "text", "text"]
    assert content[0]["text"] == "Page 1:"
    assert content[1]["source"] == {
        "type": "base64",
        "media_type": "image/png",
        "data": base64.b64encode(b"\x89PNGa").decode(),
    }
    assert "Text layer, page 1:\nhello" in content[-2]["text"]
    assert "page 2" not in content[-2]["text"]  # empty text layers are omitted
    assert TOOL_NAME in content[-1]["text"]


def test_no_text_layer_block_when_all_empty() -> None:
    content = build_content(request(text_layers=["", ""]))
    assert [b["type"] for b in content] == ["text", "image", "text", "image", "text"]


def test_validation_error_is_sent_on_retry() -> None:
    content = build_content(request(validation_error="total: field required"))
    assert "total: field required" in content[-1]["text"]


def test_request_uses_strict_tool_and_forced_choice() -> None:
    sdk = FakeSdk(tool_response())
    client(sdk).extract(request())
    call = sdk.calls[0]
    assert call["model"] == "claude-sonnet-5"
    assert call["max_tokens"] == 4000
    assert call["system"] == "sys"
    assert call["tools"][0]["strict"] is True
    assert call["tools"][0]["name"] == TOOL_NAME
    assert call["tools"][0]["input_schema"] == {"type": "object"}
    assert call["tool_choice"] == {"type": "tool", "name": TOOL_NAME}
    assert "temperature" not in call


@pytest.mark.parametrize("model", ["claude-opus-5-5", "claude-fable-5-1"])
def test_models_without_forced_tool_use_get_auto(model: str) -> None:
    sdk = FakeSdk(tool_response())
    client(sdk, model).extract(request())
    assert sdk.calls[0]["tool_choice"] == {"type": "auto"}
    assert sdk.calls[0]["tools"][0]["strict"] is True


def test_result_carries_payload_usage_and_request_id() -> None:
    res = client(FakeSdk(tool_response({"x": 2}))).extract(request())
    assert res.payload == {"x": 2}
    assert (res.input_tokens, res.output_tokens) == (1200, 300)
    assert res.request_id == "req_123"
    assert res.latency_ms >= 0


def test_cost_uses_the_price_table() -> None:
    c = client(FakeSdk())
    assert c.cost_micros(1_000_000, 0) == 2_000_000


def test_unknown_model_refused_at_construction() -> None:
    with pytest.raises(UnknownModelError):
        AnthropicClient(model="mystery-model", api_key="k", sdk=FakeSdk())


def test_truncated_answer_is_an_output_error_that_keeps_its_usage() -> None:
    with pytest.raises(LlmOutputError) as info:
        client(FakeSdk(tool_response(stop="max_tokens"))).extract(request())
    assert (info.value.input_tokens, info.value.output_tokens) == (1200, 300)


def test_answer_without_tool_call_is_an_output_error() -> None:
    resp = SimpleNamespace(
        content=[SimpleNamespace(type="text", text="I refuse")],
        usage=SimpleNamespace(input_tokens=1, output_tokens=1),
        stop_reason="end_turn",
    )
    with pytest.raises(LlmOutputError):
        client(FakeSdk(resp)).extract(request())


@pytest.mark.parametrize(
    "exc",
    [
        status_error(anthropic.RateLimitError, 429),
        status_error(anthropic.InternalServerError, 529),
        status_error(anthropic.InternalServerError, 500),
        anthropic.APIConnectionError(request=httpx2.Request("POST", "https://x")),
    ],
)
def test_transient_provider_errors_are_marked_retryable(exc: Exception) -> None:
    with pytest.raises(TransientLlmError):
        client(FakeSdk(raises=exc)).extract(request())


@pytest.mark.parametrize(
    "exc",
    [
        status_error(anthropic.AuthenticationError, 401),
        status_error(anthropic.BadRequestError, 400),
        status_error(anthropic.PermissionDeniedError, 403),
    ],
)
def test_other_provider_errors_are_permanent(exc: Exception) -> None:
    with pytest.raises(PermanentLlmError):
        client(FakeSdk(raises=exc)).extract(request())


def test_error_messages_do_not_leak_provider_text() -> None:
    with pytest.raises(PermanentLlmError) as info:
        client(FakeSdk(raises=status_error(anthropic.BadRequestError, 400))).extract(request())
    assert "boom" not in str(info.value)
