from datetime import UTC, datetime

import pytest

from intake.core.audit_timeline import (
    AuditEventFacts,
    LlmCallFacts,
    build_timeline,
    format_usd_micros,
    render_event,
    render_llm_call,
)

T0 = datetime(2026, 1, 1, tzinfo=UTC)


def ev(event_type: str, data: dict[str, object], **kw: object) -> AuditEventFacts:
    base: dict[str, object] = {
        "id": "1",
        "actor_type": "system",
        "actor_id": "worker",
        "created_at": T0,
    }
    return AuditEventFacts(event_type=event_type, data=data, **{**base, **kw})  # type: ignore[arg-type]


@pytest.mark.parametrize(
    ("micros", "text"),
    [(0, "$0.0000"), (3_100, "$0.0031"), (1_000_000, "$1.0000"), (-2_500_000, "$-2.5000")],
)
def test_format_usd_micros(micros: int, text: str) -> None:
    assert format_usd_micros(micros) == text


def test_status_changed_with_reason() -> None:
    e = ev("status_changed", {"from": "extracted", "to": "checking", "reason": "re-check"})
    assert render_event(e).summary == "Status changed from extracted to checking (re-check)."


def test_status_changed_from_nothing_and_no_reason() -> None:
    e = ev("status_changed", {"from": None, "to": "received"})
    assert render_event(e).summary == "Status changed from nothing to received."


def test_extraction_failed() -> None:
    e = ev("extraction_failed", {"reason": "UNREADABLE_DOCUMENT"})
    assert render_event(e).summary == "Extraction failed: UNREADABLE_DOCUMENT."


def test_exception_raised_unchecked() -> None:
    e = ev("exception_raised", {"code": "NO_PO", "severity": "review", "unchecked": True})
    assert render_event(e).summary == "Exception raised: NO_PO (review) (could not be checked)."


def test_exception_raised_checked() -> None:
    e = ev("exception_raised", {"code": "TOTAL_MISMATCH", "severity": "review", "unchecked": False})
    assert render_event(e).summary == "Exception raised: TOTAL_MISMATCH (review)."


def test_exception_closed() -> None:
    e = ev("exception_closed", {"code": "NO_PO", "resolution": "resolved"})
    assert render_event(e).summary == "Exception NO_PO resolved."


def test_field_corrected() -> None:
    e = ev("field_corrected", {"field": "total"})
    assert render_event(e).summary == "Field corrected: total."


def test_document_received() -> None:
    e = ev(
        "document_received",
        {
            "document_id": "d1",
            "sha256": "abc",
            "mime": "application/pdf",
            "size": 100,
            "pages": 2,
            "filename": "invoice.pdf",
            "source": "web",
        },
    )
    assert render_event(e).summary == "Document received: invoice.pdf (2 page(s), application/pdf)."


def test_duplicate_file_upload() -> None:
    e = ev("duplicate_file_upload", {"document_id": "d1", "filename": "a.pdf", "source": "web"})
    assert render_event(e).summary == "The same file was uploaded again: a.pdf."


def test_document_processed() -> None:
    e = ev(
        "document_processed",
        {"document_id": "d1", "pages": 3, "doc_quality": "scanned", "text_chars": 500},
    )
    assert render_event(e).summary == "Document processed: scanned quality, 3 page(s)."


def test_extraction_completed_cached_and_weak_fields() -> None:
    e = ev(
        "extraction_completed",
        {
            "prompt_version": "v1",
            "cached": True,
            "model_calls": 0,
            "cost_usd_micros": 3_100,
            "fields": 14,
            "lines": 3,
            "low_confidence_critical_fields": ["total", "invoice_date"],
        },
    )
    assert render_event(e).summary == (
        "Extraction completed (cached): 14 field(s), 3 line(s), cost $0.0031; "
        "low confidence: total, invoice_date."
    )


def test_extraction_completed_no_weak_fields_not_cached() -> None:
    e = ev(
        "extraction_completed",
        {
            "prompt_version": "v1",
            "cached": False,
            "model_calls": 1,
            "cost_usd_micros": 0,
            "fields": 14,
            "lines": 3,
            "low_confidence_critical_fields": [],
        },
    )
    assert render_event(e).summary == "Extraction completed: 14 field(s), 3 line(s), cost $0.0000."


def test_extraction_paused() -> None:
    e = ev("extraction_paused", {"reason": "SPEND_CAP_REACHED", "resume_at": "2026-01-02T00:00:00"})
    assert render_event(e).summary == (
        "Extraction paused (SPEND_CAP_REACHED); resuming at 2026-01-02T00:00:00."
    )


def test_checks_completed() -> None:
    e = ev(
        "checks_completed",
        {
            "rule_version": 1,
            "passed": 5,
            "failed": ["TOTAL_MISMATCH"],
            "skipped": [],
            "supplier_matched_by": "tax_id",
            "as_of": "2026-01-01",
        },
    )
    assert render_event(e).summary == (
        "Checks completed: 5 passed, 1 failed (TOTAL_MISMATCH), 0 skipped (none)."
    )


def test_duplicate_check_completed_with_kind() -> None:
    e = ev(
        "duplicate_check_completed",
        {
            "rule_version": 1,
            "outcome": "possible_duplicate",
            "kind": "exact_amount",
            "existing_invoice_id": "inv-1",
            "pending_earlier": False,
        },
    )
    assert render_event(e).summary == "Duplicate check: possible_duplicate (exact_amount)."


def test_duplicate_check_completed_clean() -> None:
    e = ev(
        "duplicate_check_completed",
        {
            "rule_version": 1,
            "outcome": "clean",
            "kind": None,
            "existing_invoice_id": None,
            "pending_earlier": False,
        },
    )
    assert render_event(e).summary == "Duplicate check: clean."


