# 0010 — An OpenRouter backend for testing without an Anthropic key
Date: 2026-09-28 · Status: accepted

## Context
Not everyone testing this system has an `ANTHROPIC_API_KEY`. The playbook already accepts one non-
Claude backend for exactly this reason (`LLM_PROVIDER=ollama`, ADR-less, playbook §M3/manual §4.9):
local, free, but slower and worse at reading invoices. A second, no-local-hardware option was asked
for: [OpenRouter](https://openrouter.ai), an OpenAI-compatible gateway to many open-weight (and other)
models, which the requester already holds a key for.

## Decision
1. Add `LLM_PROVIDER=openrouter` as a third optional backend, alongside `ollama`, in
   `extract/openrouter.py`, wired through `extract/factory.py` exactly like the other two: same
   `LlmClient` protocol, same pipeline, same schema validation, retry, confidence scoring and review
   routing downstream. No new code path in `extract/pipeline.py` or anywhere in `core/`.
2. Structured output is asked for by prompt only (schema repeated in the system message), the same
   approach as the Ollama client — not `response_format`/tool-calling, because OpenRouter fans one
   model slug out to different upstream providers with inconsistent support for either. An answer that
   is not valid JSON, or not an object, is a retryable `LlmOutputError`, same as every other client.
3. Cost is a static per-model table entry (`core/llm_budget.PRICES`), not OpenRouter's own per-request
   `usage.cost` field. OpenRouter reports real-time cost, but only per completed call, after the fact;
   the existing budget/cache accounting (`cost_micros(input_tokens, output_tokens)`) is a pure function
   of the token counts, computed *before* the row is written, and every other client already works this
   way. A model with no table entry is refused (`UnknownModelError`), never guessed — same rule as
   Anthropic.
4. Default demo model: `qwen/qwen2.5-vl-72b-instruct` (vision-capable, priced $0.80 / $1.00 per Mtok
   in/out as listed on openrouter.ai, checked 2026-09-28).
5. Documented as demo/manual-testing only, same standing as Ollama: never the reference build the
   eval report (M10) is about, and a new sentence in `data-handling.md` — invoice pages now also cross
   a *second* third party (OpenRouter, plus whichever provider it routes the request to), each with
   its own retention terms outside our control.

## Why
Reusing the existing `LlmClient` seam means this is additive: nothing downstream of extraction needed
to change or be re-tested. Prompt-only structured output trades a little strictness for working across
whichever provider OpenRouter happens to route to today. A static price table keeps cost accounting a
pure, pre-call function like every other backend, at the cost of not reflecting OpenRouter's own markup
or per-provider price variance exactly — acceptable for a demo/testing-only backend that is never
compared against the Claude-based numbers anyway.

## Consequences
- Adding or changing the demo model means adding or updating its `core/llm_budget.PRICES` entry by
  hand; there is no live price lookup.
- OpenRouter's actual per-request `usage.cost` (visible in your OpenRouter dashboard) can differ
  slightly from what this system records, because of routing and markup. Not reconciled.
- Real client data must never be sent through this backend (nor Ollama's): both are demo/manual-testing
  only, and OpenRouter adds a second external party to the data-handling picture.
