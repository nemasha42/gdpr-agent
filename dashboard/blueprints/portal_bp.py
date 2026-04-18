"""Portal submission routes — Blueprint extracted from dashboard/app.py (Phase 3).

Routes:
    POST /portal/submit/<domain>  — start background portal submission
    GET  /portal/status/<domain>  — poll portal submission progress
    POST /portal/verify/<domain>  — mark portal verification as passed
    GET  /captcha/<domain>        — show pending CAPTCHA for user to solve
    POST /captcha/<domain>        — submit CAPTCHA solution
"""

from __future__ import annotations

import base64
import json
import threading as _threading
from pathlib import Path

from flask import Blueprint, flash, jsonify, redirect, render_template, request, url_for

from reply_monitor.state_manager import (
    save_state,
    set_portal_status,
    verify_portal,
)
from dashboard.shared import (
    _current_data_dir,
    _current_state_path,
    _load_all_states,
    _lookup_company,
)

portal_bp = Blueprint("portal", __name__)

# In-memory portal task tracking — single source of truth.
# Keyed by domain → {"status": "running"|"done"|"error", "result": PortalResult|str|None}
_portal_tasks: dict[str, dict] = {}


@portal_bp.route("/portal/submit/<domain>", methods=["POST"])
def portal_submit(domain: str):
    """Start a portal submission as a background task."""
    account = request.args.get("account", "")
    portal_url_param = request.args.get("portal_url", "")

    if domain in _portal_tasks and _portal_tasks[domain].get("status") == "running":
        return jsonify({"error": "submission already in progress"}), 409

    # Resolve portal URL: explicit param > resolver > overrides
    from contact_resolver.resolver import ContactResolver
    from letter_engine.composer import compose
    from letter_engine.models import SARLetter

    resolver = ContactResolver()
    record = resolver.resolve(domain, domain, verbose=False)

    # Determine portal URL from all available sources
    effective_portal_url = portal_url_param
    if not effective_portal_url and record:
        effective_portal_url = record.contact.gdpr_portal_url
    if not effective_portal_url:
        company_rec = _lookup_company(domain)
        effective_portal_url = (company_rec.get("contact", {}) or {}).get(
            "gdpr_portal_url", ""
        )

    if not effective_portal_url:
        return jsonify({"error": "no portal URL found for this company"}), 400

    # Build the letter — use compose() if we have a record, otherwise build minimal
    if record:
        letter = compose(record)
        letter.portal_url = effective_portal_url
        letter.method = "portal"
    else:
        company_name = domain
        letter = SARLetter(
            company_name=company_name,
            method="portal",
            to_email="",
            subject=f"Subject Access Request - {company_name}",
            body=f"I am writing to make a Subject Access Request for {company_name}.",
            portal_url=effective_portal_url,
            postal_address="",
        )

    _portal_tasks[domain] = {"status": "running", "result": None}

    # THREAD SAFETY: capture Flask request-context paths BEFORE spawning thread.
    # Background threads lack Flask's request context, so _current_data_dir() /
    # _current_state_path() would fall back to _USER_DATA silently. Capture now
    # while we still have the request context.
    captured_data_dir = _current_data_dir()
    captured_state_path = _current_state_path()

    def _run():
        try:
            from portal_submitter import submit_portal

            result = submit_portal(letter, scan_email=account)
            _portal_tasks[domain] = {"status": "done", "result": result}

            # Save portal submission status to reply_state.json
            if result.success or result.needs_manual:
                from reply_monitor.state_manager import save_portal_submission

                ps_status = "submitted" if result.success else "manual"
                save_portal_submission(
                    account,
                    domain,
                    status=ps_status,
                    portal_url=letter.portal_url or "",
                    confirmation_ref=result.confirmation_ref or "",
                    error=result.error or "",
                    data_dir=captured_data_dir,
                )

                # Also update CompanyState in reply_state.json
                try:
                    states = _load_all_states(account)
                    state = states.get(domain)
                    if state:
                        set_portal_status(
                            state,
                            result.portal_status,
                            confirmation_ref=result.confirmation_ref,
                            screenshot=result.screenshot_path,
                        )
                        save_state(account, states, path=captured_state_path)
                except Exception:
                    pass  # tracker is the primary record; state sync is best-effort
        except Exception as exc:
            _portal_tasks[domain] = {"status": "error", "result": str(exc)}
            try:
                from reply_monitor.state_manager import save_portal_submission

                save_portal_submission(
                    account,
                    domain,
                    status="failed",
                    error=str(exc),
                    data_dir=captured_data_dir,
                )
            except Exception:
                pass

    _threading.Thread(target=_run, daemon=True).start()
    return jsonify({"status": "started"})


@portal_bp.route("/portal/status/<domain>")
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

    return jsonify(
        {
            "status": "done",
            "success": result.success,
            "needs_manual": result.needs_manual,
            "portal_status": result.portal_status,
            "confirmation_ref": result.confirmation_ref,
            "error": result.error,
        }
    )


@portal_bp.route("/portal/verify/<domain>", methods=["POST"])
def portal_verify(domain: str):
    """Mark portal verification as passed — restarts 30-day deadline."""
    account = request.args.get("account", "")
    states = _load_all_states(account)
    state = states.get(domain)
    if not state:
        return jsonify({"error": "domain not found"}), 404

    verify_portal(state)
    save_state(account, states, path=_current_state_path())
    return jsonify(
        {
            "status": "ok",
            "portal_status": state.portal_status,
            "deadline": state.deadline,
            "portal_verified_at": state.portal_verified_at,
        }
    )


@portal_bp.route("/captcha/<domain>")
def captcha_show(domain: str):
    """Show a pending CAPTCHA for the user to solve."""
    captcha_dir = Path(__file__).parent.parent.parent / "user_data" / "captcha_pending"
    screenshot = captcha_dir / f"{domain}.png"
    challenge_file = captcha_dir / f"{domain}.json"

    if not screenshot.exists() or not challenge_file.exists():
        flash("No pending CAPTCHA for this domain.", "warning")
        return redirect(url_for("main.dashboard"))

    img_b64 = base64.b64encode(screenshot.read_bytes()).decode()
    challenge = json.loads(challenge_file.read_text())

    return render_template(
        "captcha.html",
        domain=domain,
        captcha_image=img_b64,
        portal_url=challenge.get("portal_url", ""),
    )


@portal_bp.route("/captcha/<domain>", methods=["POST"])
def captcha_solve(domain: str):
    """Submit a CAPTCHA solution."""
    solution = request.form.get("solution", "").strip()
    if not solution:
        flash("Please enter the CAPTCHA solution.", "warning")
        return redirect(url_for(".captcha_show", domain=domain))

    captcha_dir = Path(__file__).parent.parent.parent / "user_data" / "captcha_pending"
    challenge_file = captcha_dir / f"{domain}.json"

    if challenge_file.exists():
        data = json.loads(challenge_file.read_text())
        data["status"] = "solved"
        data["solution"] = solution
        challenge_file.write_text(json.dumps(data, indent=2))
        flash("CAPTCHA solution submitted. Portal submission continuing...", "success")
    else:
        flash("CAPTCHA challenge not found or already expired.", "warning")

    return redirect(url_for("main.dashboard"))
