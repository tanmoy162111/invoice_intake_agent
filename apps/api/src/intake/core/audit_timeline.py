"""Audit timeline (playbook M9): every invoice's `audit_events` and `llm_calls` rows, turned into
a plain-language history. Pure: rows already read from the DB go in, rendered entries come out.

One renderer per known `event_type`, mirroring the exception taxonomy in `core/exceptions.py`.
An event whose type isn't in the table below still renders, with a generic sentence, instead of
being dropped or raising: the timeline must always be complete, even for a code added elsewhere
and not yet given a template here (mirror any new one in `docs/report.md`).
"""

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any


@dataclass(frozen=True)
class AuditEventFacts:
    """What `render_event` needs from one `audit_events` row."""

    id: str
    actor_type: str
    actor_id: str | None
    event_type: str
    data: dict[str, Any]
    created_at: datetime


@dataclass(frozen=True)
class LlmCallFacts:
    """What `render_llm_call` needs from one `llm_calls` row."""

    id: str
    model: str
    prompt_version: str
    input_tokens: int
    output_tokens: int
    cost_usd_micros: int
    latency_ms: int
    status: str
    created_at: datetime


@dataclass(frozen=True)
class TimelineEntry:
    id: str  # stable across renders: "event:<id>" or "llm_call:<id>"
    at: datetime
    actor_type: str
    actor_id: str | None
    summary: str  # plain language, one line
    detail: dict[str, Any]  # the raw fields, for "expand to see raw details"


def format_usd_micros(micros: int) -> str:
    """Render whole-USD-millionths as dollars to four decimal places, e.g. 3_100 -> '$0.0031'.

    Integer arithmetic only, per the repo's money rule; this is a display string for a cost
    metric, never a stored or compared amount.
    """
    sign = "-" if micros < 0 else ""
    dollars, frac = divmod(abs(micros), 1_000_000)
    return f"${sign}{dollars}.{frac // 100:04d}"


def _join(items: Sequence[str]) -> str:
    return ", ".join(items) if items else "none"


def _status_changed(d: dict[str, Any]) -> str:
    reason = f" ({d['reason']})" if d.get("reason") else ""
    frm = d.get("from") or "nothing"
    return f"Status changed from {frm} to {d['to']}{reason}."


def _extraction_failed(d: dict[str, Any]) -> str:
    return f"Extraction failed: {d['reason']}."


def _exception_raised(d: dict[str, Any]) -> str:
    tag = " (could not be checked)" if d.get("unchecked") else ""
    return f"Exception raised: {d['code']} ({d['severity']}){tag}."


def _exception_closed(d: dict[str, Any]) -> str:
    return f"Exception {d['code']} {d['resolution']}."


def _field_corrected(d: dict[str, Any]) -> str:
    return f"Field corrected: {d['field']}."


def _document_received(d: dict[str, Any]) -> str:
    return f"Document received: {d['filename']} ({d['pages']} page(s), {d['mime']})."


def _duplicate_file_upload(d: dict[str, Any]) -> str:
    return f"The same file was uploaded again: {d['filename']}."


def _document_processed(d: dict[str, Any]) -> str:
    return f"Document processed: {d['doc_quality']} quality, {d['pages']} page(s)."


def _extraction_completed(d: dict[str, Any]) -> str:
    cached = " (cached)" if d.get("cached") else ""
    weak = d.get("low_confidence_critical_fields") or []
    weak_note = f"; low confidence: {_join(weak)}" if weak else ""
    cost = format_usd_micros(d["cost_usd_micros"])
    return (
        f"Extraction completed{cached}: {d['fields']} field(s), {d['lines']} line(s), "
        f"cost {cost}{weak_note}."
    )


def _extraction_paused(d: dict[str, Any]) -> str:
    return f"Extraction paused ({d['reason']}); resuming at {d['resume_at']}."


def _checks_completed(d: dict[str, Any]) -> str:
    failed = d.get("failed") or []
    skipped = d.get("skipped") or []
    return (
        f"Checks completed: {d['passed']} passed, {len(failed)} failed ({_join(failed)}), "
        f"{len(skipped)} skipped ({_join(skipped)})."
    )


def _duplicate_check_completed(d: dict[str, Any]) -> str:
    kind = f" ({d['kind']})" if d.get("kind") else ""
    return f"Duplicate check: {d['outcome']}{kind}."


