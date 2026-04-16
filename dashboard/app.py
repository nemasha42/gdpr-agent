"""Flask dashboard for GDPR SAR monitoring.

Routes:
    GET /                       — account selector + all company cards
    GET /company/<domain>       — full reply thread for one company
    GET /data/<domain>          — data card (via data_bp)
    GET /refresh?account=EMAIL  — run monitor inline (via monitor_bp)
    GET /reextract?account=EMAIL — re-extract data links (via monitor_bp)
"""

from __future__ import annotations

import os
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

# Allow OAuth over HTTP for local development (localhost)
os.environ.setdefault("OAUTHLIB_INSECURE_TRANSPORT", "1")

# Ensure project root is on path when run directly
_PROJECT_ROOT = Path(__file__).parent.parent
sys.path.insert(0, str(_PROJECT_ROOT))

# Load .env so ANTHROPIC_API_KEY is available for schema analysis
try:
    from dotenv import load_dotenv

    load_dotenv(_PROJECT_ROOT / ".env")
except ImportError:
    pass

from flask import (
    Response,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)

from letter_engine.tracker import get_log
from reply_monitor.state_manager import (
    _COMPANY_STATUS_PRIORITY,
    compute_company_status,
    compute_status,
    days_remaining,
    load_state,
    save_state,
)
from dashboard.shared import (
    _USER_DATA,
    _STATE_PATH,
    _COMPANIES_PATH,
    REQUEST_TYPES,
    _TERMINAL_STATUSES,
    _STATUS_COLOUR,
    _TAG_COLOUR,
    _DISPLAY_NAMES,
    _TIER_TERMINAL,
    _TIER_ACTION,
    _TIER_PROGRESS,
    _TIER_NOISE,
    _effective_tags,
    _ACTION_HINTS,
    _clean_snippet,
    _is_human_friendly,
    _current_data_dir,
    _current_state_path,
    _current_sp_state_path,
    _current_tokens_dir,
    _current_sp_requests_path,
    _dedup_reply_rows,
    _get_accounts,
    _get_all_accounts,
    _build_card,
    _lookup_company,
    _load_companies_db,
    _load_all_states,
)


from flask import g
from flask_login import current_user
from dashboard.user_model import _safe_email
from dashboard import create_app

