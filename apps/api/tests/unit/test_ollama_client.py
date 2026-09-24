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
from intake.extract.ollama import OllamaClient


class Server:
    def __init__(self, status: int, reply: Any) -> None:
        self.status, self.reply = status, reply
        self.bodies: list[dict[str, Any]] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802
                length = int(self.headers["Content-Length"])
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
        "message": {"role": "assistant", "content": content},
        "done": True,
        "done_reason": "stop",
        "prompt_eval_count": 900,
        "eval_count": 120,
        **extra,
    }


def test_request_shape_and_result(serve: Callable[[int, Any], Server]) -> None:
    server = serve(200, ok_reply())
    result = OllamaClient(model="qwen-vl", base_url=server.url, num_ctx=4096).extract(REQ)
    body = server.bodies[0]
    assert body["path"] == "/api/chat"
    assert body["model"] == "qwen-vl" and body["stream"] is False
    assert body["format"] == REQ.schema
    assert body["options"] == {"temperature": 0, "num_ctx": 4096}
    system, user = body["messages"]
    assert system["role"] == "system" and json.dumps(REQ.schema) in system["content"]
    assert user["role"] == "user" and len(user["images"]) == 1
    assert "Text layer, page 1:\nhello" in user["content"]
    assert result.payload == {"a": "1"}
    assert (result.input_tokens, result.output_tokens) == (900, 120)


def test_validation_error_is_sent_on_retry(serve: Callable[[int, Any], Server]) -> None:
    server = serve(200, ok_reply())
    retry = LlmRequest(**{**REQ.__dict__, "validation_error": "total: field required"})
    OllamaClient(model="m", base_url=server.url).extract(retry)
    assert "total: field required" in server.bodies[0]["messages"][1]["content"]


def test_local_model_costs_nothing() -> None:
    assert OllamaClient(model="m").cost_micros(10**9, 10**9) == 0


@pytest.mark.parametrize("content", ["not json", "[1, 2]", ""])
def test_unusable_answers_are_output_errors_that_keep_usage(
    serve: Callable[[int, Any], Server], content: str
) -> None:
    server = serve(200, ok_reply(content))
    with pytest.raises(LlmOutputError) as info:
        OllamaClient(model="m", base_url=server.url).extract(REQ)
    assert (info.value.input_tokens, info.value.output_tokens) == (900, 120)


def test_cut_off_answer_is_an_output_error(serve: Callable[[int, Any], Server]) -> None:
    server = serve(200, ok_reply(done_reason="length"))
    with pytest.raises(LlmOutputError):
        OllamaClient(model="m", base_url=server.url).extract(REQ)


@pytest.mark.parametrize("status", [500, 503, 429])
def test_server_errors_are_retryable(serve: Callable[[int, Any], Server], status: int) -> None:
    server = serve(status, {"error": "busy"})
    with pytest.raises(TransientLlmError):
        OllamaClient(model="m", base_url=server.url).extract(REQ)


@pytest.mark.parametrize("status", [400, 404])
def test_client_errors_are_permanent_and_do_not_leak_the_reply(
    serve: Callable[[int, Any], Server], status: int
) -> None:
    server = serve(status, {"error": "model 'x' not found, secret detail"})
    with pytest.raises(PermanentLlmError) as info:
        OllamaClient(model="m", base_url=server.url).extract(REQ)
    assert "secret" not in str(info.value)


def test_unreachable_server_is_retryable() -> None:
    with pytest.raises(TransientLlmError):
        OllamaClient(model="m", base_url="http://127.0.0.1:1", timeout_s=2).extract(REQ)


def test_garbled_http_reply_is_retryable(serve: Callable[[int, Any], Server]) -> None:
    server = serve(200, b"<html>proxy error</html>")
    with pytest.raises(TransientLlmError):
        OllamaClient(model="m", base_url=server.url).extract(REQ)


@pytest.mark.parametrize("url", ["file:///etc/passwd", "ftp://host", "localhost:11434"])
def test_only_http_urls_are_accepted(url: str) -> None:
    with pytest.raises(ValueError):
        OllamaClient(model="m", base_url=url)
