"""Transfers routes — Blueprint extracted from dashboard/app.py (Phase 3).

Routes:
    GET  /transfers                         — data transfer map + D3.js graph
    POST /transfers/fetch                   — start subprocessor fetch task
    POST /transfers/request-letter/<domain> — send SP disclosure request for one company
    POST /transfers/request-all             — send SP disclosure requests to all eligible
    GET  /api/transfers/task                — poll subprocessor task progress

Background task functions:
    _fetch_all_subprocessors()              — fetch subprocessors for all SAR domains
    _send_all_disclosure_requests()         — send SP disclosure letters to all eligible
    is_stale_dict()                         — check staleness of a raw subprocessors dict
"""

from __future__ import annotations

import json
from pathlib import Path

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for

from reply_monitor.state_manager import (
    compute_status,
    load_state,
)
from dashboard.shared import (
    _COMPANIES_PATH,
    _current_data_dir,
    _current_sp_requests_path,
    _current_sp_state_path,
    _current_tokens_dir,
    _get_accounts,
    _load_all_states,
)
from dashboard.tasks import (
    start_task,
    get_task,
    find_running_task,
    update_task_progress,
)

transfers_bp = Blueprint("transfers", __name__)


@transfers_bp.route("/transfers")
def transfers_page():
    """Data Transfer Map — shows subprocessors for all SAR companies."""
    account = request.args.get("account", "")
    accounts = _get_accounts()
    if not account and accounts:
        account = accounts[0]

    try:
        raw = json.loads(_COMPANIES_PATH.read_text())
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
                sp_replies.append(
                    {
                        "received_at": r.received_at,
                        "tags": r.tags,
                        "snippet": r.snippet,
                        "gmail_message_id": r.gmail_message_id,
                    }
                )
        rows.append(
            {
                "domain": domain,
                "company_name": company_name,
                "subprocessors": sp_record,
                "has_email": has_email,
                "request_sent": domain in requested_domains,
                "sp_status": sp_status,
                "sp_replies": sp_replies,
            }
        )
    rows.sort(key=lambda r: r["company_name"].lower())

    # Build graph data for D3.js visualization
    from dashboard.services.graph_data import build_graph_data

    max_depth = request.args.get("depth", 4, type=int)
    max_depth = max(1, min(max_depth, 6))
    graph = build_graph_data(rows, companies_raw, max_depth=max_depth)
    graph_json = json.dumps(graph)

    running_task = find_running_task("subprocessors")
    running_request_task = find_running_task("subprocessor_requests")
    return render_template(
        "transfers.html",
        rows=rows,
        account=account,
        accounts=accounts,
        running_task=running_task,
        running_request_task=running_request_task,
        graph_json=graph_json,
        graph_depth=max_depth,
    )


@transfers_bp.route("/transfers/fetch", methods=["POST"])
def transfers_fetch():
    """Start background task to fetch subprocessors for all SAR domains."""
    account = request.form.get("account", "")
    if not account:
        accounts = _get_accounts()
        account = accounts[0] if accounts else ""

    if find_running_task("subprocessors"):
        return redirect(url_for(".transfers_page", account=account))

    start_task("subprocessors", _fetch_all_subprocessors, account)
    return redirect(url_for(".transfers_page", account=account))


@transfers_bp.route("/transfers/request-letter/<path:domain>", methods=["POST"])
def transfers_request_letter(domain: str):
    """Compose and send a subprocessor disclosure request letter for one company."""
    account = request.form.get("account", "")
    if not account:
        accounts = _get_accounts()
        account = accounts[0] if accounts else ""

    try:
        raw = json.loads(_COMPANIES_PATH.read_text())
        companies_raw = raw.get("companies", raw)
    except Exception:
        companies_raw = {}

    company_raw = companies_raw.get(domain)
    if not company_raw:
        flash(f"No company record for {domain}.", "warning")
        return redirect(url_for(".transfers_page", account=account))

    from contact_resolver.models import CompanyRecord
    from letter_engine.composer import compose_subprocessor_request
    from letter_engine.sender import send_letter
    from letter_engine.tracker import record_subprocessor_request

    try:
        record = CompanyRecord.model_validate(company_raw)
    except Exception:
        flash(f"Could not load company record for {domain}.", "danger")
        return redirect(url_for(".transfers_page", account=account))

    # Fall back to the email used for the SAR if the record has no privacy/dpo email
    sar_email = ""
    states = _load_all_states(account)
    sar_state = states.get(domain)
    if sar_state:
        sar_email = sar_state.to_email

    letter = compose_subprocessor_request(record, to_email_override=sar_email)
    if not letter:
        flash(f"No email contact found for {domain}.", "warning")
        return redirect(url_for(".transfers_page", account=account))

    success, msg_id, thread_id = send_letter(
        letter,
        account,
        record=False,
        tokens_dir=_current_tokens_dir(),
    )
    if success:
        letter.gmail_message_id = msg_id
        letter.gmail_thread_id = thread_id
        record_subprocessor_request(letter, domain, data_dir=_current_data_dir())
        flash(f"Disclosure request sent to {letter.to_email}.", "success")
    else:
        flash(f"Failed to send disclosure request for {domain}.", "danger")

    return redirect(url_for(".transfers_page", account=account))