app = create_app()


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
        sp_sent_domains: set[str] = {
            r.get("domain", "") for r in get_log(path=_current_sp_requests_path())
        }

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
        cards.sort(
            key=lambda c: _COMPANY_STATUS_PRIORITY.get(c["company_status"], 0),
            reverse=True,
        )

    scan_state = (
        load_scan_state(account, data_dir=_current_data_dir()) if account else {}
    )
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
        reply_rows.append(
            {
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
            }
        )

    # Build past attempts for display — each with deduplicated address + reply summary
    past_attempts_display = []
    seen_pa_emails: set[str] = set()
    for pa in state.past_attempts:
        email = pa.get("to_email", "")
        if not email:
            continue
        pa_reply_rows = []
        for r in reversed(pa.get("replies", [])):
            pa_reply_rows.append(
                {
                    "received_at": r["received_at"][:19].replace("T", " "),
                    "from_addr": r["from"],
                    "subject": r["subject"],
                    "snippet": _clean_snippet(r["snippet"]),
                    "tags": r["tags"],
                    "non_gdpr": "NON_GDPR" in r["tags"],
                    "gmail_message_id": r["gmail_message_id"],
                }
            )
        # Only include first occurrence of each address — later identical addresses
        # are duplicate retry entries, not a new service
        already_shown = email.lower() in seen_pa_emails
        seen_pa_emails.add(email.lower())
        past_attempts_display.append(
            {
                "to_email": email,
                "sar_sent_at": pa.get("sar_sent_at", "")[:10],
                "reply_rows": pa_reply_rows,
                "duplicate": already_shown,
            }
        )

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
            sp_reply_rows.append(
                {
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
                }
            )

    # Deduplicate: when SAR and SP requests share the same inbox (e.g. support@company.com),
    # the address-search fallback in the SAR monitor picks up SP replies and stores them in
    # both state files. Filter them out of reply_rows so they only appear in the SP section.
    reply_rows = _dedup_reply_rows(reply_rows, sp_reply_rows)

    # All message IDs in SP stream (company + user replies) — used for SAR dedup.
    # Using the full set (not just sp_reply_rows) prevents duplicate YOUR_REPLY events
    # when the same manual reply is fetched by both SAR and SP monitors.
    sp_all_msg_ids = {
        r.gmail_message_id for r in (sp_state.replies if sp_state else [])
    }

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
            "sent_reply_at": r.sent_reply_at[:16].replace("T", " ")
            if r.sent_reply_at
            else "",
            "to_addr": to_addr,
            "thread_id": thread_id,
        }

    # ── SAR thread (oldest first) ─────────────────────────────────────────────
    sar_thread: list[dict] = [
        {
            "type": "sent",
            "sort_key": state.sar_sent_at,
            "display_at": state.sar_sent_at[:16].replace("T", " "),
            "to_email": state.to_email,
            "subject": state.subject,
        }
    ]
    for r in state.replies:
        if r.gmail_message_id in sp_all_msg_ids:
            continue  # SP is authoritative for messages fetched by both monitors
        if "YOUR_REPLY" in r.tags:
            sar_thread.append(
                {
                    "type": "your_reply",
                    "sort_key": r.received_at,
                    "display_at": r.received_at[:16].replace("T", " "),
                    "body": r.snippet,
                }
            )
            continue
        ev = _build_thread_event(r, state.to_email, "")
        sar_thread.append(ev)
        if r.reply_review_status == "sent" and r.sent_reply_body:
            sar_thread.append(
                {
                    "type": "your_reply",
                    "sort_key": r.sent_reply_at or r.received_at,
                    "display_at": r.sent_reply_at[:16].replace("T", " ")
                    if r.sent_reply_at
                    else "",
                    "body": r.sent_reply_body,
                }
            )
    sar_thread.sort(key=lambda e: e["sort_key"])

    # ── SP thread (oldest first) ──────────────────────────────────────────────
    sp_thread: list[dict] = []
    if sp_sent:
        sp_thread.append(
            {
                "type": "sent",
                "sort_key": sp_sent.get("sent_at", ""),
                "display_at": sp_sent.get("sent_at", "")[:16].replace("T", " "),
                "to_email": sp_sent.get("to", ""),
                "subject": sp_sent.get("subject", "Subprocessor Disclosure Request"),
            }
        )
    if sp_state:
        _sp_tid = sp_state.gmail_thread_id
        _sp_to = sp_sent.get("to", "") if sp_sent else ""
        for r in sp_state.replies:
            if "YOUR_REPLY" in r.tags:
                sp_thread.append(
                    {
                        "type": "your_reply",
                        "sort_key": r.received_at,
                        "display_at": r.received_at[:16].replace("T", " "),
                        "body": r.snippet,
                    }
                )
                continue
            ev = _build_thread_event(r, _sp_to, _sp_tid)
            sp_thread.append(ev)
            if r.reply_review_status == "sent" and r.sent_reply_body:
                sp_thread.append(
                    {
                        "type": "your_reply",
                        "sort_key": r.sent_reply_at or r.received_at,
                        "display_at": r.sent_reply_at[:16].replace("T", " ")
                        if r.sent_reply_at
                        else "",
                        "body": r.sent_reply_body,
                    }
                )
        sp_thread.sort(key=lambda e: e["sort_key"])

    # ── Summary lines ("why is it this status?") ─────────────────────────────
    company_status = compute_company_status(
        sar_status=status,
        sp_status=sp_status,
        sp_sent=bool(sp_sent),
    )
    sar_days_left = days_remaining(state.sar_sent_at)

    # Look up portal URL from company records (overrides included)
    company_record = _lookup_company(domain)
    portal_url = (company_record.get("contact", {}) or {}).get("gdpr_portal_url", "")

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
        sp_status_colour=_STATUS_COLOUR.get(sp_status, "secondary")
        if sp_status
        else "",
        sp_reply_rows=sp_reply_rows,
        sar_thread=sar_thread,
        sp_thread=sp_thread,
        company_status=company_status,
        company_status_colour=_STATUS_COLOUR.get(company_status, "secondary"),
        sar_days_left=sar_days_left,
        portal_url=portal_url,
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

    reply = next(
        (r for r in state.replies if r.gmail_message_id == gmail_message_id), None
    )
    if not reply:
        flash("Reply not found.", "danger")
        return redirect(url_for("company_detail", domain=domain, account=account))

    success, _msg_id, _thread_id = send_thread_reply(
        state.gmail_thread_id,
        to_addr,
        subject,
        reply_body,
        account,
        tokens_dir=_current_tokens_dir(),
    )
    if success:
        reply.reply_review_status = "sent"
        reply.sent_reply_body = reply_body
        reply.sent_reply_at = (
            datetime.now(timezone.utc)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z")
        )
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
        reply = next(
            (r for r in state.replies if r.gmail_message_id == gmail_message_id), None
        )
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

    reply = next(
        (r for r in state.replies if r.gmail_message_id == gmail_message_id), None
    )
    if not reply:
        flash("Reply not found.", "danger")
        return redirect(url_for("company_detail", domain=domain, account=account))

    success, _msg_id, _thread_id = send_thread_reply(
        thread_id or state.gmail_thread_id,
        to_addr,
        subject,
        reply_body,
        account,
        tokens_dir=_current_tokens_dir(),
    )
    if success:
        reply.reply_review_status = "sent"
        reply.sent_reply_body = reply_body
        reply.sent_reply_at = (
            datetime.now(timezone.utc)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z")
        )
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
        reply = next(
            (r for r in state.replies if r.gmail_message_id == gmail_message_id), None
        )
        if reply:
            reply.reply_review_status = "dismissed"
            save_state(account, sp_states, path=_current_sp_state_path())

    return redirect(url_for("company_detail", domain=domain, account=account))


