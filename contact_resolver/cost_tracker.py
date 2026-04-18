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
_MAX_LOG_ENTRIES = 1000  # rotate persistent log once it exceeds this many entries

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

# Resolver stats for the current session
_resolver_stats: dict[str, int] = {"total": 0, "cache": 0, "free": 0, "llm": 0, "not_found": 0}

# Optional LLM call cap (set via set_llm_limit())
_llm_limit: int | None = None

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
    purpose: str = "",
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
    _persist(call, purpose=purpose)


def load_persistent_log() -> list[dict]:
    """Read and return all records from cost_log.json (returns [] if file missing)."""
    if not _COST_LOG_PATH.exists():
        return []
    try:
        return json.loads(_COST_LOG_PATH.read_text())
    except Exception as exc:
        print(f"[cost_tracker] load cost log failed: {exc}")
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

    # Resolver cache hit rate
    if _resolver_stats["total"] > 0:
        t = _resolver_stats["total"]
        c = _resolver_stats["cache"]
        f = _resolver_stats["free"]
        l = _resolver_stats["llm"]
        nf = _resolver_stats["not_found"]
        print(
            f"\n[RESOLVER] {t} companies attempted: "
            f"{c} cache  {f} free-source  {l} LLM  {nf} not-found"
        )


def record_resolver_result(source: str | None) -> None:
    """Record the step that resolved (or failed to resolve) a company.

    source: "cache" | "dataowners_override" | "datarequests" | "privacy_scrape"
            | "llm_search" | None (not found)
    """
    _resolver_stats["total"] += 1
    if source is None:
        _resolver_stats["not_found"] += 1
    elif source == "cache":
        _resolver_stats["cache"] += 1
    elif source == "llm_search":
        _resolver_stats["llm"] += 1
    else:
        _resolver_stats["free"] += 1


def set_llm_limit(n: int) -> None:
    """Cap LLM calls for this session.

    n=0 blocks all LLM calls. Omit (don't call) for unlimited.
    """
    global _llm_limit
    _llm_limit = n


def is_llm_limit_reached() -> bool:
    """Return True if the LLM call cap has been reached."""
    if _llm_limit is None:
        return False
    return len(_session_log) >= _llm_limit


def reset() -> None:
    """Clear the session log and resolver stats — used in tests."""
    _session_log.clear()
    _resolver_stats.update({"total": 0, "cache": 0, "free": 0, "llm": 0, "not_found": 0})
    global _llm_limit
    _llm_limit = None


def get_log() -> list[_LLMCall]:
    """Return a shallow copy of the current session log — used in tests."""
    return list(_session_log)


# ---------------------------------------------------------------------------
# Private helpers
# ---------------------------------------------------------------------------


def _persist(call: _LLMCall, *, purpose: str = "") -> None:
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
            "purpose": purpose,
            "company_name": call.company_name,
            "model": call.model,
            "input_tokens": call.input_tokens,
            "output_tokens": call.output_tokens,
            "cost_usd": call.cost_usd,
            "found": call.found,
        })
        # Rotate: keep only the most recent entries
        if len(existing) > _MAX_LOG_ENTRIES:
            existing = existing[-_MAX_LOG_ENTRIES:]
        _COST_LOG_PATH.write_text(json.dumps(existing, indent=2))
    except Exception as e:
        print(f"[cost_tracker] Warning: cost log not saved: {e}", flush=True)
