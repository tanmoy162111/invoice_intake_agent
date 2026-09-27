"""Renders one `eval/run_eval.py` run into the markdown committed at `eval/reports/*.md` (playbook
§8.3). Pure formatting: `core/eval_metrics.py` decides the numbers, this only lays them out.
"""

from dataclasses import dataclass
from datetime import datetime

from intake.core.eval_metrics import FieldAccuracy, Metrics
from intake.core.statuses import DocQuality

_QUALITY_ORDER = (DocQuality.CLEAN, DocQuality.SCANNED, DocQuality.PHOTO, DocQuality.UNKNOWN)


@dataclass(frozen=True)
class RunInfo:
    provider: str  # settings.llm_provider
    model: str
    prompt_version: str
    git_sha: str
    generated_at: datetime  # UTC


def _pct(value: float | None) -> str:
    return "—" if value is None else f"{value * 100:.1f}%"


def _usd(micros: float | None) -> str:
    return "—" if micros is None else f"${micros / 1_000_000:.4f}"


def _ms(value: float | None) -> str:
    return "—" if value is None else f"{value:,.0f} ms"


def _field_table(rows: tuple[FieldAccuracy, ...]) -> list[str]:
    lines = ["| Field | Accuracy | Correct / total |", "|---|---|---|"]
    for r in rows:
        lines.append(f"| `{r.field}` | {_pct(r.accuracy)} | {r.correct} / {r.total} |")
    return lines


def render_report(metrics: Metrics, run: RunInfo) -> str:
    lines: list[str] = []
    lines.append(f"# Evaluation report — {run.generated_at:%Y-%m-%d}")
    lines.append("")
    if run.provider != "anthropic":
        lines.append(
            f"> ⚠ **Non-reference build.** This run used `{run.provider}` (`{run.model}`), not the "
            "Claude-based reference build. These numbers say nothing about the Claude system's "
            "accuracy (playbook §8, ADR 0010)."
        )
        lines.append("")
    lines.append(
        f"Model: `{run.model}` · Provider: `{run.provider}` · Prompt: `{run.prompt_version}` · "
        f"Commit: `{run.git_sha}` · Invoices: {metrics.invoice_count}"
    )
    lines.append("")
    lines.append("## Headline numbers")
    lines.append("")
    lines.append("| Metric | Value |")
    lines.append("|---|---|")
    lines.append(
        f"| **False clear rate** (must be 0%) | {_pct(metrics.false_clear_rate)} "
        f"({metrics.false_clears} invoice(s)) |"
    )
    lines.append(f"| Touchless rate | {_pct(metrics.touchless_rate)} |")
    lines.append(f"| Exception recall | {_pct(metrics.exception_recall)} |")
    lines.append(f"| Exception precision | {_pct(metrics.exception_precision)} |")
    lines.append(
        f"| Line-item F1 | {_pct(metrics.lines.f1)} "
        f"(precision {_pct(metrics.lines.precision)}, recall {_pct(metrics.lines.recall)}) |"
    )
    lines.append(f"| Mean cost per invoice | {_usd(metrics.mean_cost_usd_micros)} |")
    lines.append(
        f"| Latency p50 / p95 | {_ms(metrics.latency_p50_ms)} / {_ms(metrics.latency_p95_ms)} |"
    )
    lines.append("")
    lines.append("## Field accuracy (overall)")
    lines.append("")
    lines += _field_table(metrics.fields)
    lines.append("")
    lines.append("## Field accuracy by document quality")
    for quality in _QUALITY_ORDER:
        rows = metrics.fields_by_quality.get(quality)
        if not rows:
            continue
        lines.append("")
        lines.append(f"### {quality.value}")
        lines.append("")
        lines += _field_table(rows)
    lines.append("")
    if metrics.false_clears > 0:
        lines.append(
            "**Regression gate: FAIL.** At least one invoice with a real problem was routed "
            "straight through. This must be fixed before merging any prompt, model, threshold or "
            "rule change (playbook §8.3)."
        )
    else:
        lines.append("**Regression gate: pass** on false clears (0 on the golden set).")
    lines.append("")
    return "\n".join(lines)
