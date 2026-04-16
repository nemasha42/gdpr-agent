"""Dashboard blueprint — main dashboard and data cards listing."""

from __future__ import annotations

from flask import Blueprint, render_template, request

from letter_engine.tracker import get_log
from reply_monitor.state_manager import (
    _COMPANY_STATUS_PRIORITY,
    compute_company_status,
    compute_status,
    load_state,
)
from dashboard.scan_state import load_scan_state
from dashboard.shared import (
    _STATUS_COLOUR,
    _build_card,
    _current_data_dir,
    _current_sp_requests_path,
    _current_sp_state_path,
    _get_accounts,
    _load_all_states,
    _lookup_company,
)

dashboard_bp = Blueprint("main", __name__)


@dashboard_bp.route("/")
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


@dashboard_bp.route("/cards")
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
