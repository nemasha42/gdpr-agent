"""Monitor blueprint — refresh and reextract routes."""

from __future__ import annotations

from flask import Blueprint, redirect, request, url_for

from dashboard.shared import (
    _get_accounts,
    _current_data_dir,
    _current_state_path,
    _current_sp_state_path,
    _current_tokens_dir,
    _current_sp_requests_path,
)

monitor_bp = Blueprint("monitor", __name__)


@monitor_bp.route("/reextract")
def reextract():
    """Re-fetch Gmail bodies for replies with DATA_PROVIDED_LINK but empty data_link."""
    account = request.args.get("account", "")
    if not account:
        accounts = _get_accounts()
        account = accounts[0] if accounts else ""
    if account:
        from dashboard.services.monitor_runner import reextract_missing_links

        try:
            reextract_missing_links(
                account,
                state_path=_current_state_path(),
                tokens_dir=_current_tokens_dir(),
            )
        except Exception as exc:
            print(f"[reextract] Error for {account}: {exc}")
    return redirect(url_for("dashboard", account=account))


@monitor_bp.route("/refresh")
def refresh():
    """Run monitor inline for the selected account and redirect to dashboard."""
    account = request.args.get("account", "")
    if account:
        from dashboard.services.monitor_runner import (
            reextract_missing_links,
            run_sar_monitor,
            run_sp_monitor,
        )

        data_dir = _current_data_dir()
        state_path = _current_state_path()
        tokens_dir = _current_tokens_dir()
        sp_requests_path = _current_sp_requests_path()
        sp_state_path = _current_sp_state_path()

        _service = None
        _email = ""
        try:
            _service, _email, _states, _counts = run_sar_monitor(
                account,
                state_path=state_path,
                tokens_dir=tokens_dir,
                data_dir=data_dir,
                sp_requests_path=sp_requests_path,
            )
        except Exception as exc:
            print(f"[refresh] Monitor error for {account}: {exc}")
        try:
            run_sp_monitor(
                account,
                state_path=state_path,
                tokens_dir=tokens_dir,
                data_dir=data_dir,
                sp_requests_path=sp_requests_path,
                sp_state_path=sp_state_path,
                service=_service,
                email=_email,
            )
        except Exception as exc:
            print(f"[refresh] Subprocessor monitor error for {account}: {exc}")
        try:
            reextract_missing_links(
                account,
                state_path=state_path,
                tokens_dir=tokens_dir,
            )
        except Exception as exc:
            print(f"[refresh] Re-extract error for {account}: {exc}")
    return redirect(url_for("dashboard", account=account))
