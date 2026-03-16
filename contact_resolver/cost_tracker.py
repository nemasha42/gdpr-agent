"""Track Anthropic LLM API call costs for the current session and persistently."""

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

# Per-model pricing (input $/token, output $/token)
_PRICING: dict[str, tuple[float, float]] = {
    "claude-haiku-4-5-20251001": (0.80 / 1_000_000, 4.00 / 1_000_000),
    "claude-sonnet-4-6":         (3.00 / 1_000_000, 15.0 / 1_000_000),
}
_DEFAULT_PRICING: tuple[float, float] = (3.00 / 1_000_000, 15.0 / 1_000_000)

_COST_LOG_PATH = Path(__file__).parent.parent / "user_data" / "cost_log.json"

# Table column content widths (excludes leading "  " padding)
_W_COMPANY = 12
_W_TOKENS = 8
_W_COST = 14
_W_RESULT = 8

# Inner width = (12+2)+1+(8+2)+1+(8+2)+1+(14+2)+1+(8+2) = 65
_INNER = (
    (_W_COMPANY + 2) + 1 +
    (_W_TOKENS + 2) + 1 +
    (_W_TOKENS + 2) + 1 +
    (_W_COST + 2) + 1 +
    (_W_RESULT + 2)
)


@dataclass
class _LLMCall:
    timestamp: str
    company_name: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    found: bool
    source: str = ""


# Module-level session log — reset between runs via reset()
_session_log: list[_LLMCall] = []

# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def record_llm_call(
    company_name: str,
    input_tokens: int,
    output_tokens: int,
    model: str,
    *,
    found: bool = False,
    source: str = "",
) -> None:
    """Append one LLM API call to the session log and persist to cost_log.json."""
    in_price, out_price = _PRICING.get(model, _DEFAULT_PRICING)
    cost = (input_tokens * in_price) + (output_tokens * out_price)
    call = _LLMCall(
        timestamp=datetime.now().isoformat(timespec="seconds"),
        company_name=company_name,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cost_usd=cost,
        found=found,
        source=source,
    )
    _session_log.append(call)
    _persist(call)


def load_persistent_log() -> list[dict]:
    """Read and return all records from cost_log.json (returns [] if file missing)."""
    if not _COST_LOG_PATH.exists():
        return []
    try:
        return json.loads(_COST_LOG_PATH.read_text())
    except Exception:
        return []


def print_cost_summary() -> None:
    """Print a formatted box table of all LLM calls and their costs."""
    if not _session_log:
        print("[COST] No LLM calls made this run — all resolved from cache/DB")
        return

    total_in = sum(c.input_tokens for c in _session_log)
    total_out = sum(c.output_tokens for c in _session_log)
    total_cost = sum(c.cost_usd for c in _session_log)

    top = f"┌{'─' * _INNER}┐"
    title = f"│  {'LLM COST SUMMARY':<{_INNER - 2}}│"
    sep_h = (
        f"├{'─' * (_W_COMPANY + 2)}"
        f"┬{'─' * (_W_TOKENS + 2)}"
        f"┬{'─' * (_W_TOKENS + 2)}"
        f"┬{'─' * (_W_COST + 2)}"
        f"┬{'─' * (_W_RESULT + 2)}┤"
    )
    sep_m = sep_h.replace("┬", "┼").replace("├", "├").replace("┤", "┤")
    bot = f"└{'─' * _INNER}┘"

    def row(company: str, inp: str, out: str, cost: str, result: str) -> str:
        return (
            f"│  {company[:_W_COMPANY]:<{_W_COMPANY}}"
            f"│  {inp[:_W_TOKENS]:<{_W_TOKENS}}"
            f"│  {out[:_W_TOKENS]:<{_W_TOKENS}}"
            f"│  {cost[:_W_COST]:<{_W_COST}}"
            f"│  {result[:_W_RESULT]:<{_W_RESULT}}│"
        )

    lines = [
        top,
        title,
        sep_h,
        row("Company", "Input", "Output", "Cost (USD)", "Result"),
        sep_m,
    ]
    for call in _session_log:
        result_str = "✓ found" if call.found else "✗ miss"
        lines.append(
            row(
                call.company_name,
                f"{call.input_tokens:,}",
                f"{call.output_tokens:,}",
                f"${call.cost_usd:.4f}",
                result_str,
            )
        )
        lines.append(sep_m)

    lines.append(
        row("TOTAL", f"{total_in:,}", f"{total_out:,}", f"${total_cost:.4f}", "")
    )
    lines.append(bot)

    # Cumulative total from persistent log
    all_records = load_persistent_log()
    if all_records:
        cum_in = sum(r.get("input_tokens", 0) for r in all_records)
        cum_out = sum(r.get("output_tokens", 0) for r in all_records)
        cum_cost = sum(r.get("cost_usd", 0.0) for r in all_records)
        lines.append(
            row("CUMULATIVE", f"{cum_in:,}", f"{cum_out:,}", f"${cum_cost:.4f}", f"{len(all_records)} calls")
        )
        lines.append(bot)

    print("\n".join(lines))


def reset() -> None:
    """Clear the session log — used in tests."""
    _session_log.clear()


def get_log() -> list[_LLMCall]:
    """Return a shallow copy of the current session log — used in tests."""
    return list(_session_log)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _persist(call: _LLMCall) -> None:
    """Append a single call record to cost_log.json."""
    import os
    if os.environ.get("PYTEST_CURRENT_TEST"):
        return  # never pollute the real log with test fixtures
    try:
        _COST_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
        existing = load_persistent_log()
        existing.append({
            "timestamp": call.timestamp,
            "source": call.source,
            "company_name": call.company_name,
            "model": call.model,
            "input_tokens": call.input_tokens,
            "output_tokens": call.output_tokens,
            "cost_usd": call.cost_usd,
            "found": call.found,
        })
        _COST_LOG_PATH.write_text(json.dumps(existing, indent=2))
    except Exception:
        pass  # persistence is best-effort; never crash the pipeline