def _match_completed(d: dict[str, Any]) -> str:
    matched = len(d.get("line_matches") or [])
    po = f"PO {d['po_id']}" if d.get("po_id") else "no PO"
    inferred = " (inferred)" if d.get("inferred") else ""
    return f"Matched against {po}{inferred}: {matched} line(s) matched."


def _routing_decided(d: dict[str, Any]) -> str:
    reasons = d.get("reasons") or []
    tail = f": {_join(reasons)}" if reasons else ""
    return f"Routed to {d['route']} ({d['status']}){tail}."


def _info_requested(_: dict[str, Any]) -> str:
    return "Requested more information from the supplier or requester."


def _bank_details_revealed(_: dict[str, Any]) -> str:
    return "Bank account details were revealed to a reviewer."


def _bank_details_reveal_failed(_: dict[str, Any]) -> str:
    return "A reviewer's attempt to reveal the bank account failed."


def _login_failed(_: dict[str, Any]) -> str:
    return "A sign-in attempt failed."


def _login_succeeded(_: dict[str, Any]) -> str:
    return "Signed in."


def _job_failed(d: dict[str, Any]) -> str:
    return f"Background job {d['type']} failed after {d['attempt']} attempt(s)."


def _job_retry_scheduled(d: dict[str, Any]) -> str:
    return f"Background job {d['type']} will retry (attempt {d['attempt']})."


def _job_requeued_after_lost_worker(d: dict[str, Any]) -> str:
    return f"Background job {d['type']} was requeued after its worker was lost."


_RENDERERS: dict[str, Callable[[dict[str, Any]], str]] = {
    "status_changed": _status_changed,
    "extraction_failed": _extraction_failed,
    "exception_raised": _exception_raised,
    "exception_closed": _exception_closed,
    "field_corrected": _field_corrected,
    "document_received": _document_received,
    "duplicate_file_upload": _duplicate_file_upload,
    "document_processed": _document_processed,
    "extraction_completed": _extraction_completed,
    "extraction_paused": _extraction_paused,
    "checks_completed": _checks_completed,
    "duplicate_check_completed": _duplicate_check_completed,
    "match_completed": _match_completed,
    "routing_decided": _routing_decided,
    "info_requested": _info_requested,
    "bank_details_revealed": _bank_details_revealed,
    "bank_details_reveal_failed": _bank_details_reveal_failed,
    "login_failed": _login_failed,
    "login_succeeded": _login_succeeded,
    "job_failed": _job_failed,
    "job_retry_scheduled": _job_retry_scheduled,
    "job_requeued_after_lost_worker": _job_requeued_after_lost_worker,
}


def _generic(event_type: str) -> str:
    return f"{event_type.replace('_', ' ').capitalize()}."


def render_event(e: AuditEventFacts) -> TimelineEntry:
    renderer = _RENDERERS.get(e.event_type)
    try:
        summary = renderer(e.data) if renderer else _generic(e.event_type)
    except (KeyError, TypeError):  # a payload shape the template doesn't expect: never crash
        summary = _generic(e.event_type)
    return TimelineEntry(
        id=f"event:{e.id}",
        at=e.created_at,
        actor_type=e.actor_type,
        actor_id=e.actor_id,
        summary=summary,
        detail={"event_type": e.event_type, **e.data},
    )


def render_llm_call(c: LlmCallFacts) -> TimelineEntry:
    summary = (
        f"Model call to {c.model} ({c.status}): {c.input_tokens} in / {c.output_tokens} out "
        f"tokens, {format_usd_micros(c.cost_usd_micros)}, {c.latency_ms} ms."
    )
    return TimelineEntry(
        id=f"llm_call:{c.id}",
        at=c.created_at,
        actor_type="agent",
        actor_id=c.model,
        summary=summary,
        detail={
            "model": c.model,
            "prompt_version": c.prompt_version,
            "input_tokens": c.input_tokens,
            "output_tokens": c.output_tokens,
            "cost_usd_micros": c.cost_usd_micros,
            "latency_ms": c.latency_ms,
            "status": c.status,
        },
    )


def build_timeline(
    events: Sequence[AuditEventFacts], llm_calls: Sequence[LlmCallFacts] = ()
) -> list[TimelineEntry]:
    """Every event and model call for one invoice, oldest first.

    Ties (same timestamp) are broken by the stable `id`, so the order never depends on how the
    caller happened to fetch the rows.
    """
    entries = [render_event(e) for e in events] + [render_llm_call(c) for c in llm_calls]
    return sorted(entries, key=lambda t: (t.at, t.id))
