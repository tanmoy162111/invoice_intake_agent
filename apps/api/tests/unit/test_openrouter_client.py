import json
import threading
from collections.abc import Callable, Iterator
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any

import pytest

from intake.extract.llm import (
    LlmOutputError,
    LlmRequest,
    PageInput,
    PermanentLlmError,
    TransientLlmError,
)
from intake.extract.openrouter import OpenRouterClient, _strip_code_fence

MODEL = "qwen/qwen2.5-vl-72b-instruct"


class Server:
    def __init__(self, status: int, reply: Any) -> None:
        self.status, self.reply = status, reply
        self.bodies: list[dict[str, Any]] = []
        self.headers: list[Any] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers["Content-Length"])
                outer.headers.append(self.headers)
                outer.bodies.append({"path": self.path, **json.loads(self.rfile.read(length))})
                payload = (
                    outer.reply
                    if isinstance(outer.reply, bytes)
                    else json.dumps(outer.reply).encode()
                )
                self.send_response(outer.status)
                self.send_header("Content-Type", "application/json")
                self.end_headers()
                self.wfile.write(payload)

            def log_message(self, *args: object) -> None:
                pass

        self.httpd = HTTPServer(("127.0.0.1", 0), Handler)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.httpd.server_port}"


@pytest.fixture
def serve() -> Iterator[Callable[[int, Any], Server]]:
    servers: list[Server] = []

    def make(status: int, reply: Any) -> Server:
        servers.append(Server(status, reply))
        return servers[-1]

    yield make
    for s in servers:
        s.httpd.shutdown()
        s.httpd.server_close()


REQ = LlmRequest(
    system="sys",
    pages=[PageInput(b"\x89PNGa", "image/png")],
    text_layers=["hello"],
    schema={"type": "object", "properties": {"a": {"type": "string"}}},
    max_output_tokens=4000,
)


def ok_reply(content: str = '{"a": "1"}', **extra: Any) -> dict[str, Any]:
    return {
        "choices": [
            {"message": {"role": "assistant", "content": content}, "finish_reason": "stop"}
        ],
        "usage": {"prompt_tokens": 900, "completion_tokens": 120},
        **extra,
    }


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ('{"a": "1"}', '{"a": "1"}'),
        ('```json\n{"a": "1"}\n```', '{"a": "1"}'),
        ('```\n{"a": "1"}\n```', '{"a": "1"}'),
        ('  ```json\n{"a": "1"}\n```  ', '{"a": "1"}'),
        ("not fenced at all", "not fenced at all"),
    ],
)
def test_strip_code_fence(content: str, expected: str) -> None:
    assert _strip_code_fence(content) == expected


def test_a_markdown_fenced_answer_still_parses(serve: Callable[[int, Any], Server]) -> None:
    # Observed live from qwen/qwen2.5-vl-72b-instruct on 2026-09-28: the model wraps its JSON
    # answer in a ```json fence despite the prompt asking for bare JSON.
    fenced = '```json\n{\n  "a": "1"\n}\n```'
    server = serve(200, ok_reply(fenced))
    result = OpenRouterClient(model=MODEL, api_key="k", base_url=server.url).extract(REQ)
    assert result.payload == {"a": "1"}


def test_request_shape_and_result(serve: Callable[[int, Any], Server]) -> None:
    server = serve(200, ok_reply())
    result = OpenRouterClient(model=MODEL, api_key="sk-or-test", base_url=server.url).extract(REQ)
    body = server.bodies[0]
    assert body["path"] == "/chat/completions"
    assert body["model"] == MODEL and body["temperature"] == 0
    assert server.headers[0]["Authorization"] == "Bearer sk-or-test"
    system, user = body["messages"]
    assert system["role"] == "system" and json.dumps(REQ.schema) in system["content"]
    assert user["role"] == "user"
    images = [c for c in user["content"] if c["type"] == "image_url"]
    assert len(images) == 1 and images[0]["image_url"]["url"].startswith("data:image/png;base64,")
    texts = " ".join(c["text"] for c in user["content"] if c["type"] == "text")
    assert "Text layer, page 1:\nhello" in texts
    assert result.payload == {"a": "1"}
    assert (result.input_tokens, result.output_tokens) == (900, 120)


def test_validation_error_is_sent_on_retry(serve: Callable[[int, Any], Server]) -> None:
    server = serve(200, ok_reply())
    retry = LlmRequest(**{**REQ.__dict__, "validation_error": "total: field required"})
    OpenRouterClient(model=MODEL, api_key="k", base_url=server.url).extract(retry)
    user = server.bodies[0]["messages"][1]
    texts = " ".join(c["text"] for c in user["content"] if c["type"] == "text")
    assert "total: field required" in texts


