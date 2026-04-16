"""Data blueprint — data card viewing, folder scanning, and download routes."""

from __future__ import annotations

import os
from pathlib import Path

from flask import Blueprint, redirect, render_template, request, url_for

from reply_monitor.state_manager import (
    load_state,
    save_state,
)
from dashboard.shared import (
    _USER_DATA,
    _get_accounts,
    _load_all_states,
    _lookup_company,
    _current_state_path,
)

data_bp = Blueprint("data", __name__)


@data_bp.route("/data/<domain>")
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
    has_local_files = (
        received_dir.exists()
        and any(
            f.suffix.lower().lstrip(".") in ("zip", "json", "csv")
            for f in received_dir.iterdir()
            if f.is_file()
        )
        if received_dir.exists()
        else False
    )

    folder_path = str(received_dir) if catalog else ""

    # Company info from companies.json
    company_record = _lookup_company(domain)
    legal_name = company_record.get("legal_entity_name", "")
    postal = company_record.get("postal_address", {}) or {}
    city = postal.get("city", "")
    country = postal.get("country", "")
    headquarters = ", ".join(filter(None, [city, country]))
    dpo_email = company_record.get("dpo_email", "") or company_record.get(
        "privacy_email", ""
    )
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


@data_bp.route("/scan/<domain>")
def scan_folder(domain: str):
    """Scan user_data/received/<domain>/ for downloaded data files, catalog + LLM-analyze."""
    account = request.args.get("account", "")
    if not account:
        accounts = _get_accounts()
        account = accounts[0] if accounts else ""

    received_dir = _USER_DATA / "received" / domain
    if not received_dir.exists():
        return redirect(
            url_for("data.data_card", domain=domain, account=account, scan_error="no_files")
        )

    candidates = sorted(
        [
            f
            for f in received_dir.iterdir()
            if f.is_file()
            and f.suffix.lower().lstrip(".") in ("zip", "json", "csv", "js")
        ],
        key=lambda f: f.stat().st_mtime,
        reverse=True,
    )
    if not candidates:
        return redirect(
            url_for("data.data_card", domain=domain, account=account, scan_error="no_files")
        )

    from reply_monitor.attachment_handler import (
        _catalog_csv,
        _catalog_json,
        _catalog_zip,
    )
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
        files = [
            FileEntry(
                filename=file_path.name, size_bytes=len(data), file_type=ext or "bin"
            )
        ]
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
    display_categories = (
        [s["name"] for s in schema] if schema else sorted(set(categories))
    )

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
            (
                r
                for r in state.replies
                if any(t.startswith("DATA_PROVIDED") for t in r.tags)
            ),
            state.replies[-1] if state.replies else None,
        )
        if target:
            target.attachment_catalog = catalog.to_dict()
            save_state(account, states, path=_current_state_path())

    return redirect(url_for("data.data_card", domain=domain, account=account))


@data_bp.route("/download/<domain>")
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
        return redirect(url_for("data.data_card", domain=domain, account=account))

    result = download_data_link(
        target_reply.extracted["data_link"],
        domain,
        api_key=os.environ.get("ANTHROPIC_API_KEY") or "",
    )

    if result.ok:
        target_reply.attachment_catalog = result.catalog.to_dict()
        save_state(account, states, path=_current_state_path())
        return redirect(url_for("data.data_card", domain=domain, account=account))

    if result.too_large:
        error = "too_large"
    elif result.expired:
        error = "expired"
    else:
        error = result.error[:100]
    return redirect(
        url_for("data.data_card", domain=domain, account=account, download_error=error)
    )
