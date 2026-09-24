"""Test doubles for the model client. Nothing here touches the network."""

from dataclasses import dataclass, field
from typing import Any

from intake.core.llm_budget import ModelPrice, cost_usd_micros
from intake.extract.llm import LlmRequest, LlmResult

Step = LlmResult | Exception


def ok_payload(**overrides: dict[str, Any]) -> dict[str, Any]:
    from intake.extract.schema import HEADER_FIELDS, LINE_FIELDS

    def f(value: str | None) -> dict[str, Any]:
        return {"value": value, "self_confidence": "high", "page": 1 if value else None}

    data: dict[str, Any] = {n: f("x") for n in HEADER_FIELDS}
    data["lines"] = [{n: f("1") for n in LINE_FIELDS}]
    data.update(overrides)
    return data


def result(payload: dict[str, Any] | None = None, tin: int = 1000, tout: int = 500) -> LlmResult:
    return LlmResult(payload or ok_payload(), tin, tout, latency_ms=5)


@dataclass
class ScriptedClient:
    """Plays back a list of results/exceptions, one per call, and records every request."""

    steps: list[Step]
    model: str = "claude-sonnet-5"
    price: ModelPrice = field(default_factory=lambda: ModelPrice(2_000_000, 10_000_000))
    requests: list[LlmRequest] = field(default_factory=list)

    def cost_micros(self, input_tokens: int, output_tokens: int) -> int:
        return cost_usd_micros(input_tokens, output_tokens, self.price)

    def extract(self, request: LlmRequest) -> LlmResult:
        self.requests.append(request)
        if not self.steps:
            raise AssertionError("the model was called more times than the test scripted")
        step = self.steps.pop(0)
        if isinstance(step, Exception):
            raise step
        return step
