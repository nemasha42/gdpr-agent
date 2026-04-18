"""Shared helpers, constants, and template utilities for the dashboard.

This module holds all helper functions, constants, context processor, and
template filter that are used by multiple route groups (or will be used by
multiple Blueprints after the full refactor).  It is a plain module — not a
Blueprint — because it contains no routes.
"""

from __future__ import annotations

import html as _html
import json
import re
import urllib.parse as _urlparse
from pathlib import Path

from flask import g
from flask_login import current_user

from letter_engine.tracker import get_log, _SUBPROCESSOR_REQUESTS_PATH
from reply_monitor.classifier import _ACTION_DRAFT_TAGS
from reply_monitor.state_manager import (
    _SUBPROCESSOR_STATE_PATH,
    compute_done_reason,
    days_remaining,
    deadline_from_sent,
    domain_from_sent_record,
    load_state,
    promote_latest_attempt,
)
from dashboard.user_model import _safe_email_to_address as _safe_email_to_addr
from dashboard.scan_state import get_all_accounts as _get_scan_accounts

# ---------------------------------------------------------------------------
# Path constants
# ---------------------------------------------------------------------------

_PROJECT_ROOT = Path(__file__).parent.parent
_USER_DATA = _PROJECT_ROOT / "user_data"
_STATE_PATH = _USER_DATA / "reply_state.json"
_COMPANIES_PATH = _PROJECT_ROOT / "data" / "companies.json"

# ---------------------------------------------------------------------------
# Extensibility hook: maps request type key -> metadata
# Adding a new type = one entry here + template section
# ---------------------------------------------------------------------------

REQUEST_TYPES = {
    "sar": {
        "label": "SAR",
        "tracker_path": _USER_DATA / "sent_letters.json",
        "state_path": _STATE_PATH,
    },
    "subprocessor": {
        "label": "SP",
        "tracker_path": _SUBPROCESSOR_REQUESTS_PATH,
        "state_path": _SUBPROCESSOR_STATE_PATH,
    },
}

# ---------------------------------------------------------------------------
# Status -> CSS colour class mapping
# ---------------------------------------------------------------------------

# Statuses where the GDPR deadline no longer applies -- hide countdown
_TERMINAL_STATUSES = {"DONE", "STALLED"}

_STATUS_COLOUR = {
    "WAITING": "primary",
    "IN_PROGRESS": "info",
    "ACTION_NEEDED": "warning",
    "REPLIED": "primary",
    "DONE": "success",
    "OVERDUE": "danger",
    "STALLED": "danger",
}

_TAG_COLOUR = {
    "AUTO_ACKNOWLEDGE": "info",
    "OUT_OF_OFFICE": "secondary",
    "BOUNCE_PERMANENT": "dark",
    "BOUNCE_TEMPORARY": "dark",
    "CONFIRMATION_REQUIRED": "warning",
    "IDENTITY_REQUIRED": "warning",
    "MORE_INFO_REQUIRED": "warning",
    "WRONG_CHANNEL": "warning",
    "HUMAN_REVIEW": "warning",
    "REQUEST_ACCEPTED": "success",
    "EXTENDED": "warning",
    "IN_PROGRESS": "info",
    "DATA_PROVIDED_LINK": "success",
    "DATA_PROVIDED_ATTACHMENT": "success",
    "DATA_PROVIDED_INLINE": "success",
    "DATA_PROVIDED_PORTAL": "success",
    "REQUEST_DENIED": "danger",
    "NO_DATA_HELD": "secondary",
    "NOT_GDPR_APPLICABLE": "secondary",
    "FULFILLED_DELETION": "success",
    "NON_GDPR": "light",
}

