import uuid
from datetime import timedelta
from decimal import Decimal

import pytest
from cryptography.fernet import Fernet
from sqlalchemy import Engine, func, select, text, update
from sqlalchemy.orm import Session

from intake.config import Settings
from intake.db.models import Document, Invoice, LlmCall, Tenant
from intake.extract.llm import LlmOutputError, PageInput, PermanentLlmError, TransientLlmError
from intake.extract.service import (
    ExtractionFailed,
    SpendCapReached,
    cache_key,
    run_extraction,
)
from intake.security import BankVault
from tests.support.fakes import ScriptedClient, ok_payload, result

SHA = "a" * 64
VAULT = BankVault(Fernet.generate_key().decode())
PAGES = [PageInput(b"\x89PNG", "image/png")]


@pytest.fixture
def ids(engine: Engine) -> tuple[uuid.UUID, uuid.UUID]:
    with Session(engine) as s:
        tenant = Tenant(name=f"svc-{uuid.uuid4().hex[:6]}")
        s.add(tenant)
        s.flush()
        doc = Document(
            tenant_id=tenant.id, file_sha256=uuid.uuid4().hex * 2, filename="a.pdf",
            mime="application/pdf", page_count=1, storage_path="x", source="test",
            doc_quality="clean",
        )  # fmt: skip
        s.add(doc)
        s.flush()
        inv = Invoice(tenant_id=tenant.id, document_id=doc.id)
        s.add(inv)
        s.commit()
        return tenant.id, inv.id


def settings(**kw: object) -> Settings:
    base: dict[str, object] = {"daily_spend_cap_usd": Decimal("5")}
    return Settings(**{**base, **kw})  # type: ignore[arg-type]


def run(  # type: ignore[no-untyped-def]
    session: Session, client: ScriptedClient, ids: tuple[uuid.UUID, uuid.UUID], **kw: object
):
    tenant_id, invoice_id = ids
    vault = kw.pop("vault", VAULT)
    prompt = kw.pop("system_prompt", "p")
    return run_extraction(
        session, client, vault, settings(**kw), tenant_id=tenant_id, invoice_id=invoice_id,  # type: ignore[arg-type]
        file_sha256=SHA, system_prompt=prompt, pages=PAGES, text_layers=["t"],  # type: ignore[arg-type]
    )  # fmt: skip


def calls(engine: Engine, tenant_id: uuid.UUID) -> list[LlmCall]:
    with Session(engine) as s:
        return list(
            s.execute(
                select(LlmCall).where(LlmCall.tenant_id == tenant_id).order_by(LlmCall.created_at)
            ).scalars()
        )


def test_successful_call_is_logged_with_tokens_cost_and_cached_answer(
    session: Session, engine: Engine, ids: tuple[uuid.UUID, uuid.UUID]
) -> None:
    client = ScriptedClient([result(tin=10_000, tout=1_000)])
    out = run(session, client, ids)
    assert (out.cached, out.calls, out.cost_micros) == (False, 1, 30_000)
    (row,) = calls(engine, ids[0])
    assert (row.status, row.model, row.prompt_version) == ("ok", "claude-sonnet-5", "v1")
    assert (row.input_tokens, row.output_tokens, row.cost_usd_micros) == (10_000, 1_000, 30_000)
    assert row.invoice_id == ids[1]
    assert row.response is not None and row.response["po_number"]["value"] == "x"


def test_reprocessing_the_same_file_uses_the_cache_and_adds_no_llm_calls_row(
    session: Session, engine: Engine, ids: tuple[uuid.UUID, uuid.UUID]
) -> None:
    client = ScriptedClient([result()])
    first = run(session, client, ids)
    again = run(session, client, ids)  # no more scripted steps: a second call would raise
    assert again.cached and again.calls == 0 and again.cost_micros == 0
    skip = {"supplier_bank_account"}  # comes back in its normalized form, by design
    assert again.extraction.model_dump(exclude=skip) == first.extraction.model_dump(exclude=skip)
    assert len(calls(engine, ids[0])) == 1
    assert len(client.requests) == 1


