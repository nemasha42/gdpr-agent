"""Settings blueprint — data export and account deletion."""

from __future__ import annotations

import io
import zipfile

from flask import (
    Blueprint,
    Response,
    g,
    redirect,
    url_for,
)
from flask import current_app

from dashboard.shared import _USER_DATA, _current_data_dir
from dashboard.user_model import _safe_email

settings_bp = Blueprint("settings", __name__, url_prefix="/settings")


@settings_bp.route("/export")
def export_data():
    """Download a zip of the user's data directory."""
    data_dir = _current_data_dir()
    if not data_dir.exists():
        return "No data found.", 404

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in data_dir.rglob("*"):
            if file_path.is_file():
                arcname = file_path.relative_to(data_dir)
                zf.write(file_path, arcname)

    buf.seek(0)
    safe = _safe_email(g.user.email)
    return Response(
        buf.getvalue(),
        mimetype="application/zip",
        headers={"Content-Disposition": f"attachment; filename=gdpr-agent-{safe}.zip"},
    )


@settings_bp.route("/delete-account", methods=["POST"])
def delete_account():
    """Delete the current user's account and all their data."""
    import shutil
    from dashboard.user_model import delete_user

    email = g.user.email
    data_dir = _current_data_dir()

    if data_dir.exists():
        shutil.rmtree(data_dir)

    delete_user(
        email,
        path=current_app.config.get("USERS_PATH", _USER_DATA / "users.json"),
    )

    from flask_login import logout_user

    logout_user()

    return redirect(url_for("auth.login"))