# Friendly display names for tags shown in the UI.
# Internal/raw tag constants are stored in reply_state.json unchanged.
_DISPLAY_NAMES: dict[str, str] = {
    "AUTO_ACKNOWLEDGE": "Acknowledged",
    "DATA_PROVIDED_LINK": "Data Provided",
    "DATA_PROVIDED_ATTACHMENT": "Data Provided",
    "DATA_PROVIDED_INLINE": "Data Provided",
    "DATA_PROVIDED_PORTAL": "Data Provided",
    "WRONG_CHANNEL": "Wrong Channel",
    "HUMAN_REVIEW": "Needs Review",
    "REQUEST_ACCEPTED": "Accepted",
    "IN_PROGRESS": "In Progress",
    "IDENTITY_REQUIRED": "ID Required",
    "CONFIRMATION_REQUIRED": "Confirm Required",
    "MORE_INFO_REQUIRED": "More Info Needed",
    "OUT_OF_OFFICE": "Out of Office",
    "BOUNCE_PERMANENT": "Bounced",
    "BOUNCE_TEMPORARY": "Temp Bounce",
    "REQUEST_DENIED": "Denied",
    "NO_DATA_HELD": "No Data Held",
    "NOT_GDPR_APPLICABLE": "Not Applicable",
    "FULFILLED_DELETION": "Deleted",
    "EXTENDED": "Extended",
    "NON_GDPR": "Non-GDPR",
}

# ---------------------------------------------------------------------------
# Tag supersession -- reduces noise on dashboard cards
# ---------------------------------------------------------------------------
# Tags are grouped into tiers; when a higher tier is present, lower-tier
# tags are hidden from the card display (but kept in per-reply detail view).

_TIER_TERMINAL: frozenset[str] = frozenset(
    {
        "DATA_PROVIDED_LINK",
        "DATA_PROVIDED_ATTACHMENT",
        "DATA_PROVIDED_INLINE",
        "DATA_PROVIDED_PORTAL",
        "REQUEST_DENIED",
        "NO_DATA_HELD",
        "NOT_GDPR_APPLICABLE",
        "FULFILLED_DELETION",
    }
)
_TIER_ACTION: frozenset[str] = frozenset(
    {
        "WRONG_CHANNEL",
        "IDENTITY_REQUIRED",
        "CONFIRMATION_REQUIRED",
        "MORE_INFO_REQUIRED",
        "HUMAN_REVIEW",
    }
)
_TIER_PROGRESS: frozenset[str] = frozenset(
    {
        "REQUEST_ACCEPTED",
        "IN_PROGRESS",
        "EXTENDED",
    }
)
_TIER_NOISE: frozenset[str] = frozenset({"OUT_OF_OFFICE", "NON_GDPR"})


def _effective_tags(all_tags: set[str]) -> list[str]:
    """Return the minimal, meaningful set of tags to show on a dashboard card.

    Applies supersession so that higher-priority tags hide redundant lower-tier
    ones (e.g. DATA_PROVIDED hides REQUEST_ACCEPTED; WRONG_CHANNEL hides ACK).
    NON_GDPR and OUT_OF_OFFICE are always suppressed unless they are the only tag.
    """
    result = all_tags - _TIER_NOISE
    if not result:
        # Only noise tags -- keep the set for the edge-case display
        return sorted(all_tags)
    if result & _TIER_TERMINAL:
        return sorted(result & _TIER_TERMINAL)
    if result & _TIER_ACTION:
        return sorted(result & _TIER_ACTION)
    if result & _TIER_PROGRESS:
        return sorted(result & _TIER_PROGRESS)
    # Tier 4 -- informational only (AUTO_ACKNOWLEDGE, BOUNCE_*)
    return sorted(result)


_ACTION_HINTS = {
    "CONFIRMATION_REQUIRED": "Click the confirmation link in the email",
    "IDENTITY_REQUIRED": "Submit identity proof as requested",
    "MORE_INFO_REQUIRED": "Clarify your request as asked",
    "WRONG_CHANNEL": "Re-submit via the channel they indicated",
    "BOUNCE_PERMANENT": "Email address bounced — contact via web",
}


# ---------------------------------------------------------------------------
# Snippet display helpers
# ---------------------------------------------------------------------------