@transfers_bp.route("/transfers/request-all", methods=["POST"])
def transfers_request_all():
    """Start background task: send disclosure requests to all eligible companies."""
    account = request.form.get("account", "")
    if not account:
        accounts = _get_accounts()
        account = accounts[0] if accounts else ""

    if find_running_task("subprocessor_requests"):
        return redirect(url_for(".transfers_page", account=account))

    # THREAD SAFETY: capture Flask request-context paths BEFORE spawning thread.
    captured_sp_requests_path = _current_sp_requests_path()
    captured_tokens_dir = _current_tokens_dir()
    captured_data_dir = _current_data_dir()

    start_task(
        "subprocessor_requests",
        _send_all_disclosure_requests,
        account,
        captured_sp_requests_path,
        captured_tokens_dir,
        captured_data_dir,
    )
    return redirect(url_for(".transfers_page", account=account))


@transfers_bp.route("/api/transfers/task")
def transfers_task_status():
    """Return JSON status of the most recent subprocessors task."""
    task_id = request.args.get("task_id", "")
    task = get_task(task_id) if task_id else find_running_task("subprocessors")
    if not task:
        return jsonify({"status": "idle"})
    return jsonify(task)


# ---------------------------------------------------------------------------
# Background task functions (run in daemon threads via dashboard.tasks)
# ---------------------------------------------------------------------------


def _fetch_all_subprocessors(task_id: str, account: str) -> dict:
    """Background task: fetch subprocessors for all SAR domains."""
    import os

    from contact_resolver.subprocessor_fetcher import fetch_subprocessors
    from contact_resolver.resolver import write_subprocessors

    api_key = os.environ.get("ANTHROPIC_API_KEY")

    states = _load_all_states(account)
    domains = list(states.keys())

    try:
        raw = json.loads(_COMPANIES_PATH.read_text())
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
        if (
            existing
            and existing.get("fetch_status") == "ok"
            and not is_stale_dict(existing)
        ):
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


def _send_all_disclosure_requests(
    task_id: str,
    account: str,
    sp_requests_path: Path,
    tokens_dir: Path,
    data_dir: Path,
) -> dict:
    """Background task: send subprocessor disclosure request letters to all eligible companies.

    Path arguments (sp_requests_path, tokens_dir, data_dir) are captured from Flask
    request context by the calling route BEFORE the background thread is spawned, to
    avoid accessing Flask's ``g`` object from a non-request thread.
    """
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
        raw = json.loads(_COMPANIES_PATH.read_text())
        companies_raw = raw.get("companies", raw)
    except Exception:
        companies_raw = {}

    # Build set of domains already requested
    _req_log = _get_letter_log(path=sp_requests_path)
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

        # Fall back to the email used for the SAR
        sar_email = ""
        sar_state = states.get(domain)
        if sar_state:
            sar_email = sar_state.to_email

        letter = compose_subprocessor_request(record, to_email_override=sar_email)
        if not letter:
            skipped += 1
            continue

        success, msg_id, thread_id = send_letter(
            letter,
            account,
            record=False,
            tokens_dir=tokens_dir,
        )
        if success:
            letter.gmail_message_id = msg_id
            letter.gmail_thread_id = thread_id
            record_subprocessor_request(letter, domain, data_dir=data_dir)
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
