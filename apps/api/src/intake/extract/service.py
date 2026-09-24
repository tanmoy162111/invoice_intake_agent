"""Model orchestration for one document: cache, daily spend cap, one schema retry, call logging.

Every model call is logged in `llm_calls` in its own short transaction, so a call that was paid for
is recorded (and cached) even if the invoice's own transaction later rolls back. A cache hit makes
no call and writes no row. The cache key is (file sha256, model, prompt version).
"""

import hashlib
import json
import uuid
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any

from cryptography.fernet import InvalidToken
from pydantic import ValidationError
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from intake.config import Settings
from intake.core.llm_budget import cap_reached, next_utc_midnight, request_hash
from intake.db.models import LlmCall
from intake.extract.llm import (
    LlmClient,
    LlmOutputError,
    LlmRequest,
    LlmResult,
    PageInput,
    PermanentLlmError,
    TransientLlmError,
)
from intake.extract.schema import InvoiceExtraction, pages_out_of_range, tool_input_schema
from intake.security import BankVault

MAX_ATTEMPTS = 2  # the first call, plus one retry that includes the validation error


class SpendCapReached(Exception):
    def __init__(self, resume_at: datetime) -> None:
        super().__init__("daily spend cap reached")
        self.resume_at = resume_at


class ExtractionFailed(Exception):
    """The model never produced a valid answer. `reason` is a code, never document content."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class Extracted:
    extraction: InvoiceExtraction
    cached: bool
    calls: int
    cost_micros: int


def cache_key(file_sha256: str, model: str, prompt_version: str, system_prompt: str) -> str:
    """The cache key: (file, model, prompt version) plus a fingerprint of the prompt text and the
    answer schema, so editing either in place can never reuse a stale answer."""
    body = system_prompt + "\n" + json.dumps(tool_input_schema(), sort_keys=True)
    fingerprint = hashlib.sha256(body.encode()).hexdigest()[:16]
    return request_hash(file_sha256, model, f"{prompt_version}:{fingerprint}")


def db_now(session: Session) -> datetime:
    return session.execute(select(func.now())).scalar_one()


def spend_today_micros(session: Session, tenant_id: uuid.UUID) -> tuple[int, datetime]:
    """Model spend since 00:00 UTC today, and the database's current time."""
    now = db_now(session)
    day_start = next_utc_midnight(now) - timedelta(days=1)
    spent = session.execute(
        select(func.coalesce(func.sum(LlmCall.cost_usd_micros), 0)).where(
            LlmCall.tenant_id == tenant_id, LlmCall.created_at >= day_start
        )
    ).scalar_one()
    return int(spent), now


def _summarize(exc: ValidationError) -> str:
    """Field paths and rules only; never the offending values (they may be document content)."""
    errs = exc.errors(include_input=False, include_url=False, include_context=False)
    return "; ".join(f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in errs)[:1000]


_SEALED = "enc:"


def _seal(dump: dict[str, Any], vault: BankVault) -> dict[str, Any]:
    """The cache must not hold a bank account in the clear (playbook §10): encrypt it."""
    value = dump["supplier_bank_account"]["value"]
    if value:
        dump["supplier_bank_account"]["value"] = _SEALED + vault.encrypt(value)
    return dump


def _unseal(data: dict[str, Any], vault: BankVault) -> dict[str, Any]:
    value = data.get("supplier_bank_account", {}).get("value")
    if isinstance(value, str) and value.startswith(_SEALED):
        data["supplier_bank_account"]["value"] = vault.decrypt(value[len(_SEALED) :])
    return data


def _cached(
    session: Session, tenant_id: uuid.UUID, key: str, vault: BankVault
) -> InvoiceExtraction | None:
    row = session.execute(
        select(LlmCall.response)
        .where(
            LlmCall.tenant_id == tenant_id,
            LlmCall.request_hash == key,
            LlmCall.status == "ok",
            LlmCall.response.is_not(None),
        )
        .order_by(LlmCall.created_at.desc())
        .limit(1)
    ).scalar_one_or_none()
    if row is None:
        return None
    try:
        return InvoiceExtraction.model_validate(_unseal(dict(row), vault))
    except (ValidationError, KeyError, ValueError, TypeError, InvalidToken):
        # the schema changed, or the bank key was rotated: the old answer is unusable, ask again
        return None


