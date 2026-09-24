"""Pure budget rules for model calls: price, cache key, daily spend cap, per-document limits.

Money is integer micro-dollars (1 USD = 1,000,000). Never floats.
"""

import hashlib
import math
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta

MICROS_PER_USD = 1_000_000
_PATCH_PX = 28  # Claude reads images in 28x28 px patches (one visual token each)
_MAX_VISUAL_TOKENS = 4784  # per-image cap for Claude 4.7+ models


class UnknownModelError(ValueError):
    """No price is known for this model, so its cost can't be counted. Refuse, don't guess."""


@dataclass(frozen=True)
class ModelPrice:
    """USD micros per million tokens."""

    input_micros_per_mtok: int
    output_micros_per_mtok: int


# Source: platform.claude.com/docs/en/about-claude/models/overview, checked 2026-09-25.
PRICES: dict[str, ModelPrice] = {
    "claude-fable-5-1": ModelPrice(10_000_000, 50_000_000),
    "claude-opus-5-5": ModelPrice(4_000_000, 20_000_000),
    "claude-sonnet-5": ModelPrice(2_000_000, 10_000_000),
    "claude-haiku-4-5-20251001": ModelPrice(1_000_000, 5_000_000),
}


def price_for(model: str) -> ModelPrice:
    try:
        return PRICES[model]
    except KeyError:
        raise UnknownModelError(f"no price known for model {model!r}") from None


def cost_usd_micros(input_tokens: int, output_tokens: int, price: ModelPrice) -> int:
    """Cost of one call, rounded up so spend is never under-counted."""
    if input_tokens < 0 or output_tokens < 0:
        raise ValueError("token counts must be >= 0")
    total = (
        input_tokens * price.input_micros_per_mtok + output_tokens * price.output_micros_per_mtok
    )
    return -(-total // 1_000_000)


def request_hash(file_sha256: str, model: str, prompt_version: str) -> str:
    """Cache key for one extraction: (file, model, prompt version). Length-prefixed parts so
    shifting characters between them can never collide."""
    h = hashlib.sha256()
    for part in (file_sha256, model, prompt_version):
        h.update(f"{len(part)}:{part}|".encode())
    return h.hexdigest()


def cap_reached(spent_today_micros: int, cap_micros: int | None) -> bool:
    """True when today's spend has reached the cap. None means no cap."""
    return cap_micros is not None and spent_today_micros >= cap_micros


def next_utc_midnight(now: datetime) -> datetime:
    """When a spend-paused job may run again: the start of the next UTC day."""
    if now.tzinfo is None:
        raise ValueError("now must be timezone-aware")
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start + timedelta(days=1)


def estimate_input_tokens(page_sizes: Sequence[tuple[int, int]], text_chars: int) -> int:
    """Upper-ish estimate of input tokens: image patches per page plus ~1 token per 3 chars."""
    images = sum(
        min(math.ceil(w / _PATCH_PX) * math.ceil(h / _PATCH_PX), _MAX_VISUAL_TOKENS)
        for w, h in page_sizes
    )
    return images + math.ceil(text_chars / 3)


def budget_violation(
    *, pages: int, est_input_tokens: int, max_pages: int, max_input_tokens: int
) -> str | None:
    """Reason code if the document is over budget, else None."""
    if pages > max_pages:
        return "TOO_MANY_PAGES"
    if est_input_tokens > max_input_tokens:
        return "TOO_MANY_TOKENS"
    return None


def skip_reason(doc_quality: str) -> str | None:
    """Why a document must not be sent to the model, if any. A blank page has nothing to read,
    and asking anyway only invites made-up values."""
    return "UNREADABLE_DOCUMENT" if doc_quality == "unknown" else None
