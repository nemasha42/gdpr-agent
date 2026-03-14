"""Track Anthropic LLM API call costs for the current session."""

from dataclasses import dataclass, field
from datetime import datetime

# Pricing for claude-sonnet-4-6 per the spec
_INPUT_COST_PER_TOKEN: float = 3.0 / 1_000_000   # $3 per million
_OUTPUT_COST_PER_TOKEN: float = 15.0 / 1_000_000  # $15 per million

# Table column content widths (excludes leading "  " padding)
_W_COMPANY = 12
_W_TOKENS = 8
_W_COST = 14

# Inner width = 14 + │ + 10 + │ + 10 + │ + 16 = 53
_INNER = _W_COMPANY + 2 + 1 + _W_TOKENS + 2 + 1 + _W_TOKENS + 2 + 1 + _W_COST + 2


@dataclass
class _LLMCall:
    timestamp: str
    company_name: str
    model: str
    input_tokens: int
    output_tokens: int
    cost_usd: float


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
) -> None:
    """Append one LLM API call to the session log."""
    cost = (input_tokens * _INPUT_COST_PER_TOKEN) + (output_tokens * _OUTPUT_COST_PER_TOKEN)
    _session_log.append(
        _LLMCall(
            timestamp=datetime.now().isoformat(timespec="seconds"),
            company_name=company_name,
            model=model,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cost_usd=cost,
        )
    )


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
        f"┬{'─' * (_W_COST + 2)}┤"
    )
    sep_m = sep_h.replace("┬", "┼").replace("├", "├").replace("┤", "┤")
    bot = f"└{'─' * _INNER}┘"

    def row(company: str, inp: str, out: str, cost: str) -> str:
        return (
            f"│  {company[:_W_COMPANY]:<{_W_COMPANY}}"
            f"│  {inp[:_W_TOKENS]:<{_W_TOKENS}}"
            f"│  {out[:_W_TOKENS]:<{_W_TOKENS}}"
            f"│  {cost[:_W_COST]:<{_W_COST}}│"
        )

    lines = [
        top,
        title,
        sep_h,
        row("Company", "Input", "Output", "Cost (USD)"),
        sep_m,
    ]
    for call in _session_log:
        lines.append(
            row(
                call.company_name,
                f"{call.input_tokens:,}",
                f"{call.output_tokens:,}",
                f"${call.cost_usd:.4f}",
            )
        )
        lines.append(sep_m)

    lines.append(
        row(
            "TOTAL",
            f"{total_in:,}",
            f"{total_out:,}",
            f"${total_cost:.4f}",
        )
    )
    lines.append(bot)
    print("\n".join(lines))


def reset() -> None:
    """Clear the session log — used in tests."""
    _session_log.clear()


def get_log() -> list[_LLMCall]:
    """Return a shallow copy of the current session log — used in tests."""
    return list(_session_log)
