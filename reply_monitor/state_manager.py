"""Load, save, and compute derived state for GDPR reply monitoring."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

from reply_monitor.models import CompanyState, ReplyRecord

_STATE_PATH = Path(__file__).parent.parent / "user_data" / "reply_state.json"
_SUBPROCESSOR_STATE_PATH = Path(__file__).parent.parent / "user_data" / "subprocessor_reply_state.json"
_SAR_DEADLINE_DAYS = 30

# ---------------------------------------------------------------------------
# Status priority for display sorting
# Higher = more urgent
# ---------------------------------------------------------------------------
_STATUS_PRIORITY: dict[str, int] = {
    "OVERDUE":              9,
    "ACTION_REQUIRED":      8,
    "ADDRESS_NOT_FOUND":    7,
    "BOUNCED":              6,
    "DENIED":               5,
    "COMPLETED":            4,
    "EXTENDED":             4,
    "USER_REPLIED":         3,
    "PORTAL_VERIFICATION":  3,
    "ACKNOWLEDGED":         2,
    "PORTAL_SUBMITTED":     2,
    "PENDING":              1,
}

# Tags that indicate a terminal / resolved state
_TERMINAL_TAGS = frozenset({
    "DATA_PROVIDED_LINK", "DATA_PROVIDED_ATTACHMENT", "DATA_PROVIDED_PORTAL",
    "DATA_PROVIDED_INLINE", "FULFILLED_DELETION",
    "REQUEST_DENIED", "NO_DATA_HELD", "NOT_GDPR_APPLICABLE",
})

# Tags that require user action
_ACTION_TAGS = frozenset({
    "CONFIRMATION_REQUIRED", "IDENTITY_REQUIRED", "MORE_INFO_REQUIRED",
    "WRONG_CHANNEL", "HUMAN_REVIEW", "PORTAL_VERIFICATION",
})

# Tags that count as acknowledged (but not action required)
_ACK_TAGS = frozenset({"AUTO_ACKNOWLEDGE", "REQUEST_ACCEPTED", "IN_PROGRESS"})


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def load_state(account_email: str, *, path: Path | None = None, data_dir: Path | None = None) -> dict[str, CompanyState]:
    """Load per-domain states for account_email from reply_state.json.

    Returns empty dict if the file doesn't exist or has no data for this account.
    """
    if path is None:
        path = (data_dir / "reply_state.json") if data_dir else _STATE_PATH
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
    path: Path | None = None,
    data_dir: Path | None = None,
) -> None:
    """Persist per-domain states for account_email to reply_state.json."""
    if path is None:
        path = (data_dir / "reply_state.json") if data_dir else _STATE_PATH
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


def save_portal_submission(
    account_email: str,
    domain: str,
    *,
    status: str,
    portal_url: str = "",
    confirmation_ref: str = "",
    error: str = "",
    data_dir: Path | None = None,
) -> None:
    """Record portal submission status on a company's state in reply_state.json.

    status: "submitted" | "manual" | "failed"
    Preserves existing gmail_thread_id so monitor continues tracking email replies.
    """
    states = load_state(account_email, data_dir=data_dir)
    state = states.get(domain)
    if not state:
        return
    state.portal_submission = {
        "status": status,
        "submitted_at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "portal_url": portal_url,
        "confirmation_ref": confirmation_ref,
        "error": error,
    }
    save_state(account_email, states, data_dir=data_dir)


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
    # Address exhausted: all retry attempts failed — terminal state
    if state.address_exhausted:
        return "ADDRESS_NOT_FOUND"

    tags_seen: set[str] = set()
    for reply in state.replies:
        if "NON_GDPR" in reply.tags or "YOUR_REPLY" in reply.tags:
            continue  # newsletters/marketing and own outgoing replies — invisible to status computation
        tags_seen.update(reply.tags)

    # Also include DATA_PROVIDED/FULFILLED_DELETION tags from past attempts.
    # This preserves COMPLETED status when a new SAR was sent to a company after
    # data was already received (promote_latest_attempt would archive the old replies).
    _DATA_TERMINAL = frozenset({
        "DATA_PROVIDED_LINK", "DATA_PROVIDED_ATTACHMENT",
        "DATA_PROVIDED_PORTAL", "FULFILLED_DELETION",
    })
    for pa in state.past_attempts:
        for r in pa.get("replies", []):
            if "NON_GDPR" not in r.get("tags", []):
                for tag in r.get("tags", []):
                    if tag in _DATA_TERMINAL:
                        tags_seen.add(tag)

    if "BOUNCE_PERMANENT" in tags_seen:
        # Only treat as BOUNCED if the bounce is the most recent event.
        # If a non-bounce reply arrived after the bounce, the bounce is superseded.
        last_bounce = max(
            (r.received_at for r in state.replies
             if "NON_GDPR" not in r.tags and "BOUNCE_PERMANENT" in r.tags),
            default="",
        )
        last_non_bounce = max(
            (r.received_at for r in state.replies
             if "NON_GDPR" not in r.tags and "BOUNCE_PERMANENT" not in r.tags),
            default="",
        )
        if last_bounce >= last_non_bounce:
            return "BOUNCED"
        # else: bounce superseded by later reply — drop BOUNCE_PERMANENT and fall through
        tags_seen.discard("BOUNCE_PERMANENT")

    # OVERDUE: past deadline with no terminal status
    try:
        deadline_str = state.deadline or ""
        if deadline_str:
            deadline = date.fromisoformat(deadline_str)
            if date.today() > deadline and not (tags_seen & _TERMINAL_TAGS):
                return "OVERDUE"
    except (ValueError, AttributeError):
        pass

    # Terminal tags (data provided, denied, etc.) override unresolved actions.
    # If the company already fulfilled the request, stale action items are moot.
    if tags_seen & _TERMINAL_TAGS:
        if {"REQUEST_DENIED", "NO_DATA_HELD", "NOT_GDPR_APPLICABLE"} & tags_seen:
            return "DENIED"
        return "COMPLETED"

    action_replies = [
        r for r in state.replies
        if "NON_GDPR" not in r.tags and "YOUR_REPLY" not in r.tags
        and bool(set(r.tags) & _ACTION_TAGS)
    ]
    if action_replies:
        # Check if all action replies are resolved (sent, dismissed, or YOUR_REPLY postdates them)
        all_resolved = all(
            r.reply_review_status in ("sent", "dismissed") for r in action_replies
        )
        if not all_resolved:
            # Check if a YOUR_REPLY exists that postdates the latest action reply
            latest_action_at = max(r.received_at for r in action_replies)
            your_replies = [
                r for r in state.replies if "YOUR_REPLY" in r.tags
            ]
            if your_replies and max(r.received_at for r in your_replies) > latest_action_at:
                all_resolved = True
        if all_resolved:
            return "USER_REPLIED"
        return "ACTION_REQUIRED"

    if "EXTENDED" in tags_seen:
        return "EXTENDED"

    if tags_seen & _ACK_TAGS:
        return "ACKNOWLEDGED"

    # Portal-specific statuses (more informative than bare PENDING)
    if state.portal_status == "awaiting_verification":
        return "PORTAL_VERIFICATION"
    if state.portal_status in ("submitted", "awaiting_captcha"):
        return "PORTAL_SUBMITTED"

    return "PENDING"


def days_remaining(sar_sent_at: str | None) -> int:
    """Return days left until the 30-day GDPR deadline from sent date.

    Returns _SAR_DEADLINE_DAYS (30) if sar_sent_at is None or empty,
    so portal/postal records without a thread ID don't crash the dashboard.
    """
    if not sar_sent_at:
        return _SAR_DEADLINE_DAYS
    try:
        sent = _parse_iso_date(sar_sent_at)
        deadline = sent + timedelta(days=_SAR_DEADLINE_DAYS)
        return (deadline - date.today()).days
    except Exception:
        return _SAR_DEADLINE_DAYS


def deadline_from_sent(sar_sent_at: str | None) -> str:
    """Return ISO deadline date (YYYY-MM-DD) 30 days from sar_sent_at.

    Returns today + 30 days if sar_sent_at is None or empty.
    """
    if not sar_sent_at:
        return (date.today() + timedelta(days=_SAR_DEADLINE_DAYS)).isoformat()
    try:
        sent = _parse_iso_date(sar_sent_at)
        return (sent + timedelta(days=_SAR_DEADLINE_DAYS)).isoformat()
    except Exception:
        return (date.today() + timedelta(days=_SAR_DEADLINE_DAYS)).isoformat()


def log_status_transition(state: CompanyState, old_status: str, new_status: str, reason: str = "") -> None:
    """Append a status transition entry to the state's log."""
    if old_status == new_status:
        return
    state.status_log.append({
        "from": old_status,
        "to": new_status,
        "at": datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z"),
        "reason": reason,
    })


