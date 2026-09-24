"""Replays recorded model answers so tests and CI never touch the network.

A fixture is `<dir>/<request_hash>.json` with the tool-call `payload` and the token usage the real
call reported. A request with no fixture is an error, never a silent live call.
"""

import json
from pathlib import Path
from typing import Any

from intake.core.llm_budget import ModelPrice, cost_usd_micros
from intake.extract.llm import LlmRequest, LlmResult


class FixtureMissing(Exception):
    pass


def write_fixture(
    directory: Path,
    key: str,
    payload: dict[str, Any],
    *,
    input_tokens: int = 1500,
    output_tokens: int = 700,
) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{key}.json"
    path.write_text(
        json.dumps(
            {"payload": payload, "input_tokens": input_tokens, "output_tokens": output_tokens},
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return path


class RecordedClient:
    def __init__(self, *, model: str, directory: Path, price: ModelPrice | None = None) -> None:
        self.model = model
        self._dir = directory
        self._price = price or ModelPrice(0, 0)
        self.calls = 0  # how many requests reached the "model" (cache hits never do)

    def cost_micros(self, input_tokens: int, output_tokens: int) -> int:
        return cost_usd_micros(input_tokens, output_tokens, self._price)

    def extract(self, request: LlmRequest) -> LlmResult:
        path = self._dir / f"{request.key}.json"
        if not request.key or not path.is_file():
            raise FixtureMissing(f"no recorded response for request {request.key!r}")
        self.calls += 1
        data = json.loads(path.read_text(encoding="utf-8"))
        return LlmResult(
            payload=data["payload"],
            input_tokens=data["input_tokens"],
            output_tokens=data["output_tokens"],
            latency_ms=1,
        )
