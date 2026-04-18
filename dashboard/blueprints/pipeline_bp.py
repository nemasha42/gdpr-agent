"""Pipeline blueprint — scan, resolve, review, approve, send.

Routes:
    GET  /pipeline                 — pipeline dashboard
    POST /pipeline/add-account     — add Gmail account + start scan
    POST /pipeline/scan            — start background scan task
    POST /pipeline/resolve         — start background resolve task
    GET  /pipeline/review          — letter review & approve
    POST /pipeline/manual-contact  — save manual contact for needs-human company
    POST /pipeline/approve         — approve/skip companies
    GET  /pipeline/reauth-send     — re-authorize gmail.send OAuth
    POST /pipeline/send            — start background send task
    GET  /pipeline/scan-page       — progressive SSE scan page
    POST /pipeline/scan/start      — start SSE progressive scan
    GET  /pipeline/scan/stream     — SSE event stream for progressive scan
"""

from __future__ import annotations

import threading
from datetime import date as _date, datetime, timezone
from pathlib import Path
from typing import Any

from flask import (
    Blueprint,
    Response,
    g,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)

from dashboard.scan_state import load_scan_state, save_scan_state
from dashboard.shared import (
    _current_data_dir,
    _current_state_path,
    _current_tokens_dir,
    _get_all_accounts,
    _load_all_states,
    _load_companies_db,
)
from dashboard.sse import MessageAnnouncer, format_sse
from dashboard.tasks import (
    find_running_task,
    start_task,
    update_task_progress,
)
from letter_engine.tracker import get_log
from reply_monitor.state_manager import (
    compute_status,
    domain_from_sent_record,
    load_state,
)

pipeline_bp = Blueprint("pipeline", __name__, url_prefix="/pipeline")

_CONFIDENCE_RANK = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}

# SSE announcers for progressive scan, keyed by user email
_scan_announcers: dict[str, MessageAnnouncer] = {}


# ---------------------------------------------------------------------------
# Pipeline-only helper functions
# ---------------------------------------------------------------------------


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

    # Load reply states to detect STALLED companies
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
            if reply_state and compute_status(reply_state) == "STALLED":
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
#
# THREAD SAFETY: these run in daemon threads (via start_task) without a Flask
# request context.  _current_data_dir() / _current_tokens_dir() depend on
# ``g.data_dir`` which is only set in the before_request hook.  To avoid
# RuntimeError we capture these paths in the route handler and pass them as
# explicit arguments.
# ---------------------------------------------------------------------------


