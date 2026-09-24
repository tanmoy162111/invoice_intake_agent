from datetime import UTC, datetime

import pytest

from intake.core.llm_budget import (
    PRICES,
    ModelPrice,
    UnknownModelError,
    budget_violation,
    cap_reached,
    cost_usd_micros,
    estimate_input_tokens,
    next_utc_midnight,
    price_for,
    request_hash,
)


def test_sonnet_5_price_matches_docs() -> None:
    # $2 / $10 per million tokens (platform.claude.com models overview, checked 2026-09-25)
    assert PRICES["claude-sonnet-5"] == ModelPrice(2_000_000, 10_000_000)


def test_cost_is_integer_micros() -> None:
    price = price_for("claude-sonnet-5")
    # 10,000 in x $2/MTok = $0.02 = 20,000 micros; 1,000 out x $10/MTok = $0.01 = 10,000 micros
    assert cost_usd_micros(10_000, 1_000, price) == 30_000


def test_cost_rounds_up_never_under_counts() -> None:
    price = ModelPrice(input_micros_per_mtok=1, output_micros_per_mtok=1)
    assert cost_usd_micros(1, 0, price) == 1
    assert cost_usd_micros(0, 0, price) == 0


def test_free_price_costs_nothing() -> None:
    assert cost_usd_micros(1_000_000, 1_000_000, ModelPrice(0, 0)) == 0


def test_negative_tokens_rejected() -> None:
    with pytest.raises(ValueError):
        cost_usd_micros(-1, 0, ModelPrice(1, 1))


def test_unknown_model_is_refused_not_guessed() -> None:
    with pytest.raises(UnknownModelError):
        price_for("claude-imaginary-9")


def test_request_hash_is_stable_and_sensitive_to_each_part() -> None:
    base = request_hash("a" * 64, "claude-sonnet-5", "v1")
    assert base == request_hash("a" * 64, "claude-sonnet-5", "v1")
    assert base != request_hash("b" * 64, "claude-sonnet-5", "v1")
    assert base != request_hash("a" * 64, "claude-opus-5-5", "v1")
    assert base != request_hash("a" * 64, "claude-sonnet-5", "v2")
    assert len(base) == 64


def test_request_hash_parts_cannot_collide_by_shifting_characters() -> None:
    assert request_hash("ab", "c", "v1") != request_hash("a", "bc", "v1")


def test_cap_not_reached_below() -> None:
    assert not cap_reached(4_999_999, 5_000_000)


def test_cap_reached_at_and_above() -> None:
    assert cap_reached(5_000_000, 5_000_000)
    assert cap_reached(6_000_000, 5_000_000)


def test_cap_none_means_no_cap() -> None:
    assert not cap_reached(10**12, None)


def test_next_utc_midnight() -> None:
    now = datetime(2026, 9, 25, 23, 59, 30, tzinfo=UTC)
    assert next_utc_midnight(now) == datetime(2026, 9, 26, 0, 0, tzinfo=UTC)
    assert next_utc_midnight(datetime(2026, 12, 31, 5, tzinfo=UTC)) == datetime(
        2027, 1, 1, tzinfo=UTC
    )


def test_next_utc_midnight_requires_timezone() -> None:
    with pytest.raises(ValueError):
        next_utc_midnight(datetime(2026, 9, 25, 12))


def test_estimate_input_tokens_uses_28px_patches_per_page() -> None:
    # 280 x 560 px = 10 x 20 patches = 200 tokens
    assert estimate_input_tokens([(280, 560)], text_chars=0) == 200


def test_estimate_input_tokens_caps_each_page() -> None:
    assert estimate_input_tokens([(10_000, 10_000)], text_chars=0) == 4784


def test_estimate_input_tokens_adds_text_conservatively() -> None:
    assert estimate_input_tokens([], text_chars=3000) == 1000
    assert estimate_input_tokens([], text_chars=1) == 1


def test_budget_ok() -> None:
    assert (
        budget_violation(pages=2, est_input_tokens=9_000, max_pages=10, max_input_tokens=50_000)
        is None
    )


def test_budget_too_many_pages() -> None:
    assert (
        budget_violation(pages=11, est_input_tokens=1, max_pages=10, max_input_tokens=50_000)
        == "TOO_MANY_PAGES"
    )


def test_budget_too_many_tokens() -> None:
    assert (
        budget_violation(pages=1, est_input_tokens=50_001, max_pages=10, max_input_tokens=50_000)
        == "TOO_MANY_TOKENS"
    )