def set_portal_status(
    state: CompanyState,
    portal_status: str,
    *,
    confirmation_ref: str = "",
    screenshot: str = "",
) -> CompanyState:
    """Update portal_status on a CompanyState and log the transition."""
    old = compute_status(state)
    state.portal_status = portal_status
    if confirmation_ref:
        state.portal_confirmation_ref = confirmation_ref
    if screenshot:
        state.portal_screenshot = screenshot
    new = compute_status(state)
    log_status_transition(state, old, new, reason=f"portal_status={portal_status}")
    return state


def verify_portal(state: CompanyState) -> CompanyState:
    """Mark portal verification as passed: restart deadline from now."""
    old = compute_status(state)
    now_str = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    state.portal_verified_at = now_str
    state.portal_status = "submitted"
    # Restart 30-day countdown from verification date
    state.deadline = deadline_from_sent(now_str)
    new = compute_status(state)
    log_status_transition(state, old, new, reason="portal_verification_passed")
    return state


def status_sort_key(status: str) -> int:
    """Return numeric priority for sorting — higher means more urgent."""
    return _STATUS_PRIORITY.get(status, 0)


# ---------------------------------------------------------------------------
# Company-level (two-stream) status derivation
# ---------------------------------------------------------------------------

_SAR_TERMINAL = frozenset({"COMPLETED", "DENIED"})
_STALLED      = frozenset({"BOUNCED", "ADDRESS_NOT_FOUND"})
_PROGRESS     = frozenset({"ACKNOWLEDGED", "EXTENDED", "PORTAL_SUBMITTED", "PORTAL_VERIFICATION"})

