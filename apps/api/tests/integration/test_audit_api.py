"""M9 over HTTP: `/invoices/{id}/audit` returns the invoice's whole history, built on the same
seeded pipeline run as the M8a review API tests (see `test_review_api.py`)."""

import uuid
from typing import Any

from sqlalchemy import text

from tests.integration.test_review_api import TRUTHS, Env, detail, env, truth_with

__all__ = ["env"]  # re-exported fixture; a bare import is dropped by some linters


def audit(env: Env, invoice_id: str) -> dict[str, Any]:
    r = env.get(f"/invoices/{invoice_id}/audit")
    assert r.status_code == 200, r.text
    return r.json()  # type: ignore[no-any-return]


def raw_row_count(env: Env, invoice_id: str) -> int:
    with env.engine.begin() as conn:
        events = conn.execute(
            text("select count(*) from audit_events where invoice_id = :i"), {"i": invoice_id}
        ).scalar_one()
        calls = conn.execute(
            text("select count(*) from llm_calls where invoice_id = :i"), {"i": invoice_id}
        ).scalar_one()
    return int(events) + int(calls)


def test_the_audit_endpoint_needs_a_token(env: Env) -> None:
    iid = env.id_of("inv-001")
    assert env.client.get(f"/invoices/{iid}/audit").status_code == 401


def test_unknown_and_malformed_invoice_ids(env: Env) -> None:
    assert env.get(f"/invoices/{uuid.uuid4()}/audit").status_code == 404
    assert env.get("/invoices/not-a-uuid/audit").status_code == 422


def test_every_row_in_audit_events_and_llm_calls_for_the_invoice_is_in_the_timeline(
    env: Env,
) -> None:
    tid = next(t["id"] for t in TRUTHS if t["expected"]["should_clear"])
    iid = env.id_of(tid)
    body = audit(env, iid)
    assert body["invoice_id"] == iid
    assert len(body["entries"]) == raw_row_count(env, iid) > 0


def test_the_timeline_is_oldest_first_and_covers_the_whole_pipeline(env: Env) -> None:
    tid = next(t["id"] for t in TRUTHS if t["expected"]["should_clear"])
    entries = audit(env, env.id_of(tid))["entries"]
    at = [e["at"] for e in entries]
    assert at == sorted(at)
    seen = {e["detail"].get("event_type") for e in entries if "event_type" in e["detail"]}
    assert {
        "document_received", "document_processed", "extraction_completed", "checks_completed",
        "match_completed", "duplicate_check_completed", "routing_decided",
    } <= seen  # fmt: skip
    first = next(e for e in entries if e["detail"].get("event_type") == "document_received")
    assert first["actor_type"] == "system"
    model_call = next((e for e in entries if e["id"].startswith("llm_call:")), None)
    assert model_call is not None and model_call["actor_type"] == "agent"


def test_a_reviewer_correction_shows_up_as_a_user_action_in_the_timeline(env: Env) -> None:
    tid = truth_with("LINE_MATH_MISMATCH")
    iid = env.id_of(tid)
    r = env.post(f"/invoices/{iid}/corrections", {"field": "payment_terms", "value": "Net 45"})
    assert r.status_code == 200, r.text
    entries = audit(env, iid)["entries"]
    corrected = next(e for e in entries if e["detail"].get("event_type") == "field_corrected")
    assert corrected["actor_type"] == "user" and corrected["actor_id"] == "reviewer"
    assert corrected["detail"]["field"] == "payment_terms"
    assert "payment_terms" in corrected["summary"]


def test_the_openapi_schema_lists_the_audit_route(env: Env) -> None:
    paths = env.client.get("/openapi.json", headers=env.service).json()["paths"]
    assert "/invoices/{invoice_id}/audit" in paths


def test_bank_details_are_never_in_the_audit_detail(env: Env) -> None:
    tid = truth_with("BANK_DETAILS_CHANGED")
    iid = env.id_of(tid)
    assert env.post(f"/invoices/{iid}/bank/reveal").status_code == 200
    blob = str(audit(env, iid))
    account = detail(env, tid)["bank"]["masked"]
    assert account not in blob or account == "••••"  # never the real account, only that it happened
    entries = audit(env, iid)["entries"]
    revealed = next(
        e for e in entries if e["detail"].get("event_type") == "bank_details_revealed"
    )  # fmt: skip
    assert revealed["actor_type"] == "user" and revealed["detail"] == {
        "event_type": "bank_details_revealed"
    }
