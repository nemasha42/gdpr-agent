"""Company blueprint — company detail view, follow-up sending, and portal marking."""

from __future__ import annotations

from datetime import datetime, timezone

from flask import Blueprint, flash, redirect, render_template, request, url_for

from letter_engine.tracker import get_log
from reply_monitor.classifier import _ACTION_DRAFT_TAGS
from reply_monitor.state_manager import (
    compute_company_status,
    compute_status,
    days_remaining,
    load_state,
    save_state,
)
from dashboard.shared import (
    _STATUS_COLOUR,
    _clean_snippet,
    _current_data_dir,
    _current_sp_requests_path,
    _current_sp_state_path,
    _current_state_path,
    _current_tokens_dir,
    _dedup_reply_rows,
    _get_accounts,
    _load_all_states,
    _lookup_company,
)

company_bp = Blueprint("company", __name__)


@company_bp.route("/company/<domain>")
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


@company_bp.route("/company/<domain>/send-followup", methods=["POST"])
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
        return redirect(url_for("company.company_detail", domain=domain, account=account))

    reply = next(
        (r for r in state.replies if r.gmail_message_id == gmail_message_id), None
    )
    if not reply:
        flash("Reply not found.", "danger")
        return redirect(url_for("company.company_detail", domain=domain, account=account))

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

    return redirect(url_for("company.company_detail", domain=domain, account=account))


@company_bp.route("/company/<domain>/dismiss-followup", methods=["POST"])
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

    return redirect(url_for("company.company_detail", domain=domain, account=account))


@company_bp.route("/company/<domain>/send-sp-followup", methods=["POST"])
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
        return redirect(url_for("company.company_detail", domain=domain, account=account))

    reply = next(
        (r for r in state.replies if r.gmail_message_id == gmail_message_id), None
    )
    if not reply:
        flash("Reply not found.", "danger")
        return redirect(url_for("company.company_detail", domain=domain, account=account))

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

    return redirect(url_for("company.company_detail", domain=domain, account=account))


@company_bp.route("/company/<domain>/dismiss-sp-followup", methods=["POST"])
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

    return redirect(url_for("company.company_detail", domain=domain, account=account))


@company_bp.route("/company/<domain>/compose-reply", methods=["POST"])
def compose_reply(domain: str):
    """Send a free-form reply in the SAR thread (no auto-generated draft needed)."""
    account = request.form.get("account", "")
    reply_body = request.form.get("reply_body", "").strip()
    to_addr = request.form.get("to_addr", "")
    subject = request.form.get("subject", "")

    if not reply_body:
        flash("Reply body cannot be empty.", "warning")
        return redirect(url_for("company.company_detail", domain=domain, account=account))

    from letter_engine.sender import send_thread_reply

    states = load_state(account, path=_current_state_path())
    state = states.get(domain)
    if not state:
        flash("Company not found.", "danger")
        return redirect(url_for("company.company_detail", domain=domain, account=account))

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

    return redirect(url_for("company.company_detail", domain=domain, account=account))


@company_bp.route("/company/<domain>/compose-sp-reply", methods=["POST"])
def compose_sp_reply(domain: str):
    """Send a free-form reply in the SP thread."""
    account = request.form.get("account", "")
    reply_body = request.form.get("reply_body", "").strip()
    to_addr = request.form.get("to_addr", "")
    subject = request.form.get("subject", "")
    thread_id = request.form.get("thread_id", "")

    if not reply_body:
        flash("Reply body cannot be empty.", "warning")
        return redirect(url_for("company.company_detail", domain=domain, account=account))

    from letter_engine.sender import send_thread_reply

    sp_states = load_state(account, path=_current_sp_state_path())
    state = sp_states.get(domain)
    if not state:
        flash("SP state not found.", "danger")
        return redirect(url_for("company.company_detail", domain=domain, account=account))

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

    return redirect(url_for("company.company_detail", domain=domain, account=account))


@company_bp.route("/company/<domain>/mark-portal-submitted", methods=["POST"])
def mark_portal_submitted(domain: str):
    """Manually mark a portal submission as completed by the user."""
    account = request.form.get("account", "")
    if not account:
        flash("No account specified.", "danger")
        return redirect(url_for("company.company_detail", domain=domain))

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
    return redirect(url_for("company.company_detail", domain=domain, account=account))
