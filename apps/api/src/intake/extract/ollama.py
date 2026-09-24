"""Optional local backend (DEMO ONLY): a vision model served by Ollama, no data leaves the machine.

Small local models read invoices noticeably worse than Claude, so numbers measured with this
client say nothing about the Claude-based reference build. Answers still go through the same schema
validation, retry, confidence scoring and review routing. Costs nothing, so the spend cap never
trips. API details follow docs.ollama.com (checked 2026-09-25): `POST /api/chat`, `stream: false`,
`images` as base64 on the message, `format` as a JSON schema, usage in `prompt_eval_count` and
`eval_count`.
"""

import base64
import json
import time
import urllib.error
import urllib.request
from typing import Any
from urllib.parse import urlparse

from intake.extract.llm import (
    LlmOutputError,
    LlmRequest,
    LlmResult,
    PermanentLlmError,
    TransientLlmError,
)


def _post_json(url: str, body: dict[str, Any], timeout_s: float) -> dict[str, Any]:
    req = urllib.request.Request(  # noqa: S310 - scheme is checked in OllamaClient.__init__
        url,
        data=json.dumps(body).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:  # noqa: S310
            data: dict[str, Any] = json.loads(resp.read())
            return data
    except urllib.error.HTTPError as exc:
        if exc.code >= 500 or exc.code in (408, 429):
            raise TransientLlmError("local model server error") from None
        raise PermanentLlmError(
            f"local model server rejected the request (HTTP {exc.code})"
        ) from None
    except (urllib.error.URLError, ConnectionError, TimeoutError):
        raise TransientLlmError("local model server unreachable") from None
    except json.JSONDecodeError:
        raise TransientLlmError("local model server sent an unreadable reply") from None


class OllamaClient:
    def __init__(
        self,
        *,
        model: str,
        base_url: str = "http://localhost:11434",
        num_ctx: int = 8192,
        timeout_s: float = 300.0,
    ) -> None:
        if urlparse(base_url).scheme not in ("http", "https"):
            raise ValueError("OLLAMA_BASE_URL must be an http(s) URL")
        self.model = model
        self._url = base_url.rstrip("/") + "/api/chat"
        self._num_ctx = num_ctx
        self._timeout_s = timeout_s

    def cost_micros(self, input_tokens: int, output_tokens: int) -> int:
        return 0  # runs on your own hardware

    def extract(self, request: LlmRequest) -> LlmResult:
        layers = [f"Text layer, page {n}:\n{t}" for n, t in enumerate(request.text_layers, 1) if t]
        ask = "Record this invoice as JSON matching the schema."
        if request.validation_error:
            ask = (
                f"Your previous answer was rejected: {request.validation_error}\n"
                "Answer again with corrected JSON matching the schema."
            )
        body = {
            "model": self.model,
            "stream": False,
            "format": request.schema,
            "options": {"temperature": 0, "num_ctx": self._num_ctx},
            "messages": [
                {
                    "role": "system",
                    # Ollama advises repeating the schema in the prompt to ground the answer.
                    "content": f"{request.system}\n\nJSON schema:\n{json.dumps(request.schema)}",
                },
                {
                    "role": "user",
                    "content": "\n\n".join([*layers, ask]),
                    "images": [base64.b64encode(p.data).decode("ascii") for p in request.pages],
                },
            ],
        }
        started = time.monotonic()
        reply = _post_json(self._url, body, self._timeout_s)
        usage = {
            "input_tokens": int(reply.get("prompt_eval_count") or 0),
            "output_tokens": int(reply.get("eval_count") or 0),
            "latency_ms": int((time.monotonic() - started) * 1000),
        }
        if reply.get("done_reason") == "length":
            raise LlmOutputError("answer was cut off at the token limit", **usage)
        try:
            payload = json.loads(reply["message"]["content"])
        except (KeyError, TypeError, json.JSONDecodeError):
            raise LlmOutputError("answer was not valid JSON", **usage) from None
        if not isinstance(payload, dict):
            raise LlmOutputError("answer was not a JSON object", **usage)
        return LlmResult(
            payload=payload,
            input_tokens=usage["input_tokens"],
            output_tokens=usage["output_tokens"],
            latency_ms=usage["latency_ms"],
        )
