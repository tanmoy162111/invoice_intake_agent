"""Optional backend (DEMO / MANUAL TESTING ONLY): an open-weight vision model through OpenRouter's
OpenAI-compatible API, for trying the pipeline without an Anthropic key.

Numbers measured with this client say nothing about the Claude-based reference build (playbook §8):
a different model, and OpenRouter can route the same model slug through different upstream
providers. Answers still go through the same schema validation, retry, confidence scoring and
review routing. Sends the document's page images to OpenRouter and whichever provider it routes
to - a real third-party data path (see docs/data-handling.md) - so demo or synthetic documents
only, never real client data (see CLAUDE.md). API details follow openrouter.ai/docs (checked
2026-09-28): `POST /chat/completions`, `Authorization: Bearer <key>`, OpenAI-shaped `messages` with
`image_url` data URIs, usage in `usage.prompt_tokens` / `usage.completion_tokens`.

No `response_format` is sent: OpenRouter fans out to many different upstream providers with
inconsistent support for it, so the schema is only carried in the prompt (like the Ollama client),
and an answer that does not parse or does not fit is a retryable `LlmOutputError`, same as every
other client. Observed live (2026-09-28, qwen/qwen2.5-vl-72b-instruct): the answer often arrives
wrapped in a ```json markdown fence rather than bare JSON, so a fence is stripped before parsing.

Also observed live: HTTP 402 with `"in_flight_budget_exhausted"` ("this request would exceed your
available credits given your current in-flight requests... retry after in-flight requests settle")
is OpenRouter's own advice to retry, not a rejection of the request itself - treated as transient,
like a rate limit, rather than permanent.
"""

import base64
import json
import re
import time
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlparse

from intake.core.llm_budget import cost_usd_micros, price_for
from intake.extract.llm import (
    LlmOutputError,
    LlmRequest,
    LlmResult,
    PermanentLlmError,
    TransientLlmError,
)

_URL_SUFFIX = "/chat/completions"
_FENCE = re.compile(r"^\s*```(?:json)?\s*\n?(.*?)\n?\s*```\s*$", re.DOTALL)


def _is_in_flight_budget_error(exc: urllib.error.HTTPError) -> bool:
    """OpenRouter's 402 for a request that would exceed the account's in-flight budget names this
    reason and explicitly asks for a retry; a 402 for any other reason (e.g. truly out of credits)
    does not, and stays permanent. Never raises: an unreadable body just means "not this reason"."""
    try:
        body = json.loads(exc.read())
        return bool(body["error"]["metadata"]["reason"] == "in_flight_budget_exhausted")
    except (OSError, ValueError, KeyError, TypeError):
        return False


def _post_json(url: str, body: dict[str, Any], api_key: str, timeout_s: float) -> dict[str, Any]:
    req = urllib.request.Request(  # noqa: S310 - url is always https://openrouter.ai/...
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json", "Authorization": f"Bearer {api_key}"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:  # noqa: S310
            data: dict[str, Any] = json.loads(resp.read())
            return data
    except urllib.error.HTTPError as exc:
        if exc.code >= 500 or exc.code in (408, 429):
            raise TransientLlmError("provider unavailable or rate limited") from None
        if exc.code == 402 and _is_in_flight_budget_error(exc):
            raise TransientLlmError(
                "provider asked to retry once in-flight requests settle"
            ) from None
        raise PermanentLlmError(f"provider rejected the request (HTTP {exc.code})") from None
    except (urllib.error.URLError, ConnectionError, TimeoutError):
        raise TransientLlmError("provider unreachable") from None
    except json.JSONDecodeError:
        raise TransientLlmError("provider sent an unreadable reply") from None


def _strip_code_fence(content: str) -> str:
    """Some models wrap the answer in a ```json ... ``` fence despite the prompt asking for bare
    JSON. Strip one if present; otherwise return the content unchanged."""
    m = _FENCE.match(content)
    return m.group(1) if m else content


def _messages(request: LlmRequest) -> list[dict[str, Any]]:
    content: list[dict[str, Any]] = []
    for n, page in enumerate(request.pages, start=1):
        content.append({"type": "text", "text": f"Page {n}:"})
        data = base64.b64encode(page.data).decode("ascii")
        content.append(
            {"type": "image_url", "image_url": {"url": f"data:{page.media_type};base64,{data}"}}
        )
    layers = [f"Text layer, page {n}:\n{t}" for n, t in enumerate(request.text_layers, 1) if t]
    if layers:
        content.append({"type": "text", "text": "\n\n".join(layers)})
    ask = "Record this invoice as JSON matching the schema."
    if request.validation_error:
        ask = (
            f"Your previous answer was rejected: {request.validation_error}\n"
            "Answer again with corrected JSON matching the schema."
        )
    content.append({"type": "text", "text": ask})
    system = f"{request.system}\n\nJSON schema:\n{json.dumps(request.schema)}"
    return [{"role": "system", "content": system}, {"role": "user", "content": content}]


class OpenRouterClient:
    def __init__(
        self,
        *,
        model: str,
        api_key: str,
        base_url: str = "https://openrouter.ai/api/v1",
        timeout_s: float = 120.0,
    ) -> None:
        if urlparse(base_url).scheme not in ("http", "https"):
            raise ValueError("OPENROUTER_BASE_URL must be an http(s) URL")
        self._price = price_for(model)  # refuse a model we can't cost
        self.model = model
        self._api_key = api_key
        self._url = base_url.rstrip("/") + _URL_SUFFIX
        self._timeout_s = timeout_s

    def cost_micros(self, input_tokens: int, output_tokens: int) -> int:
        return cost_usd_micros(input_tokens, output_tokens, self._price)

    def extract(self, request: LlmRequest) -> LlmResult:
        body = {
            "model": self.model,
            "temperature": 0,
            "max_tokens": request.max_output_tokens,
            "messages": _messages(request),
        }
        started = time.monotonic()
        reply = _post_json(self._url, body, self._api_key, self._timeout_s)
        usage = reply.get("usage") or {}
        result_usage = {
            "input_tokens": int(usage.get("prompt_tokens") or 0),
            "output_tokens": int(usage.get("completion_tokens") or 0),
            "latency_ms": int((time.monotonic() - started) * 1000),
        }
        choices = reply.get("choices") or []
        if not choices:
            raise LlmOutputError("no answer in the reply", **result_usage)
        choice = choices[0]
        if choice.get("finish_reason") == "length":
            raise LlmOutputError("answer was cut off at the token limit", **result_usage)
        try:
            content = choice["message"]["content"]
            payload = json.loads(
                _strip_code_fence(content) if isinstance(content, str) else content
            )
        except (KeyError, TypeError, json.JSONDecodeError):
            raise LlmOutputError("answer was not valid JSON", **result_usage) from None
        if not isinstance(payload, dict):
            raise LlmOutputError("answer was not a JSON object", **result_usage)
        return LlmResult(
            payload=payload,
            input_tokens=result_usage["input_tokens"],
            output_tokens=result_usage["output_tokens"],
            latency_ms=result_usage["latency_ms"],
        )