def test_cost_uses_the_priced_table() -> None:
    client = OpenRouterClient(model=MODEL, api_key="k")
    # $0.80 / $1.00 per Mtok: 1M input + 1M output = 1_800_000 micros
    assert client.cost_micros(1_000_000, 1_000_000) == 1_800_000


@pytest.mark.parametrize("content", ["not json", "[1, 2]", ""])
def test_unusable_answers_are_output_errors_that_keep_usage(
    serve: Callable[[int, Any], Server], content: str
) -> None:
    server = serve(200, ok_reply(content))
    with pytest.raises(LlmOutputError) as info:
        OpenRouterClient(model=MODEL, api_key="k", base_url=server.url).extract(REQ)
    assert (info.value.input_tokens, info.value.output_tokens) == (900, 120)


def test_cut_off_answer_is_an_output_error(serve: Callable[[int, Any], Server]) -> None:
    reply = {
        "choices": [{"message": {"content": "{}"}, "finish_reason": "length"}],
        "usage": {"prompt_tokens": 900, "completion_tokens": 120},
    }
    server = serve(200, reply)
    with pytest.raises(LlmOutputError):
        OpenRouterClient(model=MODEL, api_key="k", base_url=server.url).extract(REQ)


def test_no_choices_is_an_output_error(serve: Callable[[int, Any], Server]) -> None:
    server = serve(200, {"choices": [], "usage": {"prompt_tokens": 5, "completion_tokens": 0}})
    with pytest.raises(LlmOutputError):
        OpenRouterClient(model=MODEL, api_key="k", base_url=server.url).extract(REQ)


@pytest.mark.parametrize("status", [500, 503, 429])
def test_server_errors_are_retryable(serve: Callable[[int, Any], Server], status: int) -> None:
    server = serve(status, {"error": "busy"})
    with pytest.raises(TransientLlmError):
        OpenRouterClient(model=MODEL, api_key="k", base_url=server.url).extract(REQ)


@pytest.mark.parametrize("status", [400, 404])
def test_client_errors_are_permanent_and_do_not_leak_the_reply(
    serve: Callable[[int, Any], Server], status: int
) -> None:
    server = serve(status, {"error": "invalid api key, secret detail"})
    with pytest.raises(PermanentLlmError) as info:
        OpenRouterClient(model=MODEL, api_key="k", base_url=server.url).extract(REQ)
    assert "secret" not in str(info.value)


def test_402_in_flight_budget_exhausted_is_retryable_not_permanent(
    serve: Callable[[int, Any], Server],
) -> None:
    # Observed live: OpenRouter's own advice is "retry after in-flight requests settle".
    reply = {
        "error": {
            "message": "This request would exceed your available credits...",
            "code": 402,
            "metadata": {"reason": "in_flight_budget_exhausted"},
        }
    }
    server = serve(402, reply)
    with pytest.raises(TransientLlmError):
        OpenRouterClient(model=MODEL, api_key="k", base_url=server.url).extract(REQ)


def test_402_for_any_other_reason_stays_permanent(serve: Callable[[int, Any], Server]) -> None:
    reply = {"error": {"message": "insufficient credits", "code": 402, "metadata": {}}}
    server = serve(402, reply)
    with pytest.raises(PermanentLlmError):
        OpenRouterClient(model=MODEL, api_key="k", base_url=server.url).extract(REQ)


def test_402_with_an_unreadable_body_stays_permanent(serve: Callable[[int, Any], Server]) -> None:
    server = serve(402, b"not json")
    with pytest.raises(PermanentLlmError):
        OpenRouterClient(model=MODEL, api_key="k", base_url=server.url).extract(REQ)


def test_unreachable_server_is_retryable() -> None:
    with pytest.raises(TransientLlmError):
        OpenRouterClient(
            model=MODEL, api_key="k", base_url="http://127.0.0.1:1", timeout_s=2
        ).extract(REQ)


def test_garbled_http_reply_is_retryable(serve: Callable[[int, Any], Server]) -> None:
    server = serve(200, b"<html>proxy error</html>")
    with pytest.raises(TransientLlmError):
        OpenRouterClient(model=MODEL, api_key="k", base_url=server.url).extract(REQ)


def test_an_unpriced_model_is_refused_not_guessed() -> None:
    from intake.core.llm_budget import UnknownModelError

    with pytest.raises(UnknownModelError):
        OpenRouterClient(model="mystery-1", api_key="k")


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://host", "openrouter.ai/api/v1"])
def test_only_http_urls_are_accepted(url: str) -> None:
    with pytest.raises(ValueError):
        OpenRouterClient(model=MODEL, api_key="k", base_url=url)
