"""Flask dashboard for GDPR SAR monitoring.

Routes:
    GET /                       — account selector + all company cards
    GET /company/<domain>       — full reply thread for one company
    GET /data/<domain>          — data card (requires attachment received)
    GET /refresh?account=EMAIL  — run monitor inline, redirect to /
"""

from __future__ import annotations

import html as _html
import re
import sys
import urllib.parse as _urlparse
from datetime import datetime, timezone
from pathlib import Path

# Ensure project root is on path when run directly
_PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

# Load .env so ANTHROPIC_API_KEY is available for schema analysis
try:
    from dotenv import load_dotenv
    load_dotenv(_PROJECT_ROOT / ".env")
except ImportError:
    pass

from flask import Flask, Response, flash, jsonify, redirect, render_template, request, url_for

from letter_engine.tracker import get_log, _SUBPROCESSOR_REQUESTS_PATH
from reply_monitor.classifier import _ACTION_DRAFT_TAGS
from reply_monitor.state_manager import (
    _SUBPROCESSOR_STATE_PATH,
    _COMPANY_STATUS_PRIORITY,
    compute_company_status,
    compute_status,
    days_remaining,
    deadline_from_sent,
    domain_from_sent_record,
    load_state,
    promote_latest_attempt,
    save_state,
    status_sort_key,
)

# ---------------------------------------------------------------------------
# Snippet display helpers
# ---------------------------------------------------------------------------

def _clean_snippet(text: str) -> str:
    """Decode common encoding artifacts in Gmail snippets."""
    # HTML entities: &amp; → &, &nbsp; → space, &#160; → space, etc.
    text = _html.unescape(text)
    # MIME quoted-printable soft line breaks: =\r\n or =\n → space
    text = re.sub(r'=\r?\n', ' ', text)
    # QP-encoded chars: =3D → =, =20 → space, =E2=80=99 → ' etc.
    text = re.sub(r'=([0-9A-Fa-f]{2})', lambda m: chr(int(m.group(1), 16)), text)
    # URL-encoded fragments appearing as literal text: %20 → space, %3D → =
    text = re.sub(r'%([0-9A-Fa-f]{2})', lambda m: _urlparse.unquote('%' + m.group(1)), text)
    # Zero-width and invisible Unicode chars (newsletter spacers)
    text = re.sub(r'[\u200b\u200c\u200d\u2060\ufeff]', '', text)
    # Collapse multiple whitespace to single space
    text = re.sub(r'\s+', ' ', text).strip()
    return text


def _is_human_friendly(text: str) -> bool:
    """Return True if text contains no common encoding artifacts."""
    if re.search(r'&[a-z]+;|&#\d+;', text):    # HTML entities
        return False
    if re.search(r'=[0-9A-Fa-f]{2}', text):    # QP artifacts
        return False
    if re.search(r'%[0-9A-Fa-f]{2}', text):    # URL encoding
        return False
    return True


app = Flask(__name__, template_folder="templates")

_USER_DATA = _PROJECT_ROOT / "user_data"
_STATE_PATH = _USER_DATA / "reply_state.json"
_COMPANIES_PATH = _PROJECT_ROOT / "data" / "companies.json"

# ---------------------------------------------------------------------------
# Flask-Login & auth blueprints
# ---------------------------------------------------------------------------
from flask import g
from flask_login import LoginManager, current_user, login_required
from dashboard.user_model import (
    _safe_email,
    load_user as _load_user_by_email,
    user_data_dir,
    _safe_email_to_address as _safe_email_to_addr,
)
from dashboard.auth_routes import auth_bp
from dashboard.admin_routes import admin_bp

_SECRET_KEY_PATH = _USER_DATA / "secret_key.txt"
_USER_DATA.mkdir(parents=True, exist_ok=True)
if _SECRET_KEY_PATH.exists():
    app.config["SECRET_KEY"] = _SECRET_KEY_PATH.read_text().strip()
else:
    import secrets as _secrets
    _key = _secrets.token_hex(32)
    _SECRET_KEY_PATH.write_text(_key)
    app.config["SECRET_KEY"] = _key

app.config["USERS_PATH"] = _USER_DATA / "users.json"
app.config["USER_DATA_ROOT"] = _USER_DATA

login_manager = LoginManager(app)
login_manager.login_view = "auth.login"

app.register_blueprint(auth_bp)
app.register_blueprint(admin_bp)


@login_manager.user_loader
def _flask_load_user(email):
    users_path = app.config.get("USERS_PATH", _USER_DATA / "users.json")
    data_root = app.config.get("USER_DATA_ROOT", _USER_DATA)
    return _load_user_by_email(email, path=users_path, data_root=data_root)


@app.before_request
def _inject_user():
    """Set g.data_dir for authenticated users. Redirect to login for protected routes."""
    if request.endpoint and request.endpoint.startswith(("auth.", "static")):
        return
    if not current_user.is_authenticated:
        return login_manager.unauthorized()
    g.user = current_user
    g.data_dir = current_user.data_dir


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

# Extensibility hook: maps request type key → metadata
# Adding a new type = one entry here + template section
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
# Status → CSS colour class mapping
# ---------------------------------------------------------------------------
# Statuses where the GDPR deadline no longer applies — hide countdown
_TERMINAL_STATUSES = {"COMPLETED", "BOUNCED", "DENIED"}

_STATUS_COLOUR = {
    # Per-stream (request-level) statuses
    "OVERDUE":            "danger",
    "ACTION_REQUIRED":    "warning",
    "ADDRESS_NOT_FOUND":  "danger",
    "BOUNCED":            "secondary",
    "DENIED":             "secondary",
    "COMPLETED":          "success",
    "EXTENDED":           "warning",
    "ACKNOWLEDGED":       "info",
    "PENDING":            "primary",
    # Company-level (two-stream derived) statuses
    "STALLED":            "danger",
    "USER_REPLIED":       "primary",
    "DATA_RECEIVED":      "success",
    "FULLY_RESOLVED":     "success",
    "IN_PROGRESS":        "info",
    "SP_PENDING":         "primary",
}

_TAG_COLOUR = {
    "AUTO_ACKNOWLEDGE":      "info",
    "OUT_OF_OFFICE":         "secondary",
    "BOUNCE_PERMANENT":      "dark",
    "BOUNCE_TEMPORARY":      "dark",
    "CONFIRMATION_REQUIRED": "warning",
    "IDENTITY_REQUIRED":     "warning",
    "MORE_INFO_REQUIRED":    "warning",
    "WRONG_CHANNEL":         "warning",
    "HUMAN_REVIEW":          "warning",
    "REQUEST_ACCEPTED":      "success",
    "EXTENDED":              "warning",
    "IN_PROGRESS":           "info",
    "DATA_PROVIDED_LINK":    "success",
    "DATA_PROVIDED_ATTACHMENT": "success",
    "DATA_PROVIDED_PORTAL":  "success",
    "REQUEST_DENIED":        "danger",
    "NO_DATA_HELD":          "secondary",
    "NOT_GDPR_APPLICABLE":   "secondary",
    "FULFILLED_DELETION":    "success",
    "NON_GDPR":              "light",
}

# Friendly display names for tags shown in the UI.
# Internal/raw tag constants are stored in reply_state.json unchanged.
_DISPLAY_NAMES: dict[str, str] = {
    "AUTO_ACKNOWLEDGE":      "Acknowledged",
    "DATA_PROVIDED_LINK":    "Data Provided",
    "DATA_PROVIDED_ATTACHMENT": "Data Provided",
    "DATA_PROVIDED_PORTAL":  "Data Provided",
    "WRONG_CHANNEL":         "Wrong Channel",
    "HUMAN_REVIEW":          "Needs Review",
    "REQUEST_ACCEPTED":      "Accepted",
    "IN_PROGRESS":           "In Progress",
    "IDENTITY_REQUIRED":     "ID Required",
    "CONFIRMATION_REQUIRED": "Confirm Required",
    "MORE_INFO_REQUIRED":    "More Info Needed",
    "OUT_OF_OFFICE":         "Out of Office",
    "BOUNCE_PERMANENT":      "Bounced",
    "BOUNCE_TEMPORARY":      "Temp Bounce",
    "REQUEST_DENIED":        "Denied",
    "NO_DATA_HELD":          "No Data Held",
    "NOT_GDPR_APPLICABLE":   "Not Applicable",
    "FULFILLED_DELETION":    "Deleted",
    "EXTENDED":              "Extended",
    "NON_GDPR":              "Non-GDPR",
}

# ---------------------------------------------------------------------------
# Tag supersession — reduces noise on dashboard cards
# ---------------------------------------------------------------------------
# Tags are grouped into tiers; when a higher tier is present, lower-tier
# tags are hidden from the card display (but kept in per-reply detail view).

_TIER_TERMINAL: frozenset[str] = frozenset({
    "DATA_PROVIDED_LINK", "DATA_PROVIDED_ATTACHMENT", "DATA_PROVIDED_PORTAL",
    "REQUEST_DENIED", "NO_DATA_HELD", "NOT_GDPR_APPLICABLE", "FULFILLED_DELETION",
})
_TIER_ACTION: frozenset[str] = frozenset({
    "WRONG_CHANNEL", "IDENTITY_REQUIRED", "CONFIRMATION_REQUIRED",
    "MORE_INFO_REQUIRED", "HUMAN_REVIEW",
})
_TIER_PROGRESS: frozenset[str] = frozenset({
    "REQUEST_ACCEPTED", "IN_PROGRESS", "EXTENDED",
})
_TIER_NOISE: frozenset[str] = frozenset({"OUT_OF_OFFICE", "NON_GDPR"})


def _effective_tags(all_tags: set[str]) -> list[str]:
    """Return the minimal, meaningful set of tags to show on a dashboard card.

    Applies supersession so that higher-priority tags hide redundant lower-tier
    ones (e.g. DATA_PROVIDED hides REQUEST_ACCEPTED; WRONG_CHANNEL hides ACK).
    NON_GDPR and OUT_OF_OFFICE are always suppressed unless they are the only tag.
    """
    result = all_tags - _TIER_NOISE
    if not result:
        # Only noise tags — keep the set for the edge-case display
        return sorted(all_tags)
    if result & _TIER_TERMINAL:
        return sorted(result & _TIER_TERMINAL)
    if result & _TIER_ACTION:
        return sorted(result & _TIER_ACTION)
    if result & _TIER_PROGRESS:
        return sorted(result & _TIER_PROGRESS)
    # Tier 4 — informational only (AUTO_ACKNOWLEDGE, BOUNCE_*)
    return sorted(result)

_ACTION_HINTS = {
    "CONFIRMATION_REQUIRED": "Click the confirmation link in the email",
    "IDENTITY_REQUIRED":     "Submit identity proof as requested",
    "MORE_INFO_REQUIRED":    "Clarify your request as asked",
    "WRONG_CHANNEL":         "Re-submit via the channel they indicated",
    "BOUNCE_PERMANENT":      "Email address bounced — contact via web",
}


# ---------------------------------------------------------------------------
# Template globals
# ---------------------------------------------------------------------------

@app.context_processor
def _inject_globals():
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
    return {
        "status_colour": _STATUS_COLOUR,
        "tag_colour": _TAG_COLOUR,
        "tag_names": _DISPLAY_NAMES,
        "active_tab": active_tab,
        "now_ts": _time.time(),
    }