_COMPANY_STATUS_PRIORITY: dict[str, int] = {
    "OVERDUE":          8,
    "ACTION_REQUIRED":  7,
    "STALLED":          6,
    "USER_REPLIED":     5,
    "DATA_RECEIVED":    4,
    "FULLY_RESOLVED":   3,
    "IN_PROGRESS":      2,
    "SP_PENDING":       1,
    "PENDING":          0,
}


def compute_company_status(
    sar_status: str,
    sp_status: str,
    sp_sent: bool,
) -> str:
    """Derive company-level status aggregating SAR + SP streams.

    SP is supplementary — sp_sent=False never downgrades company status.
    SP can only escalate (e.g. SP=OVERDUE surfaces even if SAR=COMPLETED).
    """
    if sar_status == "OVERDUE" or (sp_sent and sp_status == "OVERDUE"):
        return "OVERDUE"
    if sar_status == "ACTION_REQUIRED" or (sp_sent and sp_status == "ACTION_REQUIRED"):
        return "ACTION_REQUIRED"
    if sar_status in _STALLED or (sp_sent and sp_status in _STALLED):
        return "STALLED"
    if sar_status in _SAR_TERMINAL:
        if not sp_sent or sp_status in _SAR_TERMINAL:
            return "FULLY_RESOLVED"
        return "DATA_RECEIVED"   # SP sent but still open
    if sar_status in _PROGRESS:
        return "IN_PROGRESS"
    if sar_status == "USER_REPLIED":
        return "USER_REPLIED"
    if sar_status == "PENDING" and sp_sent:
        return "SP_PENDING"
    return "PENDING"


