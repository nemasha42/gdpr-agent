"""Load, save, and compute derived state for GDPR reply monitoring."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from reply_monitor.models import CompanyState, ReplyRecord

_STATE_PATH = Path(__file__).parent.parent / "user_data" / "reply_state.json"
_SAR_DEADLINE_DAYS = 30

# ---------------------------------------------------------------------------
# Status priority for display sorting
# Higher = more urgent
# ---------------------------------------------------------------------------
_STATUS_PRIORITY: dict[str, int] = {
    "OVERDUE":          8,
    "ACTION_REQUIRED":  7,
    "BOUNCED":          6,
    "DENIED":           5,
    "COMPLETED":        4,
    "EXTENDED":         3,
    "ACKNOWLEDGED":     2,
    "PENDING":          1,
}

# Tags that indicate a terminal / resolved state
_TERMINAL_TAGS = frozenset({
    "DATA_PROVIDED_LINK", "DATA_PROVIDED_ATTACHMENT", "DATA_PROVIDED_PORTAL",
    "FULFILLED_DELETION", "REQUEST_DENIED", "NO_DATA_HELD", "NOT_GDPR_APPLICABLE",
})

# Tags that require user action
_ACTION_TAGS = frozenset({
    "CONFIRMATION_REQUIRED", "IDENTITY_REQUIRED", "MORE_INFO_REQUIRED",
    "WRONG_CHANNEL",
})

# Tags that count as acknowledged (but not action required)
_ACK_TAGS = frozenset({"AUTO_ACKNOWLEDGE", "REQUEST_ACCEPTED", "IN_PROGRESS"})


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_state(account_email: str, *, path: Path = _STATE_PATH) -> dict[str, CompanyState]:
    """Load per-domain states for account_email from reply_state.json.

    Returns empty dict if the file doesn't exist or has no data for this account.
    """
    key = _safe_email(account_email)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}
    account_data = raw.get(key, {})
    return {
        domain: CompanyState.from_dict(record)
        for domain, record in account_data.items()
    }


def save_state(
    account_email: str,
    states: dict[str, CompanyState],
    *,
    path: Path = _STATE_PATH,
) -> None:
    """Persist per-domain states for account_email to reply_state.json."""
    key = _safe_email(account_email)
    path.parent.mkdir(parents=True, exist_ok=True)

    # Load existing data for other accounts
    existing: dict = {}
    if path.exists():
        try:
            existing = json.loads(path.read_text())
        except (json.JSONDecodeError, OSError):
            pass

    existing[key] = {domain: state.to_dict() for domain, state in states.items()}
    path.write_text(json.dumps(existing, indent=2))


def update_state(state: CompanyState, new_replies: list[ReplyRecord]) -> CompanyState:
    """Merge new replies into state and update last_checked timestamp."""
    existing_ids = {r.gmail_message_id for r in state.replies}
    for reply in new_replies:
        if reply.gmail_message_id not in existing_ids:
            state.replies.append(reply)
    state.last_checked = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    return state


def compute_status(state: CompanyState) -> str:
    """Derive the current company status from accumulated reply tags.

    Priority order (plan spec):
        BOUNCED > OVERDUE > ACTION_REQUIRED > DENIED > COMPLETED >
        EXTENDED > ACKNOWLEDGED > PENDING
    """
    tags_seen: set[str] = set()
    for reply in state.replies:
        if "NON_GDPR" in reply.tags:
            continue  # newsletters/marketing — invisible to status computation
        tags_seen.update(reply.tags)

    if "BOUNCE_PERMANENT" in tags_seen:
        return "BOUNCED"

    # OVERDUE: past deadline with no terminal status
    try:
        deadline = date.fromisoformat(state.deadline)
        if date.today() > deadline and not (tags_seen & _TERMINAL_TAGS):
            return "OVERDUE"
    except ValueError:
        pass

    if tags_seen & _ACTION_TAGS:
        return "ACTION_REQUIRED"

    if {"REQUEST_DENIED", "NO_DATA_HELD", "NOT_GDPR_APPLICABLE"} & tags_seen:
        return "DENIED"

    if {"DATA_PROVIDED_LINK", "DATA_PROVIDED_ATTACHMENT",
            "DATA_PROVIDED_PORTAL", "FULFILLED_DELETION"} & tags_seen:
        return "COMPLETED"

    if "EXTENDED" in tags_seen:
        return "EXTENDED"

    if tags_seen & _ACK_TAGS:
        return "ACKNOWLEDGED"

    return "PENDING"


def days_remaining(sar_sent_at: str) -> int:
    """Return days left until the 30-day GDPR deadline from sent date."""
    sent = _parse_iso_date(sar_sent_at)
    deadline = sent + timedelta(days=_SAR_DEADLINE_DAYS)
    return (deadline - date.today()).days


def deadline_from_sent(sar_sent_at: str) -> str:
    """Return ISO deadline date (YYYY-MM-DD) 30 days from sar_sent_at."""
    sent = _parse_iso_date(sar_sent_at)
    return (sent + timedelta(days=_SAR_DEADLINE_DAYS)).isoformat()


def status_sort_key(status: str) -> int:
    """Return numeric priority for sorting — higher means more urgent."""
    return _STATUS_PRIORITY.get(status, 0)


def domain_from_sent_record(record: dict) -> str:
    """Derive domain from a sent_letters.json record.

    Uses to_email domain when available, otherwise falls back to
    company_name lowercased (last resort for portal/postal records).
    """
    to_email = record.get("to_email", "")
    if to_email and "@" in to_email:
        return to_email.split("@")[-1].lower()
    # Fallback: normalize company name to a guessable domain
    name = record.get("company_name", "unknown").lower()
    name = name.replace(" ", "").replace(",", "").replace(".", "")
    return f"{name}.com"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_email(email: str) -> str:
    return email.replace("@", "_at_").replace(".", "_")


def _parse_iso_date(ts: str) -> date:
    """Parse ISO datetime or date string to a date object."""
    ts = ts.strip()
    # Handle UTC Z suffix
    ts = ts.replace("Z", "+00:00")
    try:
        return datetime.fromisoformat(ts).date()
    except ValueError:
        return datetime.strptime(ts[:10], "%Y-%m-%d").date()