def test_match_completed_with_po() -> None:
    e = ev(
        "match_completed",
        {
            "rule_version": 1,
            "po_id": "po-1",
            "inferred": True,
            "outcomes": {"PRICE_VARIANCE": "fail"},
            "line_matches": [[1, "sku"], [2, "sku"]],
        },
    )
    assert render_event(e).summary == "Matched against PO po-1 (inferred): 2 line(s) matched."


def test_match_completed_no_po() -> None:
    e = ev(
        "match_completed",
        {"rule_version": 1, "po_id": None, "inferred": False, "outcomes": {}, "line_matches": []},
    )
    assert render_event(e).summary == "Matched against no PO: 0 line(s) matched."


def test_routing_decided_with_reasons() -> None:
    e = ev(
        "routing_decided",
        {
            "status": "needs_review",
            "route": "review",
            "reasons": ["OPEN_EXCEPTIONS"],
            "exceptions": ["TOTAL_MISMATCH"],
        },
    )
    assert render_event(e).summary == "Routed to review (needs_review): OPEN_EXCEPTIONS."


def test_routing_decided_straight_through() -> None:
    e = ev(
        "routing_decided",
        {"status": "cleared", "route": "straight_through", "reasons": [], "exceptions": []},
    )
    assert render_event(e).summary == "Routed to straight_through (cleared)."


def test_info_requested() -> None:
    assert render_event(ev("info_requested", {})).summary == (
        "Requested more information from the supplier or requester."
    )


def test_bank_details_revealed() -> None:
    assert render_event(ev("bank_details_revealed", {})).summary == (
        "Bank account details were revealed to a reviewer."
    )


def test_bank_details_reveal_failed() -> None:
    assert render_event(ev("bank_details_reveal_failed", {})).summary == (
        "A reviewer's attempt to reveal the bank account failed."
    )


def test_login_failed() -> None:
    assert render_event(ev("login_failed", {})).summary == "A sign-in attempt failed."


def test_login_succeeded() -> None:
    assert render_event(ev("login_succeeded", {})).summary == "Signed in."


def test_job_failed() -> None:
    e = ev("job_failed", {"job_id": "j1", "type": "extract", "attempt": 3, "error": "boom"})
    assert render_event(e).summary == "Background job extract failed after 3 attempt(s)."


def test_job_retry_scheduled() -> None:
    e = ev("job_retry_scheduled", {"job_id": "j1", "type": "extract", "attempt": 2, "error": "x"})
    assert render_event(e).summary == "Background job extract will retry (attempt 2)."


def test_job_requeued_after_lost_worker() -> None:
    e = ev("job_requeued_after_lost_worker", {"job_id": "j1", "type": "match", "attempt": 1})
    assert render_event(e).summary == "Background job match was requeued after its worker was lost."


def test_unknown_event_type_renders_generically_instead_of_failing() -> None:
    e = ev("some_new_thing_added_elsewhere", {"whatever": "shape"})
    assert render_event(e).summary == "Some new thing added elsewhere."


def test_known_event_type_with_unexpected_payload_shape_falls_back_generically() -> None:
    e = ev("status_changed", {"nothing": "like the real shape"})
    assert render_event(e).summary == "Status changed."


def test_render_event_keeps_actor_and_raw_detail() -> None:
    e = ev("field_corrected", {"field": "total"}, actor_type="user", actor_id="reviewer1", id="42")
    entry = render_event(e)
    assert entry.id == "event:42"
    assert entry.at == T0
    assert entry.actor_type == "user"
    assert entry.actor_id == "reviewer1"
    assert entry.detail == {"event_type": "field_corrected", "field": "total"}


def test_render_llm_call() -> None:
    c = LlmCallFacts(
        id="call-1",
        model="claude-haiku-4-5-20251001",
        prompt_version="v1",
        input_tokens=1200,
        output_tokens=300,
        cost_usd_micros=3_100,
        latency_ms=850,
        status="ok",
        created_at=T0,
    )
    entry = render_llm_call(c)
    assert entry.id == "llm_call:call-1"
    assert entry.actor_type == "agent"
    assert entry.actor_id == "claude-haiku-4-5-20251001"
    assert entry.summary == (
        "Model call to claude-haiku-4-5-20251001 (ok): 1200 in / 300 out tokens, $0.0031, 850 ms."
    )
    assert entry.detail["status"] == "ok"


def test_build_timeline_sorts_events_and_llm_calls_together_oldest_first() -> None:
    t1 = datetime(2026, 1, 1, tzinfo=UTC)
    t2 = datetime(2026, 1, 2, tzinfo=UTC)
    t3 = datetime(2026, 1, 3, tzinfo=UTC)
    events = [
        ev("status_changed", {"from": None, "to": "received"}, id="1", created_at=t1),
        ev("status_changed", {"from": "received", "to": "extracting"}, id="3", created_at=t3),
    ]
    calls = [
        LlmCallFacts(
            id="c1",
            model="m",
            prompt_version="v1",
            input_tokens=1,
            output_tokens=1,
            cost_usd_micros=0,
            latency_ms=1,
            status="ok",
            created_at=t2,
        )
    ]
    timeline = build_timeline(events, calls)
    assert [e.id for e in timeline] == ["event:1", "llm_call:c1", "event:3"]


def test_build_timeline_breaks_ties_by_stable_id() -> None:
    events = [
        ev("status_changed", {"from": "b", "to": "c"}, id="2", created_at=T0),
        ev("status_changed", {"from": "a", "to": "b"}, id="1", created_at=T0),
    ]
    timeline = build_timeline(events)
    assert [e.id for e in timeline] == ["event:1", "event:2"]


def test_build_timeline_empty() -> None:
    assert build_timeline([]) == []