def test_cache_is_keyed_by_model_and_prompt_version(
    session: Session, engine: Engine, ids: tuple[uuid.UUID, uuid.UUID]
) -> None:
    run(session, ScriptedClient([result()]), ids)
    other_model = ScriptedClient([result()], model="claude-haiku-4-5-20251001")
    assert not run(session, other_model, ids).cached
    assert not run(session, ScriptedClient([result()]), ids, extraction_prompt_version="v2").cached
    assert len(calls(engine, ids[0])) == 3


def test_schema_failure_retries_once_with_the_validation_error(
    session: Session, engine: Engine, ids: tuple[uuid.UUID, uuid.UUID]
) -> None:
    bad = ok_payload()
    del bad["total"]
    bad["po_number"] = {"value": "SECRET-DOC-TEXT", "self_confidence": "certain", "page": 1}
    client = ScriptedClient([result(bad), result()])
    out = run(session, client, ids)
    assert out.calls == 2
    assert client.requests[0].validation_error is None
    err = client.requests[1].validation_error
    assert err is not None and "total" in err and "po_number.self_confidence" in err
    assert "SECRET-DOC-TEXT" not in err  # never echo document content back or into logs
    assert [c.status for c in calls(engine, ids[0])] == ["schema_invalid", "ok"]


def test_two_schema_failures_give_up_and_count_the_cost(
    session: Session, engine: Engine, ids: tuple[uuid.UUID, uuid.UUID]
) -> None:
    bad = ok_payload()
    del bad["total"]
    client = ScriptedClient([result(bad), result(bad)])
    with pytest.raises(ExtractionFailed) as info:
        run(session, client, ids)
    assert info.value.reason == "SCHEMA_INVALID"
    rows = calls(engine, ids[0])
    assert [c.status for c in rows] == ["schema_invalid", "schema_invalid"]
    assert all(c.cost_usd_micros > 0 and c.response is None for c in rows)


def test_truncated_answer_is_retried_and_billed(
    session: Session, engine: Engine, ids: tuple[uuid.UUID, uuid.UUID]
) -> None:
    cut = LlmOutputError("answer was cut off at max_tokens", input_tokens=2000, output_tokens=8000)
    client = ScriptedClient([cut, result()])
    out = run(session, client, ids)
    assert out.calls == 2
    first = calls(engine, ids[0])[0]
    assert (first.status, first.output_tokens, first.cost_usd_micros) == (
        "output_invalid", 8000, 84_000,
    )  # fmt: skip
    assert client.requests[1].validation_error == "answer was cut off at max_tokens"


def test_spend_cap_reached_blocks_the_call_and_says_when_to_resume(
    session: Session, engine: Engine, ids: tuple[uuid.UUID, uuid.UUID]
) -> None:
    with Session(engine) as s:
        s.add(
            LlmCall(
                tenant_id=ids[0], invoice_id=ids[1], model="claude-sonnet-5", prompt_version="v1",
                input_tokens=1, output_tokens=1, cost_usd_micros=5_000_000, latency_ms=1,
                status="ok", request_hash="other",
            )
        )  # fmt: skip
        s.commit()
    client = ScriptedClient([])
    with pytest.raises(SpendCapReached) as info:
        run(session, client, ids)
    assert client.requests == []
    now = session.execute(select(func.now())).scalar_one()
    assert info.value.resume_at > now
    assert info.value.resume_at - now <= timedelta(days=1)
    assert (info.value.resume_at.hour, info.value.resume_at.minute) == (0, 0)


def test_cap_of_zero_pauses_everything(session: Session, ids: tuple[uuid.UUID, uuid.UUID]) -> None:
    with pytest.raises(SpendCapReached):
        run(session, ScriptedClient([]), ids, daily_spend_cap_usd=Decimal("0"))