def _clean_snippet(text: str) -> str:
    """Decode common encoding artifacts in Gmail snippets."""
    # HTML entities: &amp; -> &, &nbsp; -> space, &#160; -> space, etc.
    text = _html.unescape(text)
    # MIME quoted-printable soft line breaks: =\r\n or =\n -> space
    text = re.sub(r"=\r?\n", " ", text)
    # QP-encoded chars: =3D -> =, =20 -> space, =E2=80=99 -> ' etc.
    text = re.sub(r"=([0-9A-Fa-f]{2})", lambda m: chr(int(m.group(1), 16)), text)
    # URL-encoded fragments appearing as literal text: %20 -> space, %3D -> =
    text = re.sub(
        r"%([0-9A-Fa-f]{2})", lambda m: _urlparse.unquote("%" + m.group(1)), text
    )
    # Zero-width and invisible Unicode chars (newsletter spacers)
    text = re.sub(r"[\u200b\u200c\u200d\u2060\ufeff]", "", text)
    # Collapse multiple whitespace to single space
    text = re.sub(r"\s+", " ", text).strip()
    return text


def _is_human_friendly(text: str) -> bool:
    """Return True if text contains no common encoding artifacts."""
    if re.search(r"&[a-z]+;|&#\d+;", text):  # HTML entities
        return False
    if re.search(r"=[0-9A-Fa-f]{2}", text):  # QP artifacts
        return False
    if re.search(r"%[0-9A-Fa-f]{2}", text):  # URL encoding
        return False
    return True


# ---------------------------------------------------------------------------
# Path helpers (per-user data directories via Flask g)
# ---------------------------------------------------------------------------


def _current_data_dir() -> Path:
    """Return the current user's data directory, falling back to _USER_DATA for CLI."""
    try:
        return g.data_dir
    except (AttributeError, RuntimeError):
        return _USER_DATA


def _current_state_path() -> Path:
    return _current_data_dir() / "reply_state.json"


def _current_sp_state_path() -> Path:
    return _current_data_dir() / "subprocessor_reply_state.json"


def _current_tokens_dir() -> Path:
    return _current_data_dir() / "tokens"


def _current_sp_requests_path() -> Path:
    return _current_data_dir() / "subprocessor_requests.json"


# ---------------------------------------------------------------------------
# Reply deduplication
# ---------------------------------------------------------------------------


def _dedup_reply_rows(sar_rows: list[dict], sp_rows: list[dict]) -> list[dict]:
    """Remove from sar_rows any reply whose gmail_message_id also appears in sp_rows.

    Happens when SAR and SP requests share the same company inbox: the SAR monitor's
    address-search fallback picks up SP replies and stores them in both state files.
    The SP section is authoritative for those messages; the SAR section should hide them.
    """
    sp_ids = {r["gmail_message_id"] for r in sp_rows}
    if not sp_ids:
        return sar_rows
    return [r for r in sar_rows if r["gmail_message_id"] not in sp_ids]


# ---------------------------------------------------------------------------
# Account helpers
# ---------------------------------------------------------------------------


def _get_accounts() -> list[str]:
    """Return mailbox emails for the current logged-in user."""
    data_dir = _current_data_dir()
    accounts: set[str] = set()

    # From reply_state.json
    state_file = data_dir / "reply_state.json"
    if state_file.exists():
        import json as _json_mod

        try:
            data = _json_mod.loads(state_file.read_text())
            for safe_key in data:
                accounts.add(_safe_email_to_addr(safe_key))
        except Exception:
            pass

    # From token files
    tokens_dir = data_dir / "tokens"
    if tokens_dir.exists():
        for p in tokens_dir.glob("*_readonly.json"):
            safe_key = p.stem.replace("_readonly", "")
            accounts.add(_safe_email_to_addr(safe_key))

    return sorted(accounts)


def _get_all_accounts() -> list[str]:
    """Return all accounts found across reply_state.json, scan_state.json, and token files."""
    accounts: set[str] = set(_get_accounts())
    for safe_key in _get_scan_accounts(path=_current_data_dir() / "scan_state.json"):
        accounts.add(_safe_email_to_addr(safe_key))
    tokens_dir = _current_tokens_dir()
    if tokens_dir.exists():
        for p in tokens_dir.glob("*_readonly.json"):
            safe_key = p.name.replace("_readonly.json", "")
            accounts.add(_safe_email_to_addr(safe_key))
    return sorted(accounts)


# ---------------------------------------------------------------------------
# Data helpers
# ---------------------------------------------------------------------------


