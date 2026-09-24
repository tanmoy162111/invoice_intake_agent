"""The model call. `LlmClient` is the seam: the pipeline, cache, budgets and logging never know
which provider answered, and tests use a fake so CI never touches the network.

`AnthropicClient` sends the page images (and text layer) with a strict tool whose input schema is
`InvoiceExtraction`. SDK details follow platform.claude.com docs (checked 2026-09-25): base64
image blocks, `strict: true` tools, `tool_choice` forced only where the model allows it.
"""

import base64
import time
from dataclasses import dataclass
from typing import Any, Protocol

import anthropic

from intake.core.llm_budget import cost_usd_micros, price_for

TOOL_NAME = "record_invoice"
# Claude Opus 5.5 and Fable 5.1 return a 400 for tool_choice "tool"/"any"; use "auto" + strict.
FORCED_TOOL_UNSUPPORTED = frozenset({"claude-opus-5-5", "claude-fable-5-1"})


class LlmError(Exception):
    """Base class. Messages never include document content."""


class TransientLlmError(LlmError):
    """Rate limit, overload, timeout or connection failure that survived the SDK's own retries.
    Retry the job later."""


class PermanentLlmError(LlmError):
    """The provider rejected the request (bad key, bad request, no access). Retrying won't help."""


class LlmOutputError(LlmError):
    """The model answered but not with a complete tool call (none, or cut off at max tokens).
    The call was billed, so its usage is carried for cost accounting."""

    def __init__(
        self, message: str, *, input_tokens: int = 0, output_tokens: int = 0, latency_ms: int = 0
    ) -> None:
        super().__init__(message)
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.latency_ms = latency_ms


@dataclass(frozen=True)
class PageInput:
    data: bytes
    media_type: str  # image/png or image/jpeg


@dataclass(frozen=True)
class LlmRequest:
    system: str
    pages: list[PageInput]
    text_layers: list[str]  # one per page; empty strings when there is no text layer
    schema: dict[str, Any]
    max_output_tokens: int
    validation_error: str | None = None  # set on the one schema retry
    key: str = ""  # the cache key (request hash); recorded fixtures are looked up by it


@dataclass(frozen=True)
class LlmResult:
    payload: dict[str, Any]
    input_tokens: int
    output_tokens: int
    latency_ms: int
    request_id: str | None = None


class LlmClient(Protocol):
    model: str

    def extract(self, request: LlmRequest) -> LlmResult: ...

    def cost_micros(self, input_tokens: int, output_tokens: int) -> int: ...


def build_content(request: LlmRequest) -> list[dict[str, Any]]:
    """Images first (each labelled), then the text layer, then the instruction."""
    content: list[dict[str, Any]] = []
    for n, page in enumerate(request.pages, start=1):
        content.append({"type": "text", "text": f"Page {n}:"})
        content.append(
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": page.media_type,
                    "data": base64.b64encode(page.data).decode("ascii"),
                },
            }
        )
    layers = [f"Text layer, page {n}:\n{t}" for n, t in enumerate(request.text_layers, 1) if t]
    if layers:
        content.append({"type": "text", "text": "\n\n".join(layers)})
    ask = f"Record this invoice by calling {TOOL_NAME}."
    if request.validation_error:
        ask = (
            f"Your previous answer was rejected: {request.validation_error}\n"
            f"Call {TOOL_NAME} again with a corrected answer."
        )
    content.append({"type": "text", "text": ask})
    return content


class AnthropicClient:
    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        timeout_s: float = 120.0,
        sdk: Any | None = None,
    ) -> None:
        self._price = price_for(model)  # refuse a model we can't cost
        self.model = model
        # The SDK retries connection errors, 408/409/429 and 5xx twice with backoff by default.
        self._sdk: Any = sdk or anthropic.Anthropic(
            api_key=api_key, timeout=timeout_s, max_retries=2
        )

    def cost_micros(self, input_tokens: int, output_tokens: int) -> int:
        return cost_usd_micros(input_tokens, output_tokens, self._price)

    def extract(self, request: LlmRequest) -> LlmResult:
        tool = {
            "name": TOOL_NAME,
            "description": (
                "Record the invoice exactly as printed. Call this once. Use null for any field "
                "that is not on the invoice; never guess."
            ),
            "strict": True,
            "input_schema": request.schema,
        }
        tool_choice: dict[str, str] = (
            {"type": "auto"}
            if self.model in FORCED_TOOL_UNSUPPORTED
            else {"type": "tool", "name": TOOL_NAME}
        )
        started = time.monotonic()
        try:
            response = self._sdk.messages.create(
                model=self.model,
                max_tokens=request.max_output_tokens,
                system=request.system,
                tools=[tool],
                tool_choice=tool_choice,
                messages=[{"role": "user", "content": build_content(request)}],
            )
        except (
            anthropic.APIConnectionError,
            anthropic.RateLimitError,
            anthropic.InternalServerError,
        ):
            raise TransientLlmError("provider unavailable or rate limited") from None
        except anthropic.APIStatusError as exc:
            raise PermanentLlmError(
                f"provider rejected the request (HTTP {exc.status_code})"
            ) from None
        latency_ms = int((time.monotonic() - started) * 1000)

        usage = {
            "input_tokens": response.usage.input_tokens,
            "output_tokens": response.usage.output_tokens,
            "latency_ms": latency_ms,
        }
        if response.stop_reason == "max_tokens":
            raise LlmOutputError("answer was cut off at max_tokens", **usage)
        block = next(
            (b for b in response.content if b.type == "tool_use" and b.name == TOOL_NAME), None
        )
        if block is None:
            raise LlmOutputError("no tool call in the answer", **usage)
        return LlmResult(
            payload=dict(block.input),
            input_tokens=usage["input_tokens"],
            output_tokens=usage["output_tokens"],
            latency_ms=latency_ms,
            request_id=getattr(response, "_request_id", None),
        )