def test_cap_is_rechecked_before_the_retry(
    session: Session, engine: Engine, ids: tuple[uuid.UUID, uuid.UUID]
) -> None:
    bad = ok_payload()
    del bad["total"]
    # one call of 100k input tokens costs $0.20; a $0.10 cap is passed after the first call
    client = ScriptedClient([result(bad, tin=100_000, tout=0), result()])
    with pytest.raises(SpendCapReached):
        run(session, client, ids, daily_spend_cap_usd=Decimal("0.10"))
    assert len(client.requests) == 1


def test_yesterdays_spend_does_not_count_today(
    session: Session, engine: Engine, ids: tuple[uuid.UUID, uuid.UUID]
) -> None:
    with Session(engine) as s:
        row = LlmCall(
            tenant_id=ids[0], invoice_id=ids[1], model="claude-sonnet-5", prompt_version="v1",
            input_tokens=1, output_tokens=1, cost_usd_micros=99_000_000, latency_ms=1,
            status="ok", request_hash="old",
        )  # fmt: skip
        s.add(row)
        s.commit()
        s.execute(
            update(LlmCall)
            .where(LlmCall.id == row.id)
            .values(created_at=func.now() - timedelta(days=2))
        )
        s.commit()
    assert run(session, ScriptedClient([result()]), ids).calls == 1


def test_other_tenants_spend_does_not_count(
    session: Session, engine: Engine, ids: tuple[uuid.UUID, uuid.UUID]
) -> None:
    other_tenant, other_invoice = _another(engine)
    with Session(engine) as s:
        s.add(
            LlmCall(
                tenant_id=other_tenant, invoice_id=other_invoice, model="m", prompt_version="v1",
                input_tokens=1, output_tokens=1, cost_usd_micros=99_000_000, latency_ms=1,
                status="ok", request_hash="x",
            )
        )  # fmt: skip
        s.commit()
    assert run(session, ScriptedClient([result()]), ids).calls == 1


def _another(engine: Engine) -> tuple[uuid.UUID, uuid.UUID]:
    with Session(engine) as s:
        t = Tenant(name=f"other-{uuid.uuid4().hex[:6]}")
        s.add(t)
        s.flush()
        d = Document(
            tenant_id=t.id, file_sha256=uuid.uuid4().hex * 2, filename="b.pdf",
            mime="application/pdf", page_count=1, storage_path="y", source="test",
            doc_quality="clean",
        )  # fmt: skip
        s.add(d)
        s.flush()
        i = Invoice(tenant_id=t.id, document_id=d.id)
        s.add(i)
        s.commit()
        return t.id, i.id


@pytest.mark.parametrize(
    ("error", "status"),
    [(TransientLlmError("x"), "transient_error"), (PermanentLlmError("x"), "rejected")],
)
def test_provider_errors_are_logged_and_reraised(
    session: Session,
    engine: Engine,
    ids: tuple[uuid.UUID, uuid.UUID],
    error: Exception,
    status: str,
) -> None:
    with pytest.raises(type(error)):
        run(session, ScriptedClient([error]), ids)
    (row,) = calls(engine, ids[0])
    assert (row.status, row.cost_usd_micros) == (status, 0)


def test_a_paid_answer_survives_the_callers_rollback_and_is_cached(
    session: Session, engine: Engine, ids: tuple[uuid.UUID, uuid.UUID]
) -> None:
    run(session, ScriptedClient([result()]), ids)
    session.rollback()  # e.g. a later step in the worker crashed
    with Session(engine) as fresh:
        assert run(fresh, ScriptedClient([]), ids).cached


def test_a_cached_answer_that_no_longer_fits_the_schema_is_ignored(
    session: Session, engine: Engine, ids: tuple[uuid.UUID, uuid.UUID]
) -> None:
    run(session, ScriptedClient([result()]), ids)
    with engine.begin() as conn:
        conn.execute(text("update llm_calls set response = '{\"old\": true}'::jsonb"))
    client = ScriptedClient([result()])
    assert not run(session, client, ids).cached
    assert len(client.requests) == 1