def _build_card(domain: str, state, status: str) -> dict:
    """Build a flat dict for the dashboard card template."""
    has_wrong_channel = any(
        "WRONG_CHANNEL" in r.tags for r in state.replies if "NON_GDPR" not in r.tags
    )
    if status in _TERMINAL_STATUSES or has_wrong_channel:
        remaining = None
        actual_remaining = days_remaining(state.sar_sent_at)
        elapsed = 30 - actual_remaining
        pct = max(0, min(100, int(elapsed / 30 * 100)))
        progress_colour = "secondary"
    else:
        remaining = days_remaining(state.sar_sent_at)
        elapsed = 30 - remaining
        pct = max(0, min(100, int(elapsed / 30 * 100)))
        progress_colour = (
            "danger" if remaining < 7 else "warning" if remaining < 14 else "success"
        )

    # Filter out non-GDPR noise (newsletters, marketing) from display metrics
    gdpr_replies = [r for r in state.replies if "NON_GDPR" not in r.tags]
    non_gdpr_count = len(state.replies) - len(gdpr_replies)
    # Company replies only (excludes user's own outgoing messages)
    company_replies = [r for r in gdpr_replies if "YOUR_REPLY" not in r.tags]

    raw_tags: set[str] = set()
    for r in gdpr_replies:
        raw_tags.update(r.tags)
    all_tags = _effective_tags(raw_tags)

    latest_snippet = ""
    if gdpr_replies:
        latest_snippet = gdpr_replies[-1].snippet[:100]

    # Action hint
    action_hint = ""
    action_hint_url = ""
    for r in gdpr_replies:
        for tag in r.tags:
            if tag in _ACTION_HINTS:
                hint = _ACTION_HINTS[tag]
                if tag == "WRONG_CHANNEL":
                    action_hint_url = r.extracted.get("portal_url") or r.extracted.get(
                        "confirmation_url", ""
                    )
                    if not action_hint_url:
                        record = _lookup_company(domain)
                        action_hint_url = record.get("gdpr_portal_url", "")
                action_hint = hint
                break
        if action_hint:
            break

    # All GDPR replies across current attempt AND past attempts (for completed-data hints)
    all_gdpr_replies_dicts: list[dict] = []
    for r in gdpr_replies:
        all_gdpr_replies_dicts.append({"tags": r.tags, "extracted": r.extracted})
    for pa in state.past_attempts:
        for r_dict in pa.get("replies", []):
            if "NON_GDPR" not in r_dict.get("tags", []):
                all_gdpr_replies_dicts.append(r_dict)

    # Data ready hint
    if status == "DONE":
        # Check active replies first, then past attempts
        for r_info in all_gdpr_replies_dicts:
            r_tags = r_info.get("tags", []) if isinstance(r_info, dict) else r_info.tags
            r_extracted = (
                r_info.get("extracted", {})
                if isinstance(r_info, dict)
                else r_info.extracted
            )
            if "DATA_PROVIDED_LINK" in r_tags and r_extracted.get("data_link"):
                action_hint = "Download data"
                action_hint_url = r_extracted["data_link"]
                break
            if "DATA_PROVIDED_PORTAL" in r_tags:
                action_hint = "Access your data via their account portal"
                break
            if "DATA_PROVIDED_ATTACHMENT" in r_tags:
                action_hint = "Data ready — open folder in user_data/received/"
                break
            if "DATA_PROVIDED_INLINE" in r_tags:
                action_hint = "Data provided directly in email reply"
                break

    has_data = status == "DONE" and any(
        any(t.startswith("DATA_PROVIDED") for t in r_info.get("tags", []))
        for r_info in all_gdpr_replies_dicts
    )

    # Deduplicate tried addresses (same address may appear in multiple past attempts)
    _seen_emails: set[str] = set()
    tried_emails: list[str] = []
    for pa in state.past_attempts:
        email = pa.get("to_email", "")
        if email and email.lower() not in _seen_emails:
            _seen_emails.add(email.lower())
            tried_emails.append(email)

    has_pending_draft = any(
        r.reply_review_status == "pending"
        and r.suggested_reply
        and bool(set(r.tags) & _ACTION_DRAFT_TAGS)
        for r in state.replies
    )

    done_reason = compute_done_reason(state) if status == "DONE" else ""

    return {
        "domain": domain,
        "company_name": state.company_name,
        "status": status,
        "done_reason": done_reason,
        "to_email": state.to_email,
        "tried_emails": tried_emails,
        "sar_sent_at": state.sar_sent_at[:10],
        "deadline": state.deadline,
        "remaining": remaining,
        "pct": pct,
        "progress_colour": progress_colour,
        "tags": all_tags,
        "reply_count": len(gdpr_replies),
        "has_company_replies": len(company_replies) > 0,
        "non_gdpr_count": non_gdpr_count,
        "latest_snippet": latest_snippet,
        "action_hint": action_hint,
        "action_hint_url": action_hint_url,
        "has_data": has_data,
        "has_pending_draft": has_pending_draft,
    }