@app.route("/company/<domain>/compose-reply", methods=["POST"])
def compose_reply(domain: str):
    """Send a free-form reply in the SAR thread (no auto-generated draft needed)."""
    account = request.form.get("account", "")
    reply_body = request.form.get("reply_body", "").strip()
    to_addr = request.form.get("to_addr", "")
    subject = request.form.get("subject", "")

    if not reply_body:
        flash("Reply body cannot be empty.", "warning")
        return redirect(url_for("company_detail", domain=domain, account=account))

    from letter_engine.sender import send_thread_reply

    states = load_state(account, path=_current_state_path())
    state = states.get(domain)
    if not state:
        flash("Company not found.", "danger")
        return redirect(url_for("company_detail", domain=domain, account=account))

    success, _msg_id, _thread_id = send_thread_reply(
        state.gmail_thread_id,
        to_addr,
        subject,
        reply_body,
        account,
        tokens_dir=_current_tokens_dir(),
    )
    if success:
        now = (
            datetime.now(timezone.utc)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z")
        )
        # Mark any pending action drafts as resolved — user chose to compose instead
        for r in state.replies:
            if r.reply_review_status == "pending":
                r.reply_review_status = "dismissed"
        # Create an immediate YOUR_REPLY record so it shows in the thread
        from reply_monitor.models import ReplyRecord

        your_reply = ReplyRecord(
            gmail_message_id=_msg_id or f"compose-{now}",
            received_at=now,
            from_addr=account,
            subject=f"Re: {subject}",
            snippet=reply_body,
            tags=["YOUR_REPLY"],
            extracted={},
            llm_used=False,
            has_attachment=False,
            attachment_catalog=None,
        )
        state.replies.append(your_reply)
        save_state(account, states, path=_current_state_path())
        flash("Reply sent.", "success")
    else:
        flash("Failed to send reply. Check Gmail auth.", "danger")

    return redirect(url_for("company_detail", domain=domain, account=account))