def test_bank_account_is_sealed_in_the_cache_and_restored_on_a_hit(
    session: Session, engine: Engine, ids: tuple[uuid.UUID, uuid.UUID]
) -> None:
    payload = ok_payload()
    payload["supplier_bank_account"] = {
        "value": "DE20 3554 6670 1194 6383 4401", "self_confidence": "high", "page": 1,
    }  # fmt: skip
    run(session, ScriptedClient([result(payload)]), ids)
    stored = calls(engine, ids[0])[0].response
    assert stored is not None
    sealed = stored["supplier_bank_account"]["value"]
    assert sealed.startswith("enc:") and "3554" not in sealed  # no plaintext account at rest
    assert stored["invoice_number"]["value"] == "x"  # everything else is stored as is
    hit = run(session, ScriptedClient([]), ids)
    assert hit.cached
    assert hit.extraction.supplier_bank_account.value == "DE20355466701194638344" + "01"


def test_a_missing_bank_account_stays_null_in_the_cache(
    session: Session, engine: Engine, ids: tuple[uuid.UUID, uuid.UUID]
) -> None:
    payload = ok_payload()
    payload["supplier_bank_account"] = {"value": None, "self_confidence": "low", "page": None}
    run(session, ScriptedClient([result(payload)]), ids)
    stored = calls(engine, ids[0])[0].response
    assert stored is not None and stored["supplier_bank_account"]["value"] is None
    assert run(session, ScriptedClient([]), ids).extraction.supplier_bank_account.value is None


def test_a_rotated_bank_key_falls_back_to_a_fresh_call(
    session: Session, engine: Engine, ids: tuple[uuid.UUID, uuid.UUID]
) -> None:
    payload = ok_payload()
    payload["supplier_bank_account"] = {"value": "DE20 3554", "self_confidence": "high", "page": 1}
    run(session, ScriptedClient([result(payload)]), ids)
    rotated = BankVault(Fernet.generate_key().decode())
    client = ScriptedClient([result()])
    out = run(session, client, ids, vault=rotated)  # the old sealed answer can't be opened
    assert not out.cached and len(client.requests) == 1


def test_a_nul_character_in_the_answer_is_retried_not_stored(
    session: Session, engine: Engine, ids: tuple[uuid.UUID, uuid.UUID]
) -> None:
    bad = ok_payload()
    bad["supplier_name"] = {"value": "Evil\x00Corp", "self_confidence": "high", "page": 1}
    client = ScriptedClient([result(bad), result()])
    out = run(session, client, ids)
    assert out.calls == 2
    assert [c.status for c in calls(engine, ids[0])] == ["schema_invalid", "ok"]
    assert "NUL" in (client.requests[1].validation_error or "")


def test_a_page_number_beyond_the_document_triggers_the_retry(
    session: Session, engine: Engine, ids: tuple[uuid.UUID, uuid.UUID]
) -> None:
    bad = ok_payload()
    bad["total"] = {"value": "9", "self_confidence": "high", "page": 5}  # the document has 1 page
    client = ScriptedClient([result(bad), result()])
    run(session, client, ids)
    err = client.requests[1].validation_error or ""
    assert "page" in err and "total" in err and "1" in err


def test_editing_the_prompt_text_changes_the_cache_key(
    session: Session, engine: Engine, ids: tuple[uuid.UUID, uuid.UUID]
) -> None:
    run(session, ScriptedClient([result()]), ids)
    edited = ScriptedClient([result()])
    assert not run(session, edited, ids, system_prompt="p, edited in place").cached
    assert cache_key(SHA, "m", "v1", "a") != cache_key(SHA, "m", "v1", "b")
    assert cache_key(SHA, "m", "v1", "a") == cache_key(SHA, "m", "v1", "a")