@app.template_filter("flag_emoji")
def _flag_emoji_filter(cc: str) -> str:
    """Convert ISO 3166-1 alpha-2 country code to flag emoji."""
    if not cc or len(cc) != 2:
        return ""
    try:
        return "".join(chr(0x1F1E6 + ord(c) - ord('A')) for c in cc.upper())
    except Exception:
        return ""


# ---------------------------------------------------------------------------
# Helpers
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


def _build_card(domain: str, state, status: str) -> dict:
    """Build a flat dict for the dashboard card template."""
    has_wrong_channel = any(
        "WRONG_CHANNEL" in r.tags
        for r in state.replies
        if "NON_GDPR" not in r.tags
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
        progress_colour = "danger" if remaining < 7 else "warning" if remaining < 14 else "success"

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
                    action_hint_url = r.extracted.get("portal_url") or r.extracted.get("confirmation_url", "")
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
    if status == "COMPLETED":
        # Check active replies first, then past attempts
        for r_info in all_gdpr_replies_dicts:
            r_tags = r_info.get("tags", []) if isinstance(r_info, dict) else r_info.tags
            r_extracted = r_info.get("extracted", {}) if isinstance(r_info, dict) else r_info.extracted
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

    has_data = status == "COMPLETED" and any(
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

    return {
        "domain": domain,
        "company_name": state.company_name,
        "status": status,
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
    """Return company record from companies.json for Company Info tab."""
    try:
        import json
        data = json.loads(_COMPANIES_PATH.read_text())
        companies = data.get("companies", data)
        return companies.get(domain, {})
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@app.route("/")
def dashboard():
    account = request.args.get("account", "")
    accounts = _get_accounts()

    if not account and accounts:
        account = accounts[0]

    cards = []
    if account:
        states = _load_all_states(account)

        # Load subprocessor reply states and sent domains for SP badges
        sp_states = load_state(account, path=_current_sp_state_path())
        sp_sent_domains: set[str] = {r.get("domain", "") for r in get_log(path=_current_sp_requests_path())}

        for domain, state in states.items():
            status = compute_status(state)
            card = _build_card(domain, state, status)
            card["sp_sent"] = domain in sp_sent_domains
            sp_state = sp_states.get(domain)
            card["sp_status"] = compute_status(sp_state) if sp_state else "PENDING"
            card["company_status"] = compute_company_status(
                status, card["sp_status"], card["sp_sent"]
            )
            cards.append(card)

        # Sort by company-level urgency
        cards.sort(key=lambda c: _COMPANY_STATUS_PRIORITY.get(c["company_status"], 0), reverse=True)

    scan_state = load_scan_state(account, data_dir=_current_data_dir()) if account else {}
    return render_template(
        "dashboard.html",
        cards=cards,
        accounts=accounts,
        selected_account=account,
        last_scan_at=scan_state.get("last_scan_at"),
    )


@app.route("/company/<domain>")
def company_detail(domain: str):
    account = request.args.get("account", "")
    if not account:
        accounts = _get_accounts()
        account = accounts[0] if accounts else ""

    states = _load_all_states(account)
    state = states.get(domain)
    if not state:
        return f"No data found for {domain}", 404

    status = compute_status(state)

    # Build per-reply data for template (excluding outgoing manual replies — shown in timeline only)
    reply_rows = []
    for r in reversed(state.replies):
        if "YOUR_REPLY" in r.tags:
            continue
        reply_rows.append({
            "received_at": r.received_at[:19].replace("T", " "),
            "from_addr": r.from_addr,
            "subject": r.subject,
            "snippet": _clean_snippet(r.snippet),
            "tags": r.tags,
            "extracted": r.extracted,
            "llm_used": r.llm_used,
            "has_attachment": r.has_attachment,
            "non_gdpr": "NON_GDPR" in r.tags,
            "gmail_message_id": r.gmail_message_id,
            "suggested_reply": r.suggested_reply,
            "reply_review_status": r.reply_review_status,
            "has_action_draft": bool(set(r.tags) & _ACTION_DRAFT_TAGS),
            "sent_reply_body": r.sent_reply_body,
            "sent_reply_at": r.sent_reply_at[:10] if r.sent_reply_at else "",
        })

    # Build past attempts for display — each with deduplicated address + reply summary
    past_attempts_display = []
    seen_pa_emails: set[str] = set()
    for pa in state.past_attempts:
        email = pa.get("to_email", "")
        if not email:
            continue
        pa_reply_rows = []
        for r in reversed(pa.get("replies", [])):
            pa_reply_rows.append({
                "received_at": r["received_at"][:19].replace("T", " "),
                "from_addr": r["from"],
                "subject": r["subject"],
                "snippet": _clean_snippet(r["snippet"]),
                "tags": r["tags"],
                "non_gdpr": "NON_GDPR" in r["tags"],
                "gmail_message_id": r["gmail_message_id"],
            })
        # Only include first occurrence of each address — later identical addresses
        # are duplicate retry entries, not a new service
        already_shown = email.lower() in seen_pa_emails
        seen_pa_emails.add(email.lower())
        past_attempts_display.append({
            "to_email": email,
            "sar_sent_at": pa.get("sar_sent_at", "")[:10],
            "reply_rows": pa_reply_rows,
            "duplicate": already_shown,
        })

    # Load subprocessor sent record and state for this domain
    sp_log = get_log(path=_current_sp_requests_path())
    sp_sent = next((r for r in sp_log if r.get("domain") == domain), None)
    sp_states = load_state(account, path=_current_sp_state_path())
    sp_state = sp_states.get(domain)
    sp_status = compute_status(sp_state) if sp_state else None

    sp_reply_rows = []
    if sp_state:
        for r in reversed(sp_state.replies):
            if "YOUR_REPLY" in r.tags:
                continue
            sp_reply_rows.append({
                "received_at": r.received_at[:19].replace("T", " "),
                "from_addr": r.from_addr,
                "subject": r.subject,
                "snippet": _clean_snippet(r.snippet),
                "tags": r.tags,
                "extracted": r.extracted,
                "llm_used": r.llm_used,
                "has_attachment": r.has_attachment,
                "non_gdpr": "NON_GDPR" in r.tags,
                "gmail_message_id": r.gmail_message_id,
                "suggested_reply": r.suggested_reply,
                "reply_review_status": r.reply_review_status,
                "has_action_draft": bool(set(r.tags) & _ACTION_DRAFT_TAGS),
                "sent_reply_body": r.sent_reply_body,
                "sent_reply_at": r.sent_reply_at[:10] if r.sent_reply_at else "",
            })

    # Deduplicate: when SAR and SP requests share the same inbox (e.g. support@company.com),
    # the address-search fallback in the SAR monitor picks up SP replies and stores them in
    # both state files. Filter them out of reply_rows so they only appear in the SP section.
    reply_rows = _dedup_reply_rows(reply_rows, sp_reply_rows)

    # All message IDs in SP stream (company + user replies) — used for SAR dedup.
    # Using the full set (not just sp_reply_rows) prevents duplicate YOUR_REPLY events
    # when the same manual reply is fetched by both SAR and SP monitors.
    sp_all_msg_ids = {r.gmail_message_id for r in (sp_state.replies if sp_state else [])}

    def _build_thread_event(r, to_addr: str, thread_id: str) -> dict:
        return {
            "type": "reply",
            "sort_key": r.received_at,
            "display_at": r.received_at[:16].replace("T", " "),
            "from_addr": r.from_addr,
            "subject": r.subject,
            "snippet": _clean_snippet(r.snippet),
            "tags": r.tags,
            "extracted": r.extracted,
            "llm_used": r.llm_used,
            "has_attachment": r.has_attachment,
            "non_gdpr": "NON_GDPR" in r.tags,
            "gmail_message_id": r.gmail_message_id,
            "suggested_reply": r.suggested_reply,
            "reply_review_status": r.reply_review_status,
            "has_action_draft": bool(set(r.tags) & _ACTION_DRAFT_TAGS),
            "sent_reply_body": r.sent_reply_body,
            "sent_reply_at": r.sent_reply_at[:16].replace("T", " ") if r.sent_reply_at else "",
            "to_addr": to_addr,
            "thread_id": thread_id,
        }

    # ── SAR thread (oldest first) ─────────────────────────────────────────────
    sar_thread: list[dict] = [{
        "type": "sent",
        "sort_key": state.sar_sent_at,
        "display_at": state.sar_sent_at[:16].replace("T", " "),
        "to_email": state.to_email,
        "subject": state.subject,
    }]
    for r in state.replies:
        if r.gmail_message_id in sp_all_msg_ids:
            continue  # SP is authoritative for messages fetched by both monitors
        if "YOUR_REPLY" in r.tags:
            sar_thread.append({
                "type": "your_reply",
                "sort_key": r.received_at,
                "display_at": r.received_at[:16].replace("T", " "),
                "body": r.snippet,
            })
            continue
        ev = _build_thread_event(r, state.to_email, "")
        sar_thread.append(ev)
        if r.reply_review_status == "sent" and r.sent_reply_body:
            sar_thread.append({
                "type": "your_reply",
                "sort_key": r.sent_reply_at or r.received_at,
                "display_at": r.sent_reply_at[:16].replace("T", " ") if r.sent_reply_at else "",
                "body": r.sent_reply_body,
            })
    sar_thread.sort(key=lambda e: e["sort_key"])

    # ── SP thread (oldest first) ──────────────────────────────────────────────
    sp_thread: list[dict] = []
    if sp_sent:
        sp_thread.append({
            "type": "sent",
            "sort_key": sp_sent.get("sent_at", ""),
            "display_at": sp_sent.get("sent_at", "")[:16].replace("T", " "),
            "to_email": sp_sent.get("to", ""),
            "subject": sp_sent.get("subject", "Subprocessor Disclosure Request"),
        })
    if sp_state:
        _sp_tid = sp_state.gmail_thread_id
        _sp_to = sp_sent.get("to", "") if sp_sent else ""
        for r in sp_state.replies:
            if "YOUR_REPLY" in r.tags:
                sp_thread.append({
                    "type": "your_reply",
                    "sort_key": r.received_at,
                    "display_at": r.received_at[:16].replace("T", " "),
                    "body": r.snippet,
                })
                continue
            ev = _build_thread_event(r, _sp_to, _sp_tid)
            sp_thread.append(ev)
            if r.reply_review_status == "sent" and r.sent_reply_body:
                sp_thread.append({
                    "type": "your_reply",
                    "sort_key": r.sent_reply_at or r.received_at,
                    "display_at": r.sent_reply_at[:16].replace("T", " ") if r.sent_reply_at else "",
                    "body": r.sent_reply_body,
                })
        sp_thread.sort(key=lambda e: e["sort_key"])

    # ── Summary lines ("why is it this status?") ─────────────────────────────
    company_status = compute_company_status(
        sar_status=status,
        sp_status=sp_status,
        sp_sent=bool(sp_sent),
    )
    sar_days_left = days_remaining(state.sar_sent_at)

    # Look up privacy policy URL from companies.json
    company_record = _lookup_company(domain)
    contact_info = company_record.get("contact", {})
    privacy_policy_url = contact_info.get("privacy_policy_url", "")

    return render_template(
        "company_detail.html",
        domain=domain,
        state=state,
        status=status,
        status_colour=_STATUS_COLOUR.get(status, "secondary"),
        reply_rows=reply_rows,
        past_attempts=past_attempts_display,
        account=account,
        sp_sent=sp_sent,
        sp_state=sp_state,
        sp_status=sp_status,
        sp_status_colour=_STATUS_COLOUR.get(sp_status, "secondary") if sp_status else "",
        sp_reply_rows=sp_reply_rows,
        sar_thread=sar_thread,
        sp_thread=sp_thread,
        company_status=company_status,
        company_status_colour=_STATUS_COLOUR.get(company_status, "secondary"),
        sar_days_left=sar_days_left,
        privacy_policy_url=privacy_policy_url,
    )


@app.route("/company/<domain>/send-followup", methods=["POST"])
def send_followup(domain: str):
    account = request.form.get("account", "")
    gmail_message_id = request.form.get("gmail_message_id", "")
    reply_body = request.form.get("reply_body", "").strip()
    to_addr = request.form.get("to_addr", "")
    subject = request.form.get("subject", "")

    from letter_engine.sender import send_thread_reply

    states = load_state(account, path=_current_state_path())
    state = states.get(domain)
    if not state:
        flash("Company not found.", "danger")
        return redirect(url_for("company_detail", domain=domain, account=account))

    reply = next((r for r in state.replies if r.gmail_message_id == gmail_message_id), None)
    if not reply:
        flash("Reply not found.", "danger")
        return redirect(url_for("company_detail", domain=domain, account=account))

    success, _msg_id, _thread_id = send_thread_reply(
        state.gmail_thread_id, to_addr, subject, reply_body, account,
        tokens_dir=_current_tokens_dir(),
    )
    if success:
        reply.reply_review_status = "sent"
        reply.sent_reply_body = reply_body
        reply.sent_reply_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        save_state(account, states, path=_current_state_path())
        flash("Follow-up reply sent.", "success")
    else:
        flash("Failed to send follow-up reply. Check Gmail auth.", "danger")

    return redirect(url_for("company_detail", domain=domain, account=account))


@app.route("/company/<domain>/dismiss-followup", methods=["POST"])
def dismiss_followup(domain: str):
    account = request.form.get("account", "")
    gmail_message_id = request.form.get("gmail_message_id", "")

    states = load_state(account, path=_current_state_path())
    state = states.get(domain)
    if state:
        reply = next((r for r in state.replies if r.gmail_message_id == gmail_message_id), None)
        if reply:
            reply.reply_review_status = "dismissed"
            save_state(account, states, path=_current_state_path())

    return redirect(url_for("company_detail", domain=domain, account=account))


@app.route("/company/<domain>/send-sp-followup", methods=["POST"])
def send_sp_followup(domain: str):
    account = request.form.get("account", "")
    gmail_message_id = request.form.get("gmail_message_id", "")
    reply_body = request.form.get("reply_body", "").strip()
    to_addr = request.form.get("to_addr", "")
    subject = request.form.get("subject", "")
    thread_id = request.form.get("thread_id", "")

    from letter_engine.sender import send_thread_reply

    sp_states = load_state(account, path=_current_sp_state_path())
    state = sp_states.get(domain)
    if not state:
        flash("SP state not found.", "danger")
        return redirect(url_for("company_detail", domain=domain, account=account))

    reply = next((r for r in state.replies if r.gmail_message_id == gmail_message_id), None)
    if not reply:
        flash("Reply not found.", "danger")
        return redirect(url_for("company_detail", domain=domain, account=account))

    success, _msg_id, _thread_id = send_thread_reply(
        thread_id or state.gmail_thread_id, to_addr, subject, reply_body, account,
        tokens_dir=_current_tokens_dir(),
    )
    if success:
        reply.reply_review_status = "sent"
        reply.sent_reply_body = reply_body
        reply.sent_reply_at = datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
        save_state(account, sp_states, path=_current_sp_state_path())
        flash("Follow-up reply sent.", "success")
    else:
        flash("Failed to send follow-up reply. Check Gmail auth.", "danger")

    return redirect(url_for("company_detail", domain=domain, account=account))


@app.route("/company/<domain>/dismiss-sp-followup", methods=["POST"])
def dismiss_sp_followup(domain: str):
    account = request.form.get("account", "")
    gmail_message_id = request.form.get("gmail_message_id", "")

    sp_states = load_state(account, path=_current_sp_state_path())
    state = sp_states.get(domain)
    if state:
        reply = next((r for r in state.replies if r.gmail_message_id == gmail_message_id), None)
        if reply:
            reply.reply_review_status = "dismissed"
            save_state(account, sp_states, path=_current_sp_state_path())

    return redirect(url_for("company_detail", domain=domain, account=account))


@app.route("/data/<domain>")
def data_card(domain: str):
    account = request.args.get("account", "")
    scan_error = request.args.get("scan_error", "")
    download_error = request.args.get("download_error", "")
    tab = request.args.get("tab", "data_description")
    if not account:
        accounts = _get_accounts()
        account = accounts[0] if accounts else ""

    states = _load_all_states(account)
    state = states.get(domain)
    if not state:
        return f"No data found for {domain}", 404

    catalog = None
    received_at = ""
    data_link = ""

    for r in state.replies:
        if "NON_GDPR" in r.tags:
            continue
        # Attachment catalog (from email attachment or post-scan)
        if r.attachment_catalog and not catalog:
            catalog = r.attachment_catalog
            received_at = r.received_at[:10]
        # Link-based data
        if "DATA_PROVIDED_LINK" in r.tags:
            if not data_link:
                data_link = r.extracted.get("data_link", "")
            if not received_at:
                received_at = r.received_at[:10]

    # If active replies have no catalog/link, check past attempts (happens when a new SAR
    # was sent after data was already received — the old replies are archived to past_attempts)
    if not catalog and not data_link:
        for pa in state.past_attempts:
            for r_dict in pa.get("replies", []):
                if "NON_GDPR" in r_dict.get("tags", []):
                    continue
                if r_dict.get("attachment_catalog") and not catalog:
                    catalog = r_dict["attachment_catalog"]
                    received_at = r_dict.get("received_at", "")[:10]
                if "DATA_PROVIDED_LINK" in r_dict.get("tags", []):
                    if not data_link:
                        data_link = (r_dict.get("extracted") or {}).get("data_link", "")
                    if not received_at:
                        received_at = r_dict.get("received_at", "")[:10]

    # Enrich catalog dict with received_at for template display
    if catalog and isinstance(catalog, dict) and not catalog.get("received_at"):
        catalog = dict(catalog)
        catalog["received_at"] = received_at

    # Check if a file was manually saved to the received folder
    received_dir = _USER_DATA / "received" / domain
    has_local_files = received_dir.exists() and any(
        f.suffix.lower().lstrip(".") in ("zip", "json", "csv")
        for f in received_dir.iterdir()
        if f.is_file()
    ) if received_dir.exists() else False

    folder_path = str(received_dir) if catalog else ""

    # Company info from companies.json
    company_record = _lookup_company(domain)
    legal_name = company_record.get("legal_entity_name", "")
    postal = company_record.get("postal_address", {}) or {}
    city = postal.get("city", "")
    country = postal.get("country", "")
    headquarters = ", ".join(filter(None, [city, country]))
    dpo_email = company_record.get("dpo_email", "") or company_record.get("privacy_email", "")
    portal_url = company_record.get("gdpr_portal_url", "")
    request_notes = company_record.get("request_notes", {}) or {}
    response_time_days = request_notes.get("known_response_time_days")
    identity_required = request_notes.get("identity_verification_required", False)
    special_instructions = request_notes.get("special_instructions", "")

    return render_template(
        "data_card.html",
        domain=domain,
        company_name=state.company_name,
        received_at=received_at,
        data_link=data_link,
        catalog=catalog,
        folder_path=folder_path,
        account=account,
        scan_error=scan_error,
        download_error=download_error,
        has_local_files=has_local_files,
        to_email=state.to_email,
        sar_sent_at=state.sar_sent_at[:10],
        tab=tab,
        legal_name=legal_name,
        headquarters=headquarters,
        dpo_email=dpo_email,
        portal_url=portal_url,
        response_time_days=response_time_days,
        identity_required=identity_required,
        special_instructions=special_instructions,
    )


@app.route("/cards")
def cards_listing():
    """Two-tab listing: companies with data vs. without."""
    account = request.args.get("account", "")
    tab = request.args.get("tab", "with_data")
    accounts = _get_accounts()

    if not account and accounts:
        account = accounts[0]

    with_data = []
    without_data = []

    if account:
        states = _load_all_states(account)
        sp_states = load_state(account, path=_current_sp_state_path())
        sp_sent_domains: set[str] = {r.get("domain", "") for r in get_log(path=_current_sp_requests_path())}

        for domain, state in states.items():
            status = compute_status(state)
            sp_state = sp_states.get(domain)
            sp_status = compute_status(sp_state) if sp_state else "PENDING"
            sp_sent = domain in sp_sent_domains
            company_status = compute_company_status(status, sp_status, sp_sent)
            # Find best catalog across all replies (active + past attempts)
            best_catalog = None
            received_at = ""
            _SKIP_TAGS = {"NON_GDPR", "BOUNCE_PERMANENT", "BOUNCE_SOFT"}
            for r in state.replies:
                if _SKIP_TAGS & set(r.tags):
                    continue
                if r.attachment_catalog and not best_catalog:
                    best_catalog = r.attachment_catalog
                    received_at = r.received_at[:10]
            # Fall back to past_attempts if active replies have no catalog
            if not best_catalog:
                for pa in state.past_attempts:
                    for r_dict in pa.get("replies", []):
                        if _SKIP_TAGS & set(r_dict.get("tags", [])):
                            continue
                        if r_dict.get("attachment_catalog") and not best_catalog:
                            best_catalog = r_dict["attachment_catalog"]
                            received_at = r_dict.get("received_at", "")[:10]

            if best_catalog and (best_catalog.get("files") or best_catalog.get("schema")):
                schema = best_catalog.get("schema", []) if isinstance(best_catalog, dict) else []
                svc_list = best_catalog.get("services", []) if isinstance(best_catalog, dict) else []
                files = best_catalog.get("files", []) if isinstance(best_catalog, dict) else []
                total_bytes = best_catalog.get("size_bytes", 0) if isinstance(best_catalog, dict) else 0
                with_data.append({
                    "domain": domain,
                    "company_name": state.company_name,
                    "category_count": len(schema),
                    "file_count": len(files),
                    "size_bytes": total_bytes,
                    "received_at": received_at,
                    "services_preview": [s["name"] for s in svc_list[:2]],
                })
            else:
                card = _build_card(domain, state, status)
                without_data.append({
                    "domain": domain,
                    "company_name": state.company_name,
                    "status": status,
                    "company_status": company_status,
                    "status_color": _STATUS_COLOUR.get(company_status, "secondary"),
                    "days_remaining": card["remaining"],
                    "latest_tag": card["tags"][0] if card["tags"] else "",
                    "tried_emails": card["tried_emails"],
                })

    return render_template(
        "cards.html",
        with_data=with_data,
        without_data=without_data,
        tab=tab,
        account=account,
        accounts=accounts,
    )


@app.route("/scan/<domain>")
def scan_folder(domain: str):
    """Scan user_data/received/<domain>/ for downloaded data files, catalog + LLM-analyze."""
    import os

    account = request.args.get("account", "")
    if not account:
        accounts = _get_accounts()
        account = accounts[0] if accounts else ""

    received_dir = _USER_DATA / "received" / domain
    if not received_dir.exists():
        return redirect(url_for("data_card", domain=domain, account=account, scan_error="no_files"))

    candidates = sorted(
        [
            f for f in received_dir.iterdir()
            if f.is_file() and f.suffix.lower().lstrip(".") in ("zip", "json", "csv", "js")
        ],
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        return redirect(url_for("data_card", domain=domain, account=account, scan_error="no_files"))

    from reply_monitor.attachment_handler import _catalog_csv, _catalog_json, _catalog_zip
    from reply_monitor.models import AttachmentCatalog, FileEntry

    file_path = candidates[0]
    ext = file_path.suffix.lstrip(".").lower()
    data = file_path.read_bytes()

    if ext == "zip":
        files, categories = _catalog_zip(data, file_path.name)
    elif ext in ("json", "js"):
        files, categories = _catalog_json(data, file_path.name, len(data))
    elif ext == "csv":
        files, categories = _catalog_csv(data, file_path.name, len(data))
    else:
        files = [FileEntry(filename=file_path.name, size_bytes=len(data), file_type=ext or "bin")]
        categories = []

    # LLM schema analysis
    schema: list[dict] = []
    services: list[dict] = []
    export_meta: dict = {}
    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if api_key:
        from reply_monitor.schema_builder import build_schema
        try:
            result = build_schema(file_path, api_key, company_name=domain)
            if result:
                schema = result.get("categories", [])
                services = result.get("services", [])
                export_meta = result.get("export_meta", {})
        except Exception as exc:
            print(f"[scan] Schema analysis failed for {domain}: {exc}")

    # If LLM returned schema categories use them; otherwise fall back to filename heuristics
    display_categories = [s["name"] for s in schema] if schema else sorted(set(categories))

    catalog = AttachmentCatalog(
        path=str(file_path),
        size_bytes=len(data),
        file_type=ext,
        files=files,
        categories=display_categories,
        schema=schema,
        services=services,
        export_meta=export_meta,
    )

    # Persist catalog to state
    states = load_state(account, path=_current_state_path())
    state = states.get(domain)
    if state:
        target = next(
            (r for r in state.replies if any(t.startswith("DATA_PROVIDED") for t in r.tags)),
            state.replies[-1] if state.replies else None,
        )
        if target:
            target.attachment_catalog = catalog.to_dict()
            save_state(account, states, path=_current_state_path())

    return redirect(url_for("data_card", domain=domain, account=account))


@app.route("/download/<domain>")
def download_data(domain: str):
    """Trigger link_downloader for a domain, update state, redirect to data card."""
    account = request.args.get("account", "")
    if not account:
        accounts = _get_accounts()
        account = accounts[0] if accounts else ""

    from reply_monitor.link_downloader import download_data_link

    states = load_state(account, path=_current_state_path())
    state = states.get(domain)
    if not state:
        return f"No data found for {domain}", 404

    # Find reply with a populated data_link
    target_reply = None
    for r in state.replies:
        if "DATA_PROVIDED_LINK" in r.tags and r.extracted.get("data_link"):
            target_reply = r
            break

    if not target_reply:
        return redirect(url_for("data_card", domain=domain, account=account))

    result = download_data_link(target_reply.extracted["data_link"], domain, api_key=os.environ.get("ANTHROPIC_API_KEY") or "")

    if result.ok:
        target_reply.attachment_catalog = result.catalog.to_dict()
        save_state(account, states, path=_current_state_path())
        return redirect(url_for("data_card", domain=domain, account=account))

    if result.too_large:
        error = "too_large"
    elif result.expired:
        error = "expired"
    else:
        error = result.error[:100]
    return redirect(url_for("data_card", domain=domain, account=account, download_error=error))


@app.route("/reextract")
def reextract():
    """Re-fetch Gmail bodies for replies with DATA_PROVIDED_LINK but empty data_link."""
    account = request.args.get("account", "")
    if not account:
        accounts = _get_accounts()
        account = accounts[0] if accounts else ""
    if account:
        try:
            _reextract_missing_links(account)
        except Exception as exc:
            print(f"[reextract] Error for {account}: {exc}")
    return redirect(url_for("dashboard", account=account))


@app.route("/refresh")
def refresh():
    """Run monitor inline for the selected account and redirect to dashboard."""
    account = request.args.get("account", "")
    if account:
        _service = None
        _email = ""
        try:
            _service, _email = _run_monitor_for_account(account)
        except Exception as exc:
            print(f"[refresh] Monitor error for {account}: {exc}")
        try:
            _run_subprocessor_monitor_for_account(account, _service=_service, _email=_email)
        except Exception as exc:
            print(f"[refresh] Subprocessor monitor error for {account}: {exc}")
        try:
            _reextract_missing_links(account)
        except Exception as exc:
            print(f"[refresh] Re-extract error for {account}: {exc}")
    return redirect(url_for("dashboard", account=account))


def _reextract_missing_links(account: str) -> int:
    """Re-fetch Gmail message bodies for replies that have DATA_PROVIDED_LINK
    but an empty data_link extracted field, then re-run URL extraction.

    Returns count of records updated.
    """
    from auth.gmail_oauth import get_gmail_service
    from reply_monitor.classifier import reextract_data_links
    from reply_monitor.fetcher import _extract_body

    states = load_state(account, path=_current_state_path())
    needs_update = False
    for domain, state in states.items():
        for reply in state.replies:
            if "DATA_PROVIDED_LINK" in reply.tags and not reply.extracted.get("data_link"):
                try:
                    service, _email = get_gmail_service(email_hint=account, tokens_dir=_current_tokens_dir())
                    msg = service.users().messages().get(
                        userId="me",
                        id=reply.gmail_message_id,
                        format="full",
                    ).execute()
                    body = _extract_body(msg.get("payload", {}))
                    new_extracted = reextract_data_links(reply.to_dict(), body)
                    if new_extracted.get("data_link"):
                        reply.extracted = new_extracted
                        needs_update = True
                except Exception as exc:
                    print(f"[reextract] {domain}/{reply.gmail_message_id}: {exc}")

    if needs_update:
        save_state(account, states, path=_current_state_path())
        # Reload updated state and trigger auto-download for any newly extracted URLs
        import os
        states = load_state(account, path=_current_state_path())
        _auto_download_data_links(account, states, os.environ.get("ANTHROPIC_API_KEY"))

    return sum(
        1 for state in states.values()
        for r in state.replies
        if "DATA_PROVIDED_LINK" in r.tags and r.extracted.get("data_link")
    )


def _run_monitor_for_account(account: str):
    """Inline monitor run — same logic as monitor.py but without printing.

    Returns (service, email) so callers can reuse the authenticated service.
    """
    from auth.gmail_oauth import get_gmail_service
    from reply_monitor.attachment_handler import handle_attachment
    from reply_monitor.classifier import classify
    from reply_monitor.fetcher import fetch_replies_for_sar
    from reply_monitor.models import ReplyRecord
    from reply_monitor.state_manager import (
        deadline_from_sent,
        domain_from_sent_record,
        promote_latest_attempt,
        update_state,
    )

    service, email = get_gmail_service(email_hint=account, tokens_dir=_current_tokens_dir())
    sent_log = get_log(data_dir=_current_data_dir())
    states = load_state(email, path=_current_state_path())

    api_key = None
    try:
        import os
        api_key = os.environ.get("ANTHROPIC_API_KEY")
    except Exception:
        pass

    # Group by domain and promote the most recent attempt to active state
    records_by_domain: dict[str, list[dict]] = {}
    for record in sent_log:
        d = domain_from_sent_record(record)
        records_by_domain.setdefault(d, []).append(record)
    for d, records in records_by_domain.items():
        states[d] = promote_latest_attempt(
            domain=d,
            sent_records=records,
            existing_state=states.get(d),
            deadline_fn=deadline_from_sent,
        )

    for domain, records in records_by_domain.items():
        latest_record = max(records, key=lambda r: r.get("sent_at", ""))

        state = states[domain]
        existing_ids = {r.gmail_message_id for r in state.replies}
        new_messages = fetch_replies_for_sar(service, latest_record, existing_ids, user_email=email)

        new_replies: list[ReplyRecord] = []
        for msg in new_messages:
            result = classify(msg, api_key=api_key)
            catalog_dict = None
            if msg.get("has_attachment"):
                for part in msg.get("parts", []):
                    cat = handle_attachment(service, msg["id"], part, domain)
                    if cat:
                        catalog_dict = cat.to_dict()
                        break
            new_replies.append(ReplyRecord(
                gmail_message_id=msg["id"],
                received_at=msg["received_at"],
                from_addr=msg["from"],
                subject=msg["subject"],
                snippet=msg["snippet"],
                tags=result.tags,
                extracted=result.extracted,
                llm_used=result.llm_used,
                has_attachment=msg["has_attachment"],
                attachment_catalog=catalog_dict,
            ))

        if new_replies:
            states[domain] = update_state(state, new_replies)

    save_state(email, states, path=_current_state_path())

    # Auto-download any DATA_PROVIDED_LINK replies that have a URL but no catalog yet
    _auto_download_data_links(email, states, api_key)

    return service, email


def _run_subprocessor_monitor_for_account(
    account: str,
    _service=None,
    _email: str = "",
) -> None:
    """Poll Gmail for replies to subprocessor disclosure requests (inline, no print).

    Pass _service and _email (returned by _run_monitor_for_account) to reuse an
    already-authenticated Gmail service and avoid a second OAuth prompt.
    """
    from reply_monitor.classifier import classify
    from reply_monitor.fetcher import fetch_replies_for_sar
    from reply_monitor.models import ReplyRecord
    from reply_monitor.state_manager import (
        deadline_from_sent,
        promote_latest_attempt,
        update_state,
    )

    log = get_log(path=_current_sp_requests_path())
    if not log:
        return

    if _service is not None and _email:
        service, email = _service, _email
    else:
        from auth.gmail_oauth import get_gmail_service
        service, email = get_gmail_service(email_hint=account, tokens_dir=_current_tokens_dir())
    sp_states = load_state(email, path=_current_sp_state_path())

    import os as _os
    api_key = _os.environ.get("ANTHROPIC_API_KEY")

    by_domain: dict[str, list[dict]] = {}
    for rec in log:
        domain = rec.get("domain", "")
        if domain:
            by_domain.setdefault(domain, []).append(rec)

    for domain, records in by_domain.items():
        sp_states[domain] = promote_latest_attempt(
            domain=domain,
            sent_records=records,
            existing_state=sp_states.get(domain),
            deadline_fn=deadline_from_sent,
        )
        state = sp_states[domain]
        existing_ids = {r.gmail_message_id for r in state.replies}
        for pa in state.past_attempts:
            for r in pa.get("replies", []):
                existing_ids.add(r["gmail_message_id"])

        latest_record = max(records, key=lambda r: r.get("sent_at", ""))
        new_messages = fetch_replies_for_sar(service, latest_record, existing_ids, user_email=email)

        new_replies: list[ReplyRecord] = []
        for msg in new_messages:
            result = classify(msg, api_key=api_key)
            new_replies.append(ReplyRecord(
                gmail_message_id=msg["id"],
                received_at=msg["received_at"],
                from_addr=msg["from"],
                subject=msg["subject"],
                snippet=msg["snippet"],
                tags=result.tags,
                extracted=result.extracted,
                llm_used=result.llm_used,
                has_attachment=msg["has_attachment"],
            ))

        if new_replies:
            sp_states[domain] = update_state(state, new_replies)

    save_state(email, sp_states, path=_current_sp_state_path())


def _auto_download_data_links(account: str, states: dict, api_key: str | None) -> None:
    """For every DATA_PROVIDED_LINK reply with a data_link but no attachment_catalog,
    automatically download and catalog the file (uses Playwright to bypass Cloudflare)."""
    from reply_monitor.link_downloader import download_data_link

    needs_save = False
    for domain, state in states.items():
        for reply in state.replies:
            if (
                "DATA_PROVIDED_LINK" in reply.tags
                and reply.extracted.get("data_link")
                and not reply.attachment_catalog
            ):
                url = reply.extracted["data_link"]
                print(f"[auto-download] {domain}: downloading data from {url[:60]}…")
                try:
                    result = download_data_link(url, domain, api_key=api_key or "")
                    if result.ok:
                        reply.attachment_catalog = result.catalog.to_dict()
                        needs_save = True
                        print(f"[auto-download] {domain}: ✓ {len(result.catalog.files)} files, "
                              f"{len(result.catalog.schema)} schema categories")
                    else:
                        print(f"[auto-download] {domain}: ✗ {result.error or 'expired' if result.expired else result.error}")
                except Exception as exc:
                    print(f"[auto-download] {domain}: exception — {exc}")

    if needs_save:
        save_state(account, states, path=_current_state_path())



@app.route("/costs")
def costs():
    """LLM cost history and calculator."""
    from contact_resolver import cost_tracker

    records = cost_tracker.load_persistent_log()

    # Aggregate by model
    model_totals: dict[str, dict] = {}
    for r in records:
        m = r.get("model", "unknown")
        if m not in model_totals:
            model_totals[m] = {"calls": 0, "input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0}
        model_totals[m]["calls"] += 1
        model_totals[m]["input_tokens"] += r.get("input_tokens", 0)
        model_totals[m]["output_tokens"] += r.get("output_tokens", 0)
        model_totals[m]["cost_usd"] += r.get("cost_usd", 0.0)

    # Aggregate by purpose/source
    source_totals: dict[str, dict] = {}
    for r in records:
        src = r.get("purpose") or r.get("source") or "unknown"
        if src not in source_totals:
            source_totals[src] = {"calls": 0, "cost_usd": 0.0}
        source_totals[src]["calls"] += 1
        source_totals[src]["cost_usd"] += r.get("cost_usd", 0.0)

    # Compute averages per call for calculator defaults
    avg_resolver = 0.025
    avg_classifier = 0.010
    avg_schema = 0.080
    avg_subprocessor = 0.030

    resolver_calls = [r for r in records if "contact" in (r.get("purpose") or r.get("source") or "")]
    classifier_calls = [r for r in records if "classif" in (r.get("purpose") or r.get("source") or "")]
    schema_calls = [r for r in records if "schema" in (r.get("purpose") or r.get("source") or "")]
    subprocessor_calls = [r for r in records if "subprocessor" in (r.get("purpose") or r.get("source") or "")]

    if resolver_calls:
        avg_resolver = sum(r["cost_usd"] for r in resolver_calls) / len(resolver_calls)
    if classifier_calls:
        avg_classifier = sum(r["cost_usd"] for r in classifier_calls) / len(classifier_calls)
    if schema_calls:
        avg_schema = sum(r["cost_usd"] for r in schema_calls) / len(schema_calls)
    if subprocessor_calls:
        avg_subprocessor = sum(r["cost_usd"] for r in subprocessor_calls) / len(subprocessor_calls)

    grand_total = sum(r.get("cost_usd", 0.0) for r in records)

    return render_template(
        "costs.html",
        records=list(reversed(records[-200:])),  # most recent first, cap display
        model_totals=model_totals,
        source_totals=source_totals,
        grand_total=grand_total,
        total_calls=len(records),
        avg_resolver=avg_resolver,
        avg_classifier=avg_classifier,
        avg_schema=avg_schema,
        avg_subprocessor=avg_subprocessor,
    )


@app.route("/transfers")
def transfers():
    """Data Transfer Map — shows subprocessors for all SAR companies."""
    account = request.args.get("account", "")
    accounts = _get_accounts()
    if not account and accounts:
        account = accounts[0]

    import json as _j
    try:
        raw = _j.loads(_COMPANIES_PATH.read_text())
        companies_raw = raw.get("companies", raw)
    except Exception:
        companies_raw = {}

    # Build rows for companies we've sent SARs to
    states = _load_all_states(account) if account else {}

    # Load subprocessor request log for "already sent" badges and reply state for live status
    from letter_engine.tracker import get_log as _get_letter_log
    _req_log = _get_letter_log(path=_current_sp_requests_path())
    requested_domains: set[str] = {r.get("domain", "") for r in _req_log}
    sp_states = load_state(account, path=_current_sp_state_path()) if account else {}

    rows = []
    for domain in states:
        company_raw = companies_raw.get(domain, {})
        company_name = company_raw.get("company_name") or domain
        sp_record = company_raw.get("subprocessors")
        contact = company_raw.get("contact", {})
        has_email = bool(contact.get("privacy_email") or contact.get("dpo_email"))
        # SP reply state for live status badge and reply mini-section
        sp_state = sp_states.get(domain)
        sp_status = compute_status(sp_state) if sp_state else None
        sp_replies = []
        if sp_state and sp_state.replies:
            for r in sp_state.replies:
                sp_replies.append({
                    "received_at": r.received_at,
                    "tags": r.tags,
                    "snippet": r.snippet,
                    "gmail_message_id": r.gmail_message_id,
                })
        rows.append({
            "domain": domain,
            "company_name": company_name,
            "subprocessors": sp_record,
            "has_email": has_email,
            "request_sent": domain in requested_domains,
            "sp_status": sp_status,
            "sp_replies": sp_replies,
        })
    rows.sort(key=lambda r: r["company_name"].lower())

    running_task = find_running_task("subprocessors")
    running_request_task = find_running_task("subprocessor_requests")
    return render_template(
        "transfers.html",
        rows=rows,
        account=account,
        accounts=accounts,
        running_task=running_task,
        running_request_task=running_request_task,
    )


@app.route("/transfers/fetch", methods=["POST"])
def transfers_fetch():
    """Start background task to fetch subprocessors for all SAR domains."""
    account = request.form.get("account", "")
    if not account:
        accounts = _get_accounts()
        account = accounts[0] if accounts else ""

    if find_running_task("subprocessors"):
        return redirect(url_for("transfers", account=account))

    task_id = start_task("subprocessors", _fetch_all_subprocessors, account)
    return redirect(url_for("transfers", account=account))


@app.route("/transfers/request-letter/<path:domain>", methods=["POST"])
def transfers_request_letter(domain: str):
    """Compose and send a subprocessor disclosure request letter for one company."""
    account = request.form.get("account", "")
    if not account:
        accounts = _get_accounts()
        account = accounts[0] if accounts else ""

    import json as _j
    try:
        raw = _j.loads(_COMPANIES_PATH.read_text())
        companies_raw = raw.get("companies", raw)
    except Exception:
        companies_raw = {}

    company_raw = companies_raw.get(domain)
    if not company_raw:
        flash(f"No company record for {domain}.", "warning")
        return redirect(url_for("transfers", account=account))

    from contact_resolver.models import CompanyRecord
    from letter_engine.composer import compose_subprocessor_request
    from letter_engine.sender import send_letter
    from letter_engine.tracker import record_subprocessor_request

    try:
        record = CompanyRecord.model_validate(company_raw)
    except Exception:
        flash(f"Could not load company record for {domain}.", "danger")
        return redirect(url_for("transfers", account=account))

    letter = compose_subprocessor_request(record)
    if not letter:
        flash(f"No email contact found for {domain}.", "warning")
        return redirect(url_for("transfers", account=account))

    success, msg_id, thread_id = send_letter(
        letter, account, record=False, tokens_dir=_current_tokens_dir(),
    )
    if success:
        letter.gmail_message_id = msg_id
        letter.gmail_thread_id = thread_id
        record_subprocessor_request(letter, domain, data_dir=_current_data_dir())
        flash(f"Disclosure request sent to {letter.to_email}.", "success")
    else:
        flash(f"Failed to send disclosure request for {domain}.", "danger")

    return redirect(url_for("transfers", account=account))


@app.route("/transfers/request-all", methods=["POST"])
def transfers_request_all():
    """Start background task: send disclosure requests to all eligible companies."""
    account = request.form.get("account", "")
    if not account:
        accounts = _get_accounts()
        account = accounts[0] if accounts else ""

    if find_running_task("subprocessor_requests"):
        return redirect(url_for("transfers", account=account))

    start_task("subprocessor_requests", _send_all_disclosure_requests, account)
    return redirect(url_for("transfers", account=account))


@app.route("/api/transfers/task")
def transfers_task_status():
    """Return JSON status of the most recent subprocessors task."""
    task_id = request.args.get("task_id", "")
    task = get_task(task_id) if task_id else find_running_task("subprocessors")
    if not task:
        return jsonify({"status": "idle"})
    return jsonify(task)


@app.route("/api/body/<domain>/<message_id>")
def api_body(domain: str, message_id: str):
    from flask import jsonify
    if not message_id:
        return jsonify({"body": "(no message ID — run monitor to fetch reply bodies)"}), 400
    account = request.args.get("account", "")
    if not account:
        accounts = _get_accounts()
        account = accounts[0] if accounts else ""
    try:
        from auth.gmail_oauth import get_gmail_service
        from reply_monitor.fetcher import _extract_body
        service, _email = get_gmail_service(email_hint=account, tokens_dir=_current_tokens_dir())
        msg = service.users().messages().get(
            userId="me", id=message_id, format="full"
        ).execute()
        body = _extract_body(msg.get("payload", {}))
        return jsonify({"body": body or "(empty)"})
    except Exception as exc:
        msg = str(exc)
        if "invalid_grant" in msg or "Token has been expired" in msg:
            friendly = "Gmail auth expired — visit /pipeline/reauth-send to re-authorise"
        elif "404" in msg or "not found" in msg.lower():
            friendly = "Message not found in Gmail (may have been deleted)"
        else:
            friendly = f"Error loading body: {msg}"
        return jsonify({"body": f"({friendly})"}), 500


# ===========================================================================
# Portal submission routes
# ===========================================================================

import threading as _threading

_portal_tasks: dict[str, dict] = {}  # domain -> {"status": ..., "result": ...}


@app.route("/portal/submit/<domain>", methods=["POST"])
def portal_submit(domain: str):
    """Start a portal submission as a background task."""
    account = request.args.get("account", "")

    if domain in _portal_tasks and _portal_tasks[domain].get("status") == "running":
        return jsonify({"error": "submission already in progress"}), 409

    # Find the letter for this domain
    from contact_resolver.resolver import ContactResolver
    from letter_engine.composer import compose

    resolver = ContactResolver()
    record = resolver.resolve(domain, domain, verbose=False)
    if not record or record.contact.preferred_method != "portal":
        return jsonify({"error": "not a portal company"}), 400

    letter = compose(record)

    _portal_tasks[domain] = {"status": "running", "result": None}

    def _run():
        try:
            from portal_submitter import submit_portal
            result = submit_portal(letter, scan_email=account)
            _portal_tasks[domain] = {"status": "done", "result": result}

            # Record to tracker
            if result.success or result.needs_manual:
                from letter_engine import tracker
                tracker.record_sent(
                    letter,
                    portal_status=result.portal_status,
                    portal_confirmation_ref=result.confirmation_ref,
                    portal_screenshot=result.screenshot_path,
                )
        except Exception as exc:
            _portal_tasks[domain] = {"status": "error", "result": str(exc)}

    _threading.Thread(target=_run, daemon=True).start()
    return jsonify({"status": "started"})


@app.route("/portal/status/<domain>")
def portal_status(domain: str):
    """Poll portal submission progress."""
    task = _portal_tasks.get(domain)
    if not task:
        return jsonify({"status": "not_found"})

    if task["status"] == "running":
        return jsonify({"status": "running"})

    result = task["result"]
    if isinstance(result, str):
        return jsonify({"status": "error", "error": result})

    return jsonify({
        "status": "done",
        "success": result.success,
        "needs_manual": result.needs_manual,
        "portal_status": result.portal_status,
        "confirmation_ref": result.confirmation_ref,
        "error": result.error,
    })


@app.route("/captcha/<domain>")
def captcha_show(domain: str):
    """Show a pending CAPTCHA for the user to solve."""
    captcha_dir = Path(__file__).parent.parent / "user_data" / "captcha_pending"
    screenshot = captcha_dir / f"{domain}.png"
    challenge_file = captcha_dir / f"{domain}.json"

    if not screenshot.exists() or not challenge_file.exists():
        flash("No pending CAPTCHA for this domain.", "warning")
        return redirect(url_for("index"))

    import base64
    img_b64 = base64.b64encode(screenshot.read_bytes()).decode()
    challenge = json.loads(challenge_file.read_text())

    return render_template(
        "captcha.html",
        domain=domain,
        captcha_image=img_b64,
        portal_url=challenge.get("portal_url", ""),
    )


@app.route("/captcha/<domain>", methods=["POST"])
def captcha_solve(domain: str):
    """Submit a CAPTCHA solution."""
    solution = request.form.get("solution", "").strip()
    if not solution:
        flash("Please enter the CAPTCHA solution.", "warning")
        return redirect(url_for("captcha_show", domain=domain))

    captcha_dir = Path(__file__).parent.parent / "user_data" / "captcha_pending"
    challenge_file = captcha_dir / f"{domain}.json"

    if challenge_file.exists():
        data = json.loads(challenge_file.read_text())
        data["status"] = "solved"
        data["solution"] = solution
        challenge_file.write_text(json.dumps(data, indent=2))
        flash("CAPTCHA solution submitted. Portal submission continuing...", "success")
    else:
        flash("CAPTCHA challenge not found or already expired.", "warning")

    return redirect(url_for("index"))


# ===========================================================================
# Pipeline routes
# ===========================================================================

import json as _json
from datetime import datetime, timezone
from typing import Any

from dashboard.scan_state import load_scan_state, save_scan_state, get_all_accounts as _get_scan_accounts
from dashboard.tasks import start_task, get_task, find_running_task, update_task_progress

_CONFIDENCE_RANK = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}


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


def _load_companies_db() -> dict:
    try:
        return _json.loads(_COMPANIES_PATH.read_text()).get("companies",
               _json.loads(_COMPANIES_PATH.read_text()))
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


def _sync_scan_state_flags(account: str, state: dict) -> dict:
    """Update contact_resolved / sar_sent flags from authoritative sources.

    Also detects BOUNCE_PERMANENT in reply_state and advances the retry cycle:
      - Adds the bounced email to failed_emails, increments bounce_attempts
      - Resets contact_resolved/sar_sent so the company re-enters the resolve→send flow
      - After 3 bounces, sets needs_human=True

    sar_sent is True only when a NON-bounced email was sent (direct domain match or
    contact-email-domain match), excluding any address in failed_emails.
    """
    sent_log = get_log(data_dir=_current_data_dir())
    companies_db = _load_companies_db()

    # Build most-recent sent record per domain
    sent_by_domain: dict[str, dict] = {}
    for r in sent_log:
        d = domain_from_sent_record(r)
        if d not in sent_by_domain or r.get("sent_at", "") > sent_by_domain[d].get("sent_at", ""):
            sent_by_domain[d] = r

    # Load reply states to detect BOUNCED companies
    try:
        reply_states = load_state(account, path=_current_state_path())
    except Exception:
        reply_states = {}

    _MAX_BOUNCE_ATTEMPTS = 3

    for domain, data in state.get("discovered_companies", {}).items():
        failed_emails: list[str] = data.setdefault("failed_emails", [])
        bounce_attempts: int = data.get("bounce_attempts", 0)

        # ── Bounce detection ──────────────────────────────────────────
        # Only trigger when a SAR has been sent and the company isn't already
        # waiting for human input.
        if data.get("sar_sent") and not data.get("needs_human"):
            reply_state = reply_states.get(domain)
            if reply_state and compute_status(reply_state) == "BOUNCED":
                # The bounced email is on the CompanyState, not the most recent
                # sent record.  reply_state.to_email holds the address that was
                # used when the state was first created (the original send).
                bounced_email = reply_state.to_email

                # Register this bounce only once (guard against repeated calls)
                if bounced_email and bounced_email not in failed_emails:
                    failed_emails.append(bounced_email)
                    bounce_attempts += 1
                    data["bounce_attempts"] = bounce_attempts

                    # Find the currently active sent email (most recent record)
                    latest_sent_rec = sent_by_domain.get(domain)
                    current_sent_email = (latest_sent_rec.get("to_email", "") if latest_sent_rec else "")

                    if bounce_attempts >= _MAX_BOUNCE_ATTEMPTS:
                        data["needs_human"] = True
                    elif current_sent_email and current_sent_email != bounced_email:
                        # Already retried with a different address — keep sar_sent=True
                        pass
                    else:
                        # Latest send is the same as the bounced one — reset for retry
                        data["contact_resolved"] = False
                        data["sar_sent"] = False
                        data["skipped"] = False
                        data["approved"] = False

        # ── sar_sent: trust scan_state if already True (set by _do_send) ──
        # Only derive from sent_letters.json for CLI-sent letters, which
        # never went through _do_send and have sar_sent=False in scan_state.
        # Portal/postal letters have no to_email, so domain extraction is
        # unreliable — trusting scan_state is the only safe option.
        if data.get("sar_sent"):
            already_sent = True  # set by _do_send; bounce detection above may have cleared it
        else:
            # CLI-sent letters: derive from sent_letters.json
            latest_sent_rec = sent_by_domain.get(domain)
            if latest_sent_rec:
                latest_to = latest_sent_rec.get("to_email", "")
                already_sent = bool(latest_to) and latest_to not in failed_emails
            else:
                already_sent = False

            if not already_sent and domain in companies_db:
                # Secondary: contact email domain matches a sent domain (e.g. substack/substackinc)
                rec = companies_db[domain]
                contact = rec.get("contact", {})
                for email_field in ("privacy_email", "dpo_email"):
                    email = contact.get(email_field, "")
                    if email and "@" in email:
                        contact_domain = email.split("@")[-1].lower()
                        contact_sent_rec = sent_by_domain.get(contact_domain)
                        if contact_sent_rec:
                            to_email = contact_sent_rec.get("to_email", "")
                            if to_email and to_email not in failed_emails:
                                already_sent = True
                                break

        data["sar_sent"] = already_sent

        # ── contact_resolved / skipped ────────────────────────────────
        if domain in companies_db and not data.get("needs_human"):
            data["contact_resolved"] = True
            data["skipped"] = False  # resolved overrides a previous skipped
        elif not data.get("contact_resolved"):
            pass  # leave as-is (may be False from bounce reset or never resolved)

    return state


def _token_exists(account: str, kind: str) -> bool:
    """Check whether an OAuth token file exists for this account."""
    from dashboard.scan_state import _safe_key as _sk
    return (_current_tokens_dir() / f"{_sk(account)}_{kind}.json").exists()


def _send_token_valid(account: str) -> tuple[bool, str]:
    """Return (is_valid, error_message) for the send token."""
    from auth.gmail_oauth import check_send_token_valid
    return check_send_token_valid(account, tokens_dir=_current_tokens_dir())


# ---------------------------------------------------------------------------
# Background task functions
# ---------------------------------------------------------------------------

def _do_scan(task_id: str, service: Any, account: str, known_ids: set[str], max_emails: int = 2000) -> dict:
    from scanner.inbox_reader import fetch_new_emails, get_inbox_total
    from scanner.service_extractor import extract_services

    # Fetch total inbox size first (single fast API call) so the UI can show X/Y
    inbox_total = get_inbox_total(service)

    def _progress(n: int) -> None:
        update_task_progress(task_id, n)

    new_emails = fetch_new_emails(service, known_ids, max_results=max_emails, progress_callback=_progress)
    new_services = extract_services(new_emails)

    state = load_scan_state(account, data_dir=_current_data_dir())
    discovered = state.setdefault("discovered_companies", {})

    new_count = 0
    for s in new_services:
        domain = s["domain"]
        if domain not in discovered:
            discovered[domain] = {
                "company_name_raw": s["company_name_raw"],
                "confidence": s["confidence"],
                "first_seen": s.get("first_seen", ""),
                "last_seen": s.get("last_seen", ""),
                "contact_resolved": False,
                "sar_sent": False,
                "approved": False,
                "skipped": False,
            }
            new_count += 1
        else:
            # Upgrade confidence if higher; update last_seen
            existing = discovered[domain]
            if _CONFIDENCE_RANK.get(s["confidence"], 0) > _CONFIDENCE_RANK.get(existing["confidence"], 0):
                existing["confidence"] = s["confidence"]
            if s.get("last_seen", "") > existing.get("last_seen", ""):
                existing["last_seen"] = s["last_seen"]

    all_ids = list(set(state.get("scanned_message_ids", [])) | {e["message_id"] for e in new_emails})
    state["scanned_message_ids"] = all_ids
    state["last_scan_at"] = datetime.now(timezone.utc).isoformat()
    if inbox_total > 0:
        state["inbox_total"] = inbox_total
    save_scan_state(account, state, data_dir=_current_data_dir())

    return {"new_emails": len(new_emails), "new_companies": new_count, "total_discovered": len(discovered)}


def _do_resolve(task_id: str, account: str, unresolved: list[tuple[str, dict]], max_llm: int) -> dict:
    from contact_resolver import cost_tracker
    from contact_resolver.resolver import ContactResolver

    cost_tracker.set_llm_limit(max_llm)
    resolver = ContactResolver()
    resolved_count = 0

    for i, (domain, data) in enumerate(unresolved):
        update_task_progress(task_id, i, len(unresolved))
        exclude = set(data.get("failed_emails", []))
        record = resolver.resolve(domain, data["company_name_raw"], verbose=False, exclude_emails=exclude or None)
        # Reload before writing to avoid stale overwrites
        state = load_scan_state(account, data_dir=_current_data_dir())
        if domain in state.get("discovered_companies", {}):
            if record:
                state["discovered_companies"][domain]["contact_resolved"] = True
                resolved_count += 1
            else:
                # Mark as skipped so it isn't retried on the next resolve run
                state["discovered_companies"][domain]["skipped"] = True
        save_scan_state(account, state, data_dir=_current_data_dir())

    update_task_progress(task_id, len(unresolved), len(unresolved))
    return {"resolved": resolved_count, "attempted": len(unresolved)}


def _do_send(task_id: str, account: str, approved_domains: list[str]) -> dict:
    from contact_resolver.models import CompanyRecord
    from letter_engine.composer import compose
    from letter_engine.sender import send_letter

    companies_db = _load_companies_db()
    sent_count = 0

    for i, domain in enumerate(approved_domains):
        update_task_progress(task_id, i, len(approved_domains))
        raw = companies_db.get(domain)
        if not raw:
            continue
        try:
            record = CompanyRecord.model_validate(raw)
        except Exception:
            continue
        letter = compose(record)
        success, _msg_id, _thread_id = send_letter(
            letter, account, data_dir=_current_data_dir(), tokens_dir=_current_tokens_dir(),
        )
        if success:
            state = load_scan_state(account, data_dir=_current_data_dir())
            if domain in state.get("discovered_companies", {}):
                state["discovered_companies"][domain]["sar_sent"] = True
            save_scan_state(account, state, data_dir=_current_data_dir())
            sent_count += 1

    update_task_progress(task_id, len(approved_domains), len(approved_domains))
    return {"sent": sent_count, "attempted": len(approved_domains)}


def _fetch_all_subprocessors(task_id: str, account: str) -> dict:
    """Background task: fetch subprocessors for all SAR domains."""
    import json as _j
    import os
    from contact_resolver.subprocessor_fetcher import fetch_subprocessors, is_stale
    from contact_resolver.resolver import write_subprocessors

    api_key = os.environ.get("ANTHROPIC_API_KEY")

    states = _load_all_states(account)
    domains = list(states.keys())

    try:
        raw = _j.loads(_COMPANIES_PATH.read_text())
        companies_raw = raw.get("companies", raw)
    except Exception:
        companies_raw = {}

    fetched = 0
    skipped = 0
    fetched_domains: set[str] = set()

    for i, domain in enumerate(domains):
        update_task_progress(task_id, i, len(domains))
        company_raw = companies_raw.get(domain, {})
        company_name = company_raw.get("company_name") or domain

        existing = company_raw.get("subprocessors")
        if existing and existing.get("fetch_status") == "ok" and not is_stale_dict(existing):
            skipped += 1
            continue

        if domain in fetched_domains:
            skipped += 1
            continue
        fetched_domains.add(domain)

        record = fetch_subprocessors(company_name, domain, api_key=api_key)
        write_subprocessors(domain, record)
        fetched += 1

    update_task_progress(task_id, len(domains), len(domains))
    return {"fetched": fetched, "skipped": skipped, "total": len(domains)}


def _send_all_disclosure_requests(task_id: str, account: str) -> dict:
    """Background task: send subprocessor disclosure request letters to all eligible companies."""
    import json as _j
    from contact_resolver.models import CompanyRecord
    from letter_engine.composer import compose_subprocessor_request
    from letter_engine.sender import send_letter
    from letter_engine.tracker import (
        get_log as _get_letter_log,
        record_subprocessor_request,
    )

    states = _load_all_states(account)
    domains = list(states.keys())

    try:
        raw = _j.loads(_COMPANIES_PATH.read_text())
        companies_raw = raw.get("companies", raw)
    except Exception:
        companies_raw = {}

    # Build set of domains already requested
    _req_log = _get_letter_log(path=_current_sp_requests_path())
    already_requested: set[str] = {r.get("domain", "") for r in _req_log}

    sent = 0
    skipped = 0

    for i, domain in enumerate(domains):
        update_task_progress(task_id, i, len(domains))

        if domain in already_requested:
            skipped += 1
            continue

        company_raw = companies_raw.get(domain)
        if not company_raw:
            skipped += 1
            continue

        try:
            record = CompanyRecord.model_validate(company_raw)
        except Exception:
            skipped += 1
            continue

        letter = compose_subprocessor_request(record)
        if not letter:
            skipped += 1
            continue

        success, msg_id, thread_id = send_letter(
            letter, account, record=False, tokens_dir=_current_tokens_dir(),
        )
        if success:
            letter.gmail_message_id = msg_id
            letter.gmail_thread_id = thread_id
            record_subprocessor_request(letter, domain, data_dir=_current_data_dir())
            already_requested.add(domain)
            sent += 1
        else:
            skipped += 1

    update_task_progress(task_id, len(domains), len(domains))
    return {"sent": sent, "skipped": skipped, "total": len(domains)}


def is_stale_dict(sp_dict: dict, ttl_days: int = 30) -> bool:
    """Check staleness of a raw subprocessors dict (from JSON)."""
    from contact_resolver.subprocessor_fetcher import is_stale
    from contact_resolver.models import SubprocessorRecord
    try:
        record = SubprocessorRecord.model_validate(sp_dict)
        return is_stale(record, ttl_days)
    except Exception:
        return True


# ---------------------------------------------------------------------------
# Pipeline page
# ---------------------------------------------------------------------------

@app.route("/pipeline")
def pipeline():
    accounts = _get_all_accounts()
    account = request.args.get("account", accounts[0] if accounts else "")

    state = load_scan_state(account, data_dir=_current_data_dir())
    state = _sync_scan_state_flags(account, state)

    discovered = state.get("discovered_companies", {})
    resolved = sum(1 for d in discovered.values() if d.get("contact_resolved"))
    sent = sum(1 for d in discovered.values() if d.get("sar_sent"))
    skipped = sum(1 for d in discovered.values() if d.get("skipped") and not d.get("contact_resolved"))
    needs_human_count = sum(1 for d in discovered.values() if d.get("needs_human"))
    ready = sum(
        1 for d in discovered.values()
        if d.get("contact_resolved") and not d.get("sar_sent") and not d.get("skipped")
    )

    needs_human_items = [
        {
            "domain": domain,
            "company_name": data.get("company_name_raw", domain),
            "failed_emails": data.get("failed_emails", []),
            "bounce_attempts": data.get("bounce_attempts", 0),
        }
        for domain, data in discovered.items()
        if data.get("needs_human")
    ]

    # Count pending/overdue from reply_state for the Monitor card
    monitor_counts = {"PENDING": 0, "OVERDUE": 0}
    try:
        reply_states = load_state(account, path=_current_state_path())
        for s in reply_states.values():
            st = compute_status(s)
            if st in monitor_counts:
                monitor_counts[st] += 1
    except Exception:
        pass

    running_scan = find_running_task("scan")
    running_resolve = find_running_task("resolve")
    running_send = find_running_task("send")

    last_scan_at = state.get("last_scan_at")
    total_scanned = len(state.get("scanned_message_ids", []))
    inbox_total = state.get("inbox_total", 0)

    # Inbox is "fully scanned" when we know the total and have seen at least that many IDs.
    # inbox_total=0 means we haven't fetched the total yet (older scan state) — treat as unknown.
    inbox_complete = inbox_total > 0 and total_scanned >= inbox_total

    # Cooldown only applies when the inbox is already fully scanned — no point re-scanning
    # a complete inbox within 30 minutes. When there are still unseen emails, always allow
    # clicking "Scan remaining".
    scan_on_cooldown = False
    if inbox_complete and last_scan_at and not running_scan:
        try:
            last_dt = datetime.fromisoformat(last_scan_at.replace("Z", "+00:00"))
            elapsed_minutes = (datetime.now(timezone.utc) - last_dt).total_seconds() / 60
            scan_on_cooldown = elapsed_minutes < 30
        except Exception:
            pass

    # Authoritative "sent" count: unique domains across both sent_letters.json and reply_state.
    # reply_state alone undercounts — the reply monitor hasn't necessarily run yet for recently
    # sent letters, so they appear in sent_letters.json but not yet in reply_state.json.
    sent_count = len(_load_all_states(account))

    return render_template(
        "pipeline.html",
        accounts=accounts,
        account=account,
        last_scan_at=last_scan_at,
        total_scanned=total_scanned,
        inbox_total=inbox_total,
        inbox_complete=inbox_complete,
        total_discovered=len(discovered),
        resolved_count=resolved,
        sent_count=sent_count,
        skipped_count=skipped,
        ready_count=ready,
        needs_human_count=needs_human_count,
        needs_human_items=needs_human_items,
        monitor_counts=monitor_counts,
        running_scan=running_scan,
        running_resolve=running_resolve,
        running_send=running_send,
        scan_on_cooldown=scan_on_cooldown,
    )


@app.route("/pipeline/add-account", methods=["POST"])
def pipeline_add_account():
    """Authenticate a new Gmail account and immediately start a full scan."""
    new_account = request.form.get("account", "").strip()
    if not new_account:
        return redirect(url_for("pipeline"))
    # Trigger OAuth (will open browser if token missing)
    try:
        from auth.gmail_oauth import get_gmail_service
        _service, authenticated_as = get_gmail_service(email_hint=new_account, tokens_dir=_current_tokens_dir())
    except Exception as exc:
        return f"OAuth failed: {exc}", 500

    # Start a full scan (known_ids is empty → full inbox scan)
    start_task("scan", _do_scan, _service, authenticated_as, set(), 2000)
    return redirect(url_for("pipeline", account=authenticated_as))


@app.route("/pipeline/scan", methods=["POST"])
def pipeline_scan():
    from auth.gmail_oauth import get_gmail_service

    account = request.form.get("account", "")
    if not account:
        accounts = _get_all_accounts()
        account = accounts[0] if accounts else ""

    existing = find_running_task("scan")
    if existing:
        return jsonify({"task_id": existing["id"], "already_running": True})

    # Build + validate the Gmail service synchronously here so auth errors surface
    # immediately as JSON (not as a silent hang in a background thread).
    try:
        service, account = get_gmail_service(email_hint=account, tokens_dir=_current_tokens_dir())
    except Exception as exc:
        return jsonify({"error": f"Gmail auth failed: {exc}. Run `python run.py --dry-run` once to re-authenticate."}), 401

    max_emails = min(int(request.form.get("max_emails", 2000)), 10000)
    state = load_scan_state(account, data_dir=_current_data_dir())
    known_ids = set(state.get("scanned_message_ids", []))
    task_id = start_task("scan", _do_scan, service, account, known_ids, max_emails)
    return jsonify({"task_id": task_id})


@app.route("/pipeline/resolve", methods=["POST"])
def pipeline_resolve():
    account = request.form.get("account", "")
    max_llm = int(request.form.get("max_llm_calls", 20))

    existing = find_running_task("resolve")
    if existing:
        return jsonify({"task_id": existing["id"], "already_running": True})

    state = load_scan_state(account, data_dir=_current_data_dir())
    state = _sync_scan_state_flags(account, state)
    save_scan_state(account, state, data_dir=_current_data_dir())  # persist cache-hit flags so they aren't re-attempted
    unresolved = [
        (domain, data)
        for domain, data in state.get("discovered_companies", {}).items()
        if not data.get("contact_resolved") and not data.get("skipped")
    ]
    if not unresolved:
        return jsonify({"task_id": None, "message": "Nothing to resolve"})

    task_id = start_task("resolve", _do_resolve, account, unresolved, max_llm)
    return jsonify({"task_id": task_id, "total": len(unresolved)})


@app.route("/pipeline/review")
def pipeline_review():
    accounts = _get_all_accounts()
    account = request.args.get("account", accounts[0] if accounts else "")

    state = load_scan_state(account, data_dir=_current_data_dir())
    state = _sync_scan_state_flags(account, state)
    companies_db = _load_companies_db()

    from contact_resolver.models import CompanyRecord
    from letter_engine.composer import compose

    review_items = []
    for domain, data in state.get("discovered_companies", {}).items():
        if not data.get("contact_resolved"):
            continue
        if data.get("sar_sent") or data.get("skipped"):
            continue
        raw = companies_db.get(domain)
        if not raw:
            continue
        try:
            record = CompanyRecord.model_validate(raw)
            letter = compose(record)
        except Exception:
            continue
        review_items.append({
            "domain": domain,
            "company_name": data.get("company_name_raw", record.company_name),
            "confidence": data.get("confidence", "LOW"),
            "method": letter.method,
            "to_email": letter.to_email,
            "portal_url": letter.portal_url,
            "subject": letter.subject,
            "body": letter.body,
            "approved": data.get("approved", False),
        })

    review_items.sort(key=lambda x: _CONFIDENCE_RANK.get(x["confidence"], 0), reverse=True)

    # Check if the send token is still valid so the template can warn the user
    send_ok, send_auth_error = _send_token_valid(account) if account else (False, "No account")

    return render_template(
        "pipeline_review.html",
        accounts=accounts,
        account=account,
        review_items=review_items,
        send_token_valid=send_ok,
        send_auth_error=send_auth_error,
    )


@app.route("/pipeline/manual-contact", methods=["POST"])
def pipeline_manual_contact():
    """Save a user-supplied contact email for a needs-human company and re-queue for review."""
    from contact_resolver.models import CompanyRecord, Contact, Flags, RequestNotes
    from datetime import date as _date

    account = request.form.get("account", "")
    domain = request.form.get("domain", "").strip()
    manual_email = request.form.get("email", "").strip()

    if not domain or not manual_email or "@" not in manual_email:
        return redirect(url_for("pipeline", account=account))

    # Save the manual contact to companies.json so compose() can use it
    db = _load_companies_db()
    existing_raw = db.get(domain, {})
    company_name = existing_raw.get("company_name", domain)

    record = CompanyRecord(
        company_name=company_name,
        source="user_manual",
        source_confidence="high",
        last_verified=_date.today().isoformat(),
        contact=Contact(privacy_email=manual_email, preferred_method="email"),
    )

    from contact_resolver.resolver import ContactResolver
    ContactResolver().save(domain, record)

    # Reset scan state: clear needs_human, mark resolved, ready for review
    state = load_scan_state(account, data_dir=_current_data_dir())
    company_data = state.get("discovered_companies", {}).get(domain)
    if company_data is not None:
        company_data["needs_human"] = False
        company_data["contact_resolved"] = True
        company_data["sar_sent"] = False
        company_data["approved"] = False
        company_data["skipped"] = False
    save_scan_state(account, state, data_dir=_current_data_dir())

    return redirect(url_for("pipeline", account=account))


@app.route("/pipeline/approve", methods=["POST"])
def pipeline_approve():
    data = request.get_json(force=True)
    account = data.get("account", "")
    approvals: dict = data.get("approvals", {})

    state = load_scan_state(account, data_dir=_current_data_dir())
    for domain, approved in approvals.items():
        if domain in state.get("discovered_companies", {}):
            state["discovered_companies"][domain]["approved"] = bool(approved)
            state["discovered_companies"][domain]["skipped"] = not bool(approved)
    save_scan_state(account, state, data_dir=_current_data_dir())
    return jsonify({"ok": True})


@app.route("/pipeline/reauth-send")
def pipeline_reauth_send():
    """Re-run OAuth for the gmail.send scope.  Opens a browser on the local machine."""
    from auth.gmail_oauth import get_gmail_send_service
    from dashboard.scan_state import _safe_key as _sk

    account = request.args.get("account", "")

    # Delete the stale token so get_gmail_send_service will trigger a fresh OAuth flow
    token_path = _current_tokens_dir() / f"{_sk(account)}_send.json"
    token_path.unlink(missing_ok=True)

    try:
        get_gmail_send_service(account, tokens_dir=_current_tokens_dir())
    except Exception as exc:
        return f"<p>Auth failed: {exc}</p><p><a href='/pipeline/review?account={account}'>Back</a></p>", 500

    return redirect(url_for("pipeline_review", account=account))


@app.route("/pipeline/send", methods=["POST"])
def pipeline_send():
    account = request.form.get("account", "")

    send_ok, send_err = _send_token_valid(account)
    if not send_ok:
        # Redirect to review page — template will show the re-auth warning
        return redirect(url_for("pipeline_review", account=account))

    existing = find_running_task("send")
    if existing:
        return jsonify({"task_id": existing["id"], "already_running": True})

    state = load_scan_state(account, data_dir=_current_data_dir())
    state = _sync_scan_state_flags(account, state)
    approved_domains = [
        domain for domain, data in state.get("discovered_companies", {}).items()
        if data.get("approved") and not data.get("sar_sent")
    ]
    if not approved_domains:
        return redirect(url_for("pipeline", account=account))

    task_id = start_task("send", _do_send, account, approved_domains)
    return redirect(url_for("pipeline", account=account, task_id=task_id))


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------

@app.route("/api/task/<task_id>")
def api_task(task_id: str):
    task = get_task(task_id)
    if not task:
        return jsonify({"error": "unknown task"}), 404
    return jsonify(task)


@app.route("/api/scan/status")
def api_scan_status():
    account = request.args.get("account", "")
    state = load_scan_state(account, data_dir=_current_data_dir())
    running = find_running_task("scan")
    total_scanned = len(state.get("scanned_message_ids", []))
    inbox_total = state.get("inbox_total", 0)
    return jsonify({
        "in_progress": bool(running),
        "task_id": running["id"] if running else None,
        "progress": running["progress"] if running else 0,
        "last_scan_at": state.get("last_scan_at"),
        "total_scanned_ids": total_scanned,
        "inbox_total": inbox_total,
        "inbox_complete": inbox_total > 0 and total_scanned >= inbox_total,
        "total_discovered": len(state.get("discovered_companies", {})),
    })


# ---------------------------------------------------------------------------
# Settings: export & delete account
# ---------------------------------------------------------------------------

import io
import zipfile


@app.route("/settings/export")
def export_data():
    """Download a zip of the user's data directory."""
    data_dir = _current_data_dir()
    if not data_dir.exists():
        return "No data found.", 404

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in data_dir.rglob("*"):
            if file_path.is_file():
                arcname = file_path.relative_to(data_dir)
                zf.write(file_path, arcname)

    buf.seek(0)
    safe = _safe_email(g.user.email)
    return Response(
        buf.getvalue(),
        mimetype="application/zip",
        headers={"Content-Disposition": f"attachment; filename=gdpr-agent-{safe}.zip"},
    )


@app.route("/settings/delete-account", methods=["POST"])
def delete_account():
    """Delete the current user's account and all their data."""
    import shutil
    from dashboard.user_model import delete_user

    email = g.user.email
    data_dir = _current_data_dir()

    if data_dir.exists():
        shutil.rmtree(data_dir)

    delete_user(email, path=app.config.get("USERS_PATH", _USER_DATA / "users.json"))

    from flask_login import logout_user
    logout_user()

    return redirect(url_for("auth.login"))


# ---------------------------------------------------------------------------
# Progressive scan with SSE
# ---------------------------------------------------------------------------

import threading
from dashboard.sse import MessageAnnouncer, format_sse

_scan_announcers: dict[str, MessageAnnouncer] = {}


@app.route("/scan")
def scan_page():
    """Progressive scan page."""
    mailbox = request.args.get("mailbox", "")
    data_dir = _current_data_dir()
    scan_state = {}
    if mailbox:
        scan_state = load_scan_state(mailbox, data_dir=data_dir)
    return render_template("scan.html", mailbox=mailbox, scan_state=scan_state)


@app.route("/scan/start", methods=["POST"])
def scan_start():
    """Start or resume a scan for the given mailbox."""
    mailbox = request.form.get("mailbox", "")
    batch_size = int(request.form.get("batch_size", 500))
    user_email = g.user.email
    data_dir = _current_data_dir()

    ann = MessageAnnouncer()
    _scan_announcers[user_email] = ann

    def _run_scan():
        from auth.gmail_oauth import get_gmail_service
        from scanner.inbox_reader import fetch_emails
        from scanner.service_extractor import extract_services

        try:
            tokens_dir = data_dir / "tokens"
            service, email = get_gmail_service(
                email_hint=mailbox, tokens_dir=tokens_dir
            )

            profile = service.users().getProfile(userId="me").execute()
            total = profile.get("messagesTotal", 0)
            ann.announce(format_sse(
                f'{{"total_estimate": {total}}}', event="estimate"
            ))

            state = load_scan_state(mailbox, data_dir=data_dir)

            emails = fetch_emails(service, max_results=batch_size)
            services = extract_services(emails)

            for svc in services:
                domain = svc["domain"]
                name = svc["company_name"]
                confidence = svc["confidence"]
                ann.announce(format_sse(
                    f'{{"domain": "{domain}", "name": "{name}", "confidence": "{confidence}"}}',
                    event="service",
                ))

            ann.announce(format_sse(
                f'{{"scanned": {len(emails)}, "services": {len(services)}, "done": true}}',
                event="progress",
            ))

            save_scan_state(mailbox, {
                "emails_scanned": state.get("emails_scanned", 0) + len(emails),
                "total_estimate": total,
                "services_found": [s["domain"] for s in services],
                "status": "paused",
            }, data_dir=data_dir)

        except Exception as e:
            ann.announce(format_sse(f'{{"error": "{e!s}"}}', event="error"))

    thread = threading.Thread(target=_run_scan, daemon=True)
    thread.start()
    return "", 204


@app.route("/scan/stream")
def scan_stream():
    """SSE endpoint for scan progress."""
    user_email = g.user.email
    ann = _scan_announcers.get(user_email)
    if not ann:
        return "No active scan", 404

    def stream():
        q = ann.listen()
        try:
            while True:
                msg = q.get(timeout=60)
                yield msg
        except Exception:
            pass

    return Response(
        stream(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


if __name__ == "__main__":
    app.run(debug=True, port=5001, threaded=True)
