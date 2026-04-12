"""Admin routes: invite users, manage users."""

from __future__ import annotations

from pathlib import Path
from functools import wraps

from flask import (
    Blueprint, current_app, render_template, request, abort,
)
from flask_login import current_user, login_required

from dashboard.user_model import (
    generate_invite_token, load_users,
)

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


def _users_path() -> Path:
    return current_app.config.get("USERS_PATH", Path("user_data/users.json"))


def admin_required(f):
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if not current_user.is_admin:
            abort(403)
        return f(*args, **kwargs)
    return decorated


@admin_bp.route("/invite", methods=["GET", "POST"])
@admin_required
def invite():
    invite_link = None
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        if email:
            token = generate_invite_token(
                email, secret_key=current_app.config["SECRET_KEY"]
            )
            invite_link = f"{request.host_url}join/{token}"

    return render_template("admin_invite.html", invite_link=invite_link)


@admin_bp.route("/users")
@admin_required
def users_list():
    users = load_users(path=_users_path())
    return render_template("admin_users.html", users=users)