@app.route("/company/<domain>/compose-sp-reply", methods=["POST"])
def compose_sp_reply(domain: str):
    """Send a free-form reply in the SP thread."""
    account = request.form.get("account", "")
    reply_body = request.form.get("reply_body", "").strip()
    to_addr = request.form.get("to_addr", "")
    subject = request.form.get("subject", "")
    thread_id = request.form.get("thread_id", "")

    if not reply_body:
        flash("Reply body cannot be empty.", "warning")
        return redirect(url_for("company_detail", domain=domain, account=account))

    from letter_engine.sender import send_thread_reply

    sp_states = load_state(account, path=_current_sp_state_path())
    state = sp_states.get(domain)
    if not state:
        flash("SP state not found.", "danger")
        return redirect(url_for("company_detail", domain=domain, account=account))

    success, _msg_id, _thread_id = send_thread_reply(
        thread_id or state.gmail_thread_id,
        to_addr,
        subject,
        reply_body,
        account,
        tokens_dir=_current_tokens_dir(),
    )
    if success:
        now = (
            datetime.now(timezone.utc)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z")
        )
        for r in state.replies:
            if r.reply_review_status == "pending":
                r.reply_review_status = "dismissed"
        from reply_monitor.models import ReplyRecord

        your_reply = ReplyRecord(
            gmail_message_id=_msg_id or f"compose-{now}",
            received_at=now,
            from_addr=account,
            subject=f"Re: {subject}",
            snippet=reply_body,
            tags=["YOUR_REPLY"],
            extracted={},
            llm_used=False,
            has_attachment=False,
            attachment_catalog=None,
        )
        state.replies.append(your_reply)
        save_state(account, sp_states, path=_current_sp_state_path())
        flash("Reply sent.", "success")
    else:
        flash("Failed to send reply. Check Gmail auth.", "danger")

    return redirect(url_for("company_detail", domain=domain, account=account))


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
    wrong_channel = []

    if account:
        states = _load_all_states(account)
        sp_states = load_state(account, path=_current_sp_state_path())
        sp_sent_domains: set[str] = {
            r.get("domain", "") for r in get_log(path=_current_sp_requests_path())
        }

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

            if best_catalog and (
                best_catalog.get("files") or best_catalog.get("schema")
            ):
                schema = (
                    best_catalog.get("schema", [])
                    if isinstance(best_catalog, dict)
                    else []
                )
                svc_list = (
                    best_catalog.get("services", [])
                    if isinstance(best_catalog, dict)
                    else []
                )
                files = (
                    best_catalog.get("files", [])
                    if isinstance(best_catalog, dict)
                    else []
                )
                total_bytes = (
                    best_catalog.get("size_bytes", 0)
                    if isinstance(best_catalog, dict)
                    else 0
                )
                with_data.append(
                    {
                        "domain": domain,
                        "company_name": state.company_name,
                        "category_count": len(schema),
                        "file_count": len(files),
                        "size_bytes": total_bytes,
                        "received_at": received_at,
                        "services_preview": [s["name"] for s in svc_list[:2]],
                    }
                )
            else:
                card = _build_card(domain, state, status)
                card_dict = {
                    "domain": domain,
                    "company_name": state.company_name,
                    "status": status,
                    "company_status": company_status,
                    "status_color": _STATUS_COLOUR.get(company_status, "secondary"),
                    "days_remaining": card["remaining"],
                    "latest_tag": card["tags"][0] if card["tags"] else "",
                    "tried_emails": card["tried_emails"],
                }
                # Check if any GDPR reply has WRONG_CHANNEL tag
                has_wrong_channel = any(
                    "WRONG_CHANNEL" in r.tags
                    for r in state.replies
                    if "NON_GDPR" not in r.tags
                )
                if has_wrong_channel:
                    # Extract portal URL from the reply if available
                    portal_url = ""
                    portal_verification = None
                    for r in state.replies:
                        if "WRONG_CHANNEL" in r.tags:
                            portal_url = (
                                r.extracted.get("portal_url", "") if r.extracted else ""
                            )
                            portal_verification = r.portal_verification
                            break
                    # Fall back to company record's portal URL (from overrides/resolver)
                    if not portal_url:
                        record = _lookup_company(domain)
                        portal_url = record.get("gdpr_portal_url", "")
                    card_dict["portal_url"] = portal_url
                    card_dict["portal_verification"] = portal_verification
                    wrong_channel.append(card_dict)
                else:
                    without_data.append(card_dict)

    return render_template(
        "cards.html",
        with_data=with_data,
        without_data=without_data,
        wrong_channel=wrong_channel,
        tab=tab,
        account=account,
        accounts=accounts,
    )




