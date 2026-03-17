"""Flask dashboard for GDPR SAR monitoring.

Routes:
    GET /                       — account selector + all company cards
    GET /company/<domain>       — full reply thread for one company
    GET /data/<domain>          — data card (requires attachment received)
    GET /refresh?account=EMAIL  — run monitor inline, redirect to /
"""

from __future__ import annotations

import sys
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

from flask import Flask, redirect, render_template, request, url_for

from letter_engine.tracker import get_log
from reply_monitor.state_manager import (
    compute_status,
    days_remaining,
    domain_from_sent_record,
    load_state,
    save_state,
    status_sort_key,
)

app = Flask(__name__, template_folder="templates")
app.secret_key = "gdpr-agent-dashboard"  # non-sensitive local app

_USER_DATA = _PROJECT_ROOT / "user_data"
_STATE_PATH = _USER_DATA / "reply_state.json"
_COMPANIES_PATH = _PROJECT_ROOT / "data" / "companies.json"

# ---------------------------------------------------------------------------
# Status → CSS colour class mapping
# ---------------------------------------------------------------------------
# Statuses where the GDPR deadline no longer applies — hide countdown
_TERMINAL_STATUSES = {"COMPLETED", "BOUNCED", "DENIED"}

_STATUS_COLOUR = {
    "OVERDUE":         "danger",
    "ACTION_REQUIRED": "warning",
    "BOUNCED":         "secondary",
    "DENIED":          "secondary",
    "COMPLETED":       "success",
    "EXTENDED":        "warning",
    "ACKNOWLEDGED":    "info",
    "PENDING":         "primary",
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
    "HUMAN_REVIEW":          "danger",
    "NON_GDPR":              "light",
}

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
    return {
        "status_colour": _STATUS_COLOUR,
        "tag_colour": _TAG_COLOUR,
    }


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _get_accounts() -> list[str]:
    """Return all account emails found in reply_state.json."""
    if not _STATE_PATH.exists():
        return []
    import json
    try:
        data = json.loads(_STATE_PATH.read_text())
    except Exception:
        return []
    accounts = []
    for safe_key in data.keys():
        # Reverse _safe_email: trader1620_at_gmail_com → trader1620@gmail.com
        if "_at_" in safe_key:
            local, domain = safe_key.split("_at_", 1)
            accounts.append(f"{local}@{domain.replace('_', '.')}")
        else:
            accounts.append(safe_key)
    return accounts


def _build_card(domain: str, state, status: str) -> dict:
    """Build a flat dict for the dashboard card template."""
    if status in _TERMINAL_STATUSES:
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

    all_tags: list[str] = []
    for r in gdpr_replies:
        for t in r.tags:
            if t not in all_tags:
                all_tags.append(t)

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

    # Data ready hint
    if status == "COMPLETED":
        for r in gdpr_replies:
            if "DATA_PROVIDED_LINK" in r.tags and r.extracted.get("data_link"):
                action_hint = "Download data"
                action_hint_url = r.extracted["data_link"]
                break
            if "DATA_PROVIDED_PORTAL" in r.tags:
                action_hint = "Access your data via their account portal"
                break
            if "DATA_PROVIDED_ATTACHMENT" in r.tags:
                action_hint = "Data ready — open folder in user_data/received/"
                break

    has_data = status == "COMPLETED" and any(
        any(t.startswith("DATA_PROVIDED") for t in r.tags) for r in gdpr_replies
    )

    return {
        "domain": domain,
        "company_name": state.company_name,
        "status": status,
        "to_email": state.to_email,
        "sar_sent_at": state.sar_sent_at[:10],
        "deadline": state.deadline,
        "remaining": remaining,
        "pct": pct,
        "progress_colour": progress_colour,
        "tags": all_tags,
        "reply_count": len(gdpr_replies),
        "non_gdpr_count": non_gdpr_count,
        "latest_snippet": latest_snippet,
        "action_hint": action_hint,
        "action_hint_url": action_hint_url,
        "has_data": has_data,
    }