def _do_scan(
    task_id: str,
    service: Any,
    account: str,
    known_ids: set[str],
    max_emails: int,
    data_dir: Path,
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

    state = load_scan_state(account, data_dir=data_dir)
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
    save_scan_state(account, state, data_dir=data_dir)

    return {
        "new_emails": len(new_emails),
        "new_companies": new_count,
        "total_discovered": len(discovered),
    }


def _do_resolve(
    task_id: str,
    account: str,
    unresolved: list[tuple[str, dict]],
    max_llm: int,
    data_dir: Path,
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
        state = load_scan_state(account, data_dir=data_dir)
        if domain in state.get("discovered_companies", {}):
            if record:
                state["discovered_companies"][domain]["contact_resolved"] = True
                resolved_count += 1
            else:
                # Mark as skipped so it isn't retried on the next resolve run
                state["discovered_companies"][domain]["skipped"] = True
        save_scan_state(account, state, data_dir=data_dir)

    update_task_progress(task_id, len(unresolved), len(unresolved))
    return {"resolved": resolved_count, "attempted": len(unresolved)}


def _do_send(
    task_id: str,
    account: str,
    approved_domains: list[str],
    data_dir: Path,
    tokens_dir: Path,
) -> dict:
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
            data_dir=data_dir,
            tokens_dir=tokens_dir,
        )
        if success:
            state = load_scan_state(account, data_dir=data_dir)
            if domain in state.get("discovered_companies", {}):
                state["discovered_companies"][domain]["sar_sent"] = True
            save_scan_state(account, state, data_dir=data_dir)
            sent_count += 1

    update_task_progress(task_id, len(approved_domains), len(approved_domains))
    return {"sent": sent_count, "attempted": len(approved_domains)}


# ---------------------------------------------------------------------------
# Pipeline routes
# ---------------------------------------------------------------------------


@pipeline_bp.route("/")
def pipeline_page() -> str:
    accounts = _get_all_accounts()
    account = request.args.get("account", accounts[0] if accounts else "")

    state = load_scan_state(account, data_dir=_current_data_dir())
    state = _sync_scan_state_flags(account, state)

    discovered = state.get("discovered_companies", {})
    resolved = sum(1 for d in discovered.values() if d.get("contact_resolved"))
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
    monitor_counts = {"WAITING": 0, "OVERDUE": 0}
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


@pipeline_bp.route("/add-account", methods=["POST"])
def pipeline_add_account() -> Response:
    """Authenticate a new Gmail account and immediately start a full scan."""
    new_account = request.form.get("account", "").strip()
    if not new_account:
        return redirect(url_for(".pipeline_page"))
    # Trigger OAuth (will open browser if token missing)
    try:
        from auth.gmail_oauth import get_gmail_service

        _service, authenticated_as = get_gmail_service(
            email_hint=new_account, tokens_dir=_current_tokens_dir()
        )
    except Exception as exc:
        return f"OAuth failed: {exc}", 500

    # Capture data_dir before spawning background thread
    data_dir = _current_data_dir()

    # Start a full scan (known_ids is empty → full inbox scan)
    start_task("scan", _do_scan, _service, authenticated_as, set(), 2000, data_dir)
    return redirect(url_for(".pipeline_page", account=authenticated_as))


@pipeline_bp.route("/scan", methods=["POST"])
def pipeline_scan() -> Response:
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

    # Capture data_dir before spawning background thread
    data_dir = _current_data_dir()

    task_id = start_task(
        "scan", _do_scan, service, account, known_ids, max_emails, data_dir
    )
    return jsonify({"task_id": task_id})


@pipeline_bp.route("/resolve", methods=["POST"])
def pipeline_resolve() -> Response:
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

    # Capture data_dir before spawning background thread
    data_dir = _current_data_dir()

    task_id = start_task("resolve", _do_resolve, account, unresolved, max_llm, data_dir)
    return jsonify({"task_id": task_id, "total": len(unresolved)})


@pipeline_bp.route("/review")
def pipeline_review() -> str:
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


@pipeline_bp.route("/manual-contact", methods=["POST"])
def pipeline_manual_contact() -> Response:
    """Save a user-supplied contact email for a needs-human company and re-queue for review."""
    from contact_resolver.models import CompanyRecord, Contact
    from contact_resolver.resolver import ContactResolver

    account = request.form.get("account", "")
    domain = request.form.get("domain", "").strip()
    manual_email = request.form.get("email", "").strip()

    if not domain or not manual_email or "@" not in manual_email:
        return redirect(url_for(".pipeline_page", account=account))

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

    return redirect(url_for(".pipeline_page", account=account))


@pipeline_bp.route("/approve", methods=["POST"])
def pipeline_approve() -> Response:
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


@pipeline_bp.route("/reauth-send")
def pipeline_reauth_send() -> Response:
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

    return redirect(url_for(".pipeline_review", account=account))


@pipeline_bp.route("/send", methods=["POST"])
def pipeline_send() -> Response:
    account = request.form.get("account", "")

    send_ok, send_err = _send_token_valid(account)
    if not send_ok:
        # Redirect to review page — template will show the re-auth warning
        return redirect(url_for(".pipeline_review", account=account))

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
        return redirect(url_for(".pipeline_page", account=account))

    # Capture data_dir and tokens_dir before spawning background thread
    data_dir = _current_data_dir()
    tokens_dir = _current_tokens_dir()

    task_id = start_task(
        "send", _do_send, account, approved_domains, data_dir, tokens_dir
    )
    return redirect(url_for(".pipeline_page", account=account, task_id=task_id))


# ---------------------------------------------------------------------------
# Progressive scan with SSE
# ---------------------------------------------------------------------------


@pipeline_bp.route("/scan-page")
def scan_page() -> str:
    """Progressive scan page."""
    mailbox = request.args.get("mailbox", "")
    data_dir = _current_data_dir()
    scan_state = {}
    if mailbox:
        scan_state = load_scan_state(mailbox, data_dir=data_dir)
    return render_template("scan.html", mailbox=mailbox, scan_state=scan_state)


@pipeline_bp.route("/scan/start", methods=["POST"])
def scan_start() -> tuple[str, int]:
    """Start or resume a scan for the given mailbox."""
    mailbox = request.form.get("mailbox", "")
    batch_size = int(request.form.get("batch_size", 500))
    user_email = g.user.email

    # Capture data_dir in route context before spawning background thread
    data_dir = _current_data_dir()

    ann = MessageAnnouncer()
    _scan_announcers[user_email] = ann

    def _run_scan() -> None:
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


@pipeline_bp.route("/scan/stream")
def scan_stream() -> Response:
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