@app.route("/company/<domain>/mark-portal-submitted", methods=["POST"])


@app.route("/company/<domain>/mark-portal-submitted", methods=["POST"])
def mark_portal_submitted(domain: str):
    """Manually mark a portal submission as completed by the user."""
    account = request.form.get("account", "")
    if not account:
        flash("No account specified.", "danger")
        return redirect(url_for("company_detail", domain=domain))

    from reply_monitor.state_manager import save_portal_submission

    company_record = _lookup_company(domain)
    p_url = (company_record.get("contact", {}) or {}).get("gdpr_portal_url", "")

    save_portal_submission(
        account,
        domain,
        status="submitted",
        portal_url=p_url,
        confirmation_ref=request.form.get("confirmation_ref", ""),
        data_dir=_current_data_dir(),
    )
    flash(f"Marked {domain} as submitted via portal.", "success")
    return redirect(url_for("company_detail", domain=domain, account=account))


# ===========================================================================
# Pipeline routes
# ===========================================================================

from typing import Any

from dashboard.scan_state import (
    load_scan_state,
    save_scan_state,
)
from dashboard.tasks import (
    start_task,
    get_task,
    find_running_task,
    update_task_progress,
)

_CONFIDENCE_RANK = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}


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
        if d not in sent_by_domain or r.get("sent_at", "") > sent_by_domain[d].get(
            "sent_at", ""
        ):
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
                    current_sent_email = (
                        latest_sent_rec.get("to_email", "") if latest_sent_rec else ""
                    )

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
            already_sent = (
                True  # set by _do_send; bounce detection above may have cleared it
            )
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


def _do_scan(
    task_id: str,
    service: Any,
    account: str,
    known_ids: set[str],
    max_emails: int = 2000,
) -> dict:
    from scanner.inbox_reader import fetch_new_emails, get_inbox_total
    from scanner.service_extractor import extract_services

    # Fetch total inbox size first (single fast API call) so the UI can show X/Y
    inbox_total = get_inbox_total(service)

    def _progress(n: int) -> None:
        update_task_progress(task_id, n)

    new_emails = fetch_new_emails(
        service, known_ids, max_results=max_emails, progress_callback=_progress
    )
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
            if _CONFIDENCE_RANK.get(s["confidence"], 0) > _CONFIDENCE_RANK.get(
                existing["confidence"], 0
            ):
                existing["confidence"] = s["confidence"]
            if s.get("last_seen", "") > existing.get("last_seen", ""):
                existing["last_seen"] = s["last_seen"]

    all_ids = list(
        set(state.get("scanned_message_ids", []))
        | {e["message_id"] for e in new_emails}
    )
    state["scanned_message_ids"] = all_ids
    state["last_scan_at"] = datetime.now(timezone.utc).isoformat()
    if inbox_total > 0:
        state["inbox_total"] = inbox_total
    save_scan_state(account, state, data_dir=_current_data_dir())

    return {
        "new_emails": len(new_emails),
        "new_companies": new_count,
        "total_discovered": len(discovered),
    }