def _lookup_company(domain: str) -> dict:
    """Return company record from companies.json for Company Info tab."""
    try:
        import json
        data = json.loads(_COMPANIES_PATH.read_text())
        return data.get(domain, {})
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
        states = load_state(account, path=_STATE_PATH)

        # Also surface companies from sent_letters.json that have no state yet
        sent_log = get_log()
        seen_domains: set[str] = set()
        for record in sent_log:
            d = domain_from_sent_record(record)
            if d in seen_domains:
                continue
            seen_domains.add(d)
            if d not in states:
                # Create a minimal pending state for display
                from reply_monitor.models import CompanyState
                from reply_monitor.state_manager import deadline_from_sent
                states[d] = CompanyState(
                    domain=d,
                    company_name=record.get("company_name", d),
                    sar_sent_at=record.get("sent_at", ""),
                    to_email=record.get("to_email", ""),
                    subject=record.get("subject", ""),
                    gmail_thread_id=record.get("gmail_thread_id", ""),
                    deadline=deadline_from_sent(record.get("sent_at", "")),
                )

        for domain, state in states.items():
            status = compute_status(state)
            cards.append(_build_card(domain, state, status))

        # Sort by urgency
        cards.sort(key=lambda c: status_sort_key(c["status"]), reverse=True)

    return render_template(
        "dashboard.html",
        cards=cards,
        accounts=accounts,
        selected_account=account,
    )


@app.route("/company/<domain>")
def company_detail(domain: str):
    account = request.args.get("account", "")
    if not account:
        accounts = _get_accounts()
        account = accounts[0] if accounts else ""

    states = load_state(account, path=_STATE_PATH)
    state = states.get(domain)
    if not state:
        return f"No data found for {domain}", 404

    status = compute_status(state)

    # Build per-reply data for template
    reply_rows = []
    for r in reversed(state.replies):
        reply_rows.append({
            "received_at": r.received_at[:19].replace("T", " "),
            "from_addr": r.from_addr,
            "subject": r.subject,
            "snippet": r.snippet,
            "tags": r.tags,
            "extracted": r.extracted,
            "llm_used": r.llm_used,
            "has_attachment": r.has_attachment,
            "non_gdpr": "NON_GDPR" in r.tags,
            "gmail_message_id": r.gmail_message_id,
        })

    return render_template(
        "company_detail.html",
        domain=domain,
        state=state,
        status=status,
        status_colour=_STATUS_COLOUR.get(status, "secondary"),
        reply_rows=reply_rows,
        account=account,
    )


@app.route("/data/<domain>")
def data_card(domain: str):
    account = request.args.get("account", "")
    scan_error = request.args.get("scan_error", "")
    download_error = request.args.get("download_error", "")
    tab = request.args.get("tab", "data_description")
    if not account:
        accounts = _get_accounts()
        account = accounts[0] if accounts else ""

    states = load_state(account, path=_STATE_PATH)
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
        states = load_state(account, path=_STATE_PATH)

        for domain, state in states.items():
            status = compute_status(state)
            # Find best catalog across all replies
            best_catalog = None
            received_at = ""
            for r in state.replies:
                if "NON_GDPR" in r.tags:
                    continue
                if r.attachment_catalog and not best_catalog:
                    best_catalog = r.attachment_catalog
                    received_at = r.received_at[:10]

            if best_catalog:
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
                    "status_color": _STATUS_COLOUR.get(status, "secondary"),
                    "days_remaining": None if status in _TERMINAL_STATUSES else days_remaining(state.sar_sent_at),
                    "latest_tag": card["tags"][0] if card["tags"] else "",
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
            if f.is_file() and f.suffix.lower().lstrip(".") in ("zip", "json", "csv")
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
    elif ext == "json":
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
    states = load_state(account, path=_STATE_PATH)
    state = states.get(domain)
    if state:
        target = next(
            (r for r in state.replies if any(t.startswith("DATA_PROVIDED") for t in r.tags)),
            state.replies[-1] if state.replies else None,
        )
        if target:
            target.attachment_catalog = catalog.to_dict()
            save_state(account, states, path=_STATE_PATH)

    return redirect(url_for("data_card", domain=domain, account=account))