def promote_latest_attempt(
    domain: str,
    sent_records: list[dict],
    existing_state: "CompanyState | None",
    deadline_fn,
) -> "CompanyState":
    """Ensure CompanyState reflects the most recent sent letter for this domain.

    When multiple letters were sent to the same domain (e.g. first address bounced,
    user retried with a new address), the most recent letter becomes the "active"
    attempt. Any older attempts — along with their existing replies — are archived
    into ``past_attempts`` so the history is preserved.

    Args:
        domain: The domain key (e.g. "reflexivity.com").
        sent_records: All sent-letter records for this domain, in any order.
        existing_state: The current CompanyState from reply_state.json, or None.
        deadline_fn: Callable that converts an ISO sent_at string to a deadline string.

    Returns:
        An updated CompanyState whose top-level fields reflect the newest attempt.
    """
    from reply_monitor.models import CompanyState  # avoid circular at module level

    if not sent_records:
        raise ValueError(f"No sent records for domain {domain}")

    # Sort oldest-first so we can iterate in chronological order
    sorted_records = sorted(sent_records, key=lambda r: r.get("sent_at", ""))
    latest = sorted_records[-1]
    older = sorted_records[:-1]

    latest_thread = latest.get("gmail_thread_id", "")

    # Build a lookup of replies by thread_id from the existing state
    # (both active replies and any previously archived past_attempts).
    thread_replies: dict[str, list[dict]] = {}
    if existing_state:
        active_thread = existing_state.gmail_thread_id
        if active_thread:
            thread_replies[active_thread] = [r.to_dict() for r in existing_state.replies]
        for pa in existing_state.past_attempts:
            t = pa.get("gmail_thread_id", "")
            if t:
                thread_replies[t] = pa.get("replies", [])

    # Build past_attempts list (one entry per older sent record)
    past_attempts = []
    for rec in older:
        t = rec.get("gmail_thread_id", "")
        past_attempts.append({
            "to_email": rec.get("to_email", ""),
            "gmail_thread_id": t,
            "sar_sent_at": rec.get("sent_at", ""),
            "deadline": deadline_fn(rec.get("sent_at", "")),
            "replies": thread_replies.get(t, []),
        })

    # Active replies are those belonging to the latest thread
    from reply_monitor.models import ReplyRecord
    active_reply_dicts = thread_replies.get(latest_thread, [])
    active_replies = [ReplyRecord.from_dict(r) for r in active_reply_dicts]

    company_name = latest.get("company_name", domain)

    # Preserve portal fields: prefer existing state (may have been updated via
    # verify_portal), fall back to sent record values.
    portal_status = ""
    portal_confirmed_ref = ""
    portal_screenshot = ""
    portal_verified_at = ""
    status_log: list[dict] = []
    if existing_state:
        portal_status = existing_state.portal_status
        portal_confirmed_ref = existing_state.portal_confirmation_ref
        portal_screenshot = existing_state.portal_screenshot
        portal_verified_at = existing_state.portal_verified_at
        status_log = existing_state.status_log
    # If no existing portal_status, seed from the sent record
    if not portal_status:
        portal_status = latest.get("portal_status", "")
    if not portal_confirmed_ref:
        portal_confirmed_ref = latest.get("portal_confirmation_ref", "")
    if not portal_screenshot:
        portal_screenshot = latest.get("portal_screenshot", "")

    return CompanyState(
        domain=domain,
        company_name=company_name,
        sar_sent_at=latest.get("sent_at", ""),
        to_email=latest.get("to_email", ""),
        subject=latest.get("subject", ""),
        gmail_thread_id=latest_thread,
        deadline=deadline_fn(latest.get("sent_at", "")),
        replies=active_replies,
        last_checked=existing_state.last_checked if existing_state else "",
        past_attempts=past_attempts,
        portal_status=portal_status,
        portal_confirmation_ref=portal_confirmed_ref,
        portal_screenshot=portal_screenshot,
        portal_verified_at=portal_verified_at,
        status_log=status_log,
    )


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