def _do_resolve(
    task_id: str, account: str, unresolved: list[tuple[str, dict]], max_llm: int
) -> dict:
    from contact_resolver import cost_tracker
    from contact_resolver.resolver import ContactResolver

    cost_tracker.set_llm_limit(max_llm)
    resolver = ContactResolver()
    resolved_count = 0

    for i, (domain, data) in enumerate(unresolved):
        update_task_progress(task_id, i, len(unresolved))
        exclude = set(data.get("failed_emails", []))
        record = resolver.resolve(
            domain,
            data["company_name_raw"],
            verbose=False,
            exclude_emails=exclude or None,
        )
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
            letter,
            account,
            data_dir=_current_data_dir(),
            tokens_dir=_current_tokens_dir(),
        )
        if success:
            state = load_scan_state(account, data_dir=_current_data_dir())
            if domain in state.get("discovered_companies", {}):
                state["discovered_companies"][domain]["sar_sent"] = True
            save_scan_state(account, state, data_dir=_current_data_dir())
            sent_count += 1

    update_task_progress(task_id, len(approved_domains), len(approved_domains))
    return {"sent": sent_count, "attempted": len(approved_domains)}


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
    skipped = sum(
        1
        for d in discovered.values()
        if d.get("skipped") and not d.get("contact_resolved")
    )
    needs_human_count = sum(1 for d in discovered.values() if d.get("needs_human"))
    ready = sum(
        1
        for d in discovered.values()
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
            elapsed_minutes = (
                datetime.now(timezone.utc) - last_dt
            ).total_seconds() / 60
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

        _service, authenticated_as = get_gmail_service(
            email_hint=new_account, tokens_dir=_current_tokens_dir()
        )
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
        service, account = get_gmail_service(
            email_hint=account, tokens_dir=_current_tokens_dir()
        )
    except Exception as exc:
        return jsonify(
            {
                "error": f"Gmail auth failed: {exc}. Run `python run.py --dry-run` once to re-authenticate."
            }
        ), 401

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
    save_scan_state(
        account, state, data_dir=_current_data_dir()
    )  # persist cache-hit flags so they aren't re-attempted
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
        review_items.append(
            {
                "domain": domain,
                "company_name": data.get("company_name_raw", record.company_name),
                "confidence": data.get("confidence", "LOW"),
                "method": letter.method,
                "to_email": letter.to_email,
                "portal_url": letter.portal_url,
                "subject": letter.subject,
                "body": letter.body,
                "approved": data.get("approved", False),
            }
        )

    review_items.sort(
        key=lambda x: _CONFIDENCE_RANK.get(x["confidence"], 0), reverse=True
    )

    # Check if the send token is still valid so the template can warn the user
    send_ok, send_auth_error = (
        _send_token_valid(account) if account else (False, "No account")
    )

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
    from contact_resolver.models import CompanyRecord, Contact
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
        return (
            f"<p>Auth failed: {exc}</p><p><a href='/pipeline/review?account={account}'>Back</a></p>",
            500,
        )

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
        domain
        for domain, data in state.get("discovered_companies", {}).items()
        if data.get("approved") and not data.get("sar_sent")
    ]
    if not approved_domains:
        return redirect(url_for("pipeline", account=account))

    task_id = start_task("send", _do_send, account, approved_domains)
    return redirect(url_for("pipeline", account=account, task_id=task_id))


# ---------------------------------------------------------------------------
# API endpoints
# ---------------------------------------------------------------------------



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
            ann.announce(format_sse(f'{{"total_estimate": {total}}}', event="estimate"))

            state = load_scan_state(mailbox, data_dir=data_dir)

            emails = fetch_emails(service, max_results=batch_size)
            services = extract_services(emails)

            for svc in services:
                domain = svc["domain"]
                name = svc["company_name"]
                confidence = svc["confidence"]
                ann.announce(
                    format_sse(
                        f'{{"domain": "{domain}", "name": "{name}", "confidence": "{confidence}"}}',
                        event="service",
                    )
                )

            ann.announce(
                format_sse(
                    f'{{"scanned": {len(emails)}, "services": {len(services)}, "done": true}}',
                    event="progress",
                )
            )

            save_scan_state(
                mailbox,
                {
                    "emails_scanned": state.get("emails_scanned", 0) + len(emails),
                    "total_estimate": total,
                    "services_found": [s["domain"] for s in services],
                    "status": "paused",
                },
                data_dir=data_dir,
            )

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