def _lookup_company(domain: str) -> dict:
    """Return company record from companies.json, merged with overrides."""
    try:
        import json as _j2

        raw = _j2.loads(_COMPANIES_PATH.read_text())
        # Handle nested {"companies": {...}} structure
        data = raw.get("companies", raw) if isinstance(raw, dict) else raw
        record = dict(data.get(domain, {}))
        # Merge overrides (higher priority)
        overrides_path = _PROJECT_ROOT / "data" / "dataowners_overrides.json"
        if overrides_path.exists():
            overrides = _j2.loads(overrides_path.read_text())
            override = overrides.get(domain, {})
            if override:
                # Deep-merge contact dict
                if "contact" in override and "contact" in record:
                    merged_contact = dict(record["contact"])
                    for k, v in override["contact"].items():
                        if v:  # only override non-empty values
                            merged_contact[k] = v
                    record["contact"] = merged_contact
                # Shallow merge other top-level fields
                for k, v in override.items():
                    if k != "contact" and v:
                        record[k] = v
        return record
    except Exception:
        return {}


def _load_companies_db() -> dict:
    try:
        return json.loads(_COMPANIES_PATH.read_text()).get(
            "companies", json.loads(_COMPANIES_PATH.read_text())
        )
    except Exception:
        return {}


def _load_all_states(account: str) -> dict:
    """Return merged reply states: reply_state.json + any domains only in sent_letters.json.

    This is the single authoritative source of "all companies we have sent to",
    used by the dashboard, cards, and pipeline to ensure consistent counts.
    """
    states = load_state(account, path=_current_state_path())
    try:
        sent_log = get_log(data_dir=_current_data_dir())
        records_by_domain: dict[str, list[dict]] = {}
        for record in sent_log:
            d = domain_from_sent_record(record)
            if d:
                records_by_domain.setdefault(d, []).append(record)
        for d, records in records_by_domain.items():
            states[d] = promote_latest_attempt(
                domain=d,
                sent_records=records,
                existing_state=states.get(d),
                deadline_fn=deadline_from_sent,
            )
    except Exception:
        pass
    return states


# ---------------------------------------------------------------------------
# Context processor & template filter (plain functions — registered by app)
# ---------------------------------------------------------------------------


def inject_globals() -> dict:
    """Provide status colours, tag maps, and nav data to all templates."""
    import time as _time
    from flask import request as _req

    path = _req.path
    if path.startswith("/pipeline"):
        active_tab = "pipeline"
    elif path.startswith("/costs"):
        active_tab = "costs"
    elif path.startswith("/transfers"):
        active_tab = "transfers"
    else:
        active_tab = "dashboard"

    # Mailbox accounts -- available globally for navbar dropdown
    nav_accounts: list[str] = []
    nav_selected_account = ""
    if current_user.is_authenticated:
        nav_accounts = _get_accounts()
        nav_selected_account = _req.args.get("account", "")
        if not nav_selected_account and nav_accounts:
            nav_selected_account = nav_accounts[0]

    return {
        "status_colour": _STATUS_COLOUR,
        "tag_colour": _TAG_COLOUR,
        "tag_names": _DISPLAY_NAMES,
        "active_tab": active_tab,
        "now_ts": _time.time(),
        "nav_accounts": nav_accounts,
        "nav_selected_account": nav_selected_account,
    }


def flag_emoji_filter(cc: str) -> str:
    """Convert ISO 3166-1 alpha-2 country code to flag emoji."""
    if not cc or len(cc) != 2:
        return ""
    try:
        return "".join(chr(0x1F1E6 + ord(c) - ord("A")) for c in cc.upper())
    except Exception:
        return ""
