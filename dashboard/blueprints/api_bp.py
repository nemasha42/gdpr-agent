"""API blueprint — stateless JSON endpoints for task polling and message bodies."""

from __future__ import annotations

from flask import Blueprint, jsonify, request

from dashboard.shared import _current_data_dir, _current_tokens_dir, _get_accounts
from dashboard.tasks import get_task, find_running_task
from dashboard.scan_state import load_scan_state
from dashboard.view_state import mark_viewed

api_bp = Blueprint("api", __name__, url_prefix="/api")


@api_bp.route("/task/<task_id>")
def api_task(task_id: str):
    task = get_task(task_id)
    if not task:
        return jsonify({"error": "unknown task"}), 404
    return jsonify(task)


@api_bp.route("/scan/status")
def api_scan_status():
    account = request.args.get("account", "")
    state = load_scan_state(account, data_dir=_current_data_dir())
    running = find_running_task("scan")
    total_scanned = len(state.get("scanned_message_ids", []))
    inbox_total = state.get("inbox_total", 0)
    return jsonify(
        {
            "in_progress": bool(running),
            "task_id": running["id"] if running else None,
            "progress": running["progress"] if running else 0,
            "last_scan_at": state.get("last_scan_at"),
            "total_scanned_ids": total_scanned,
            "inbox_total": inbox_total,
            "inbox_complete": inbox_total > 0 and total_scanned >= inbox_total,
            "total_discovered": len(state.get("discovered_companies", {})),
        }
    )


@api_bp.route("/mark-viewed/<domain>", methods=["POST"])
def api_mark_viewed(domain: str):
    account = request.args.get("account", "")
    if not account:
        accounts = _get_accounts()
        account = accounts[0] if accounts else ""
    if not account:
        return jsonify({"error": "no account"}), 400
    ts = mark_viewed(account, domain)
    return jsonify({"viewed_at": ts})


@api_bp.route("/body/<domain>/<message_id>")
def api_body(domain: str, message_id: str):
    if not message_id:
        return jsonify(
            {"body": "(no message ID — run monitor to fetch reply bodies)"}
        ), 400
    account = request.args.get("account", "")
    if not account:
        accounts = _get_accounts()
        account = accounts[0] if accounts else ""
    try:
        from auth.gmail_oauth import get_gmail_service
        from reply_monitor.fetcher import _extract_body

        service, _email = get_gmail_service(
            email_hint=account, tokens_dir=_current_tokens_dir()
        )
        msg = (
            service.users()
            .messages()
            .get(userId="me", id=message_id, format="full")
            .execute()
        )
        body = _extract_body(msg.get("payload", {}))
        return jsonify({"body": body or "(empty)"})
    except Exception as exc:
        msg = str(exc)
        if "invalid_grant" in msg or "Token has been expired" in msg:
            friendly = (
                "Gmail auth expired — visit /pipeline/reauth-send to re-authorise"
            )
        elif "404" in msg or "not found" in msg.lower():
            friendly = "Message not found in Gmail (may have been deleted)"
        else:
            friendly = f"Error loading body: {msg}"
        return jsonify({"body": f"({friendly})"}), 500