def _log_call(
    session: Session,
    *,
    tenant_id: uuid.UUID,
    invoice_id: uuid.UUID,
    client: LlmClient,
    prompt_version: str,
    key: str,
    status: str,
    input_tokens: int = 0,
    output_tokens: int = 0,
    latency_ms: int = 0,
    response: dict[str, Any] | None = None,
) -> int:
    """Write one llm_calls row in its own transaction. Returns its cost in micros."""
    cost = client.cost_micros(input_tokens, output_tokens)
    with Session(session.get_bind()) as own:
        own.add(
            LlmCall(
                tenant_id=tenant_id, invoice_id=invoice_id, model=client.model,
                prompt_version=prompt_version, input_tokens=input_tokens,
                output_tokens=output_tokens, cost_usd_micros=cost, latency_ms=latency_ms,
                status=status, request_hash=key, response=response,
            )
        )  # fmt: skip
        own.commit()
    return cost


def run_extraction(
    session: Session,
    client: LlmClient,
    vault: BankVault,
    settings: Settings,
    *,
    tenant_id: uuid.UUID,
    invoice_id: uuid.UUID,
    file_sha256: str,
    system_prompt: str,
    pages: list[PageInput],
    text_layers: list[str],
) -> Extracted:
    prompt_version = settings.extraction_prompt_version
    key = cache_key(file_sha256, client.model, prompt_version, system_prompt)
    hit = _cached(session, tenant_id, key, vault)
    if hit is not None:
        return Extracted(hit, cached=True, calls=0, cost_micros=0)

    request = LlmRequest(
        system=system_prompt, pages=pages, text_layers=text_layers, schema=tool_input_schema(),
        max_output_tokens=settings.extract_max_output_tokens, key=key,
    )  # fmt: skip

    def record(
        status: str,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        latency_ms: int = 0,
        response: dict[str, Any] | None = None,
    ) -> int:
        return _log_call(
            session, tenant_id=tenant_id, invoice_id=invoice_id, client=client,
            prompt_version=prompt_version, key=key, status=status, input_tokens=input_tokens,
            output_tokens=output_tokens, latency_ms=latency_ms, response=response,
        )  # fmt: skip

    validation_error: str | None = None
    calls = 0
    total_cost = 0
    for _ in range(MAX_ATTEMPTS):
        spent, now = spend_today_micros(session, tenant_id)
        if cap_reached(spent, settings.daily_spend_cap_micros):
            raise SpendCapReached(next_utc_midnight(now))
        calls += 1
        try:
            result: LlmResult = client.extract(replace(request, validation_error=validation_error))
        except LlmOutputError as exc:
            total_cost += record(
                "output_invalid", input_tokens=exc.input_tokens,
                output_tokens=exc.output_tokens, latency_ms=exc.latency_ms,
            )  # fmt: skip
            validation_error = str(exc)
            continue
        except TransientLlmError:
            record("transient_error")
            raise
        except PermanentLlmError:
            record("rejected")
            raise
        try:
            parsed = InvoiceExtraction.model_validate(result.payload)
            bad_pages = pages_out_of_range(parsed, len(pages))
            if bad_pages:
                raise ValueError(
                    f"page must be between 1 and {len(pages)}: {', '.join(bad_pages[:10])}"
                )
        except (ValidationError, ValueError) as exc:  # ValidationError is a ValueError
            total_cost += record(
                "schema_invalid", input_tokens=result.input_tokens,
                output_tokens=result.output_tokens, latency_ms=result.latency_ms,
            )  # fmt: skip
            validation_error = _summarize(exc) if isinstance(exc, ValidationError) else str(exc)
            continue
        total_cost += record(
            "ok", input_tokens=result.input_tokens, output_tokens=result.output_tokens,
            latency_ms=result.latency_ms, response=_seal(parsed.model_dump(mode="json"), vault),
        )  # fmt: skip
        return Extracted(parsed, cached=False, calls=calls, cost_micros=total_cost)
    raise ExtractionFailed("SCHEMA_INVALID")