@app.route("/download/<domain>")
def download_data(domain: str):
    """Trigger link_downloader for a domain, update state, redirect to data card."""
    account = request.args.get("account", "")
    if not account:
        accounts = _get_accounts()
        account = accounts[0] if accounts else ""

    from reply_monitor.link_downloader import download_data_link

    states = load_state(account, path=_STATE_PATH)
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
        save_state(account, states, path=_STATE_PATH)
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
        try:
            _run_monitor_for_account(account)
        except Exception as exc:
            print(f"[refresh] Monitor error for {account}: {exc}")
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

    states = load_state(account, path=_STATE_PATH)
    needs_update = False
    for domain, state in states.items():
        for reply in state.replies:
            if "DATA_PROVIDED_LINK" in reply.tags and not reply.extracted.get("data_link"):
                try:
                    service, _email = get_gmail_service(email_hint=account)
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
        save_state(account, states, path=_STATE_PATH)
        # Reload updated state and trigger auto-download for any newly extracted URLs
        import os
        states = load_state(account, path=_STATE_PATH)
        _auto_download_data_links(account, states, os.environ.get("ANTHROPIC_API_KEY"))

    return sum(
        1 for state in states.values()
        for r in state.replies
        if "DATA_PROVIDED_LINK" in r.tags and r.extracted.get("data_link")
    )


def _run_monitor_for_account(account: str) -> None:
    """Inline monitor run — same logic as monitor.py but without printing."""
    from auth.gmail_oauth import get_gmail_service
    from reply_monitor.attachment_handler import handle_attachment
    from reply_monitor.classifier import classify
    from reply_monitor.fetcher import fetch_replies_for_sar
    from reply_monitor.models import ReplyRecord
    from reply_monitor.state_manager import (
        deadline_from_sent,
        domain_from_sent_record,
        update_state,
    )

    service, email = get_gmail_service(email_hint=account)
    sent_log = get_log()
    states = load_state(email, path=_STATE_PATH)

    api_key = None
    try:
        import os
        api_key = os.environ.get("ANTHROPIC_API_KEY")
    except Exception:
        pass

    for record in sent_log:
        domain = domain_from_sent_record(record)
        if domain not in states:
            from reply_monitor.models import CompanyState
            states[domain] = CompanyState(
                domain=domain,
                company_name=record.get("company_name", domain),
                sar_sent_at=record.get("sent_at", ""),
                to_email=record.get("to_email", ""),
                subject=record.get("subject", ""),
                gmail_thread_id=record.get("gmail_thread_id", ""),
                deadline=deadline_from_sent(record.get("sent_at", "")),
            )

        state = states[domain]
        existing_ids = {r.gmail_message_id for r in state.replies}
        new_messages = fetch_replies_for_sar(service, record, existing_ids, user_email=email)

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

    save_state(email, states, path=_STATE_PATH)

    # Auto-download any DATA_PROVIDED_LINK replies that have a URL but no catalog yet
    _auto_download_data_links(email, states, api_key)


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
        save_state(account, states, path=_STATE_PATH)



@app.route("/api/body/<domain>/<message_id>")
def api_body(domain: str, message_id: str):
    from flask import jsonify
    account = request.args.get("account", "")
    if not account:
        accounts = _get_accounts()
        account = accounts[0] if accounts else ""
    try:
        from auth.gmail_oauth import get_gmail_service
        from reply_monitor.fetcher import _extract_body
        service, _email = get_gmail_service(email_hint=account)
        msg = service.users().messages().get(
            userId="me", id=message_id, format="full"
        ).execute()
        body = _extract_body(msg.get("payload", {}))
        return jsonify({"body": body or "(empty)"})
    except Exception as exc:
        return jsonify({"body": f"(error: {exc})"}), 500


if __name__ == "__main__":
    app.run(debug=True, port=5001)
