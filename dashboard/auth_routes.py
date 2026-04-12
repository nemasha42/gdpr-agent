"""Authentication routes: invite, login, logout, OAuth callback."""

from __future__ import annotations

from pathlib import Path

from flask import (
    Blueprint, current_app, flash, redirect, render_template,
    request, session, url_for,
)
from flask_login import current_user, login_required, login_user, logout_user

from dashboard.user_model import (
    User, generate_invite_token, load_user, save_user,
    validate_invite_token, user_data_dir,
)

auth_bp = Blueprint("auth", __name__)


def _users_path() -> Path:
    return current_app.config.get("USERS_PATH", Path("user_data/users.json"))


def _user_data_root() -> Path:
    return current_app.config.get("USER_DATA_ROOT", Path("user_data"))


@auth_bp.route("/join/<token>")
def join(token: str):
    """Invite link landing page."""
    email = validate_invite_token(
        token, secret_key=current_app.config["SECRET_KEY"]
    )
    if email is None:
        return "Invalid or expired invite link.", 403

    existing = load_user(email, path=_users_path())
    if existing is not None:
        return redirect(url_for("auth.login"))

    session["onboarding_email"] = email
    session["invite_token"] = token
    return render_template("onboarding.html", email=email)


@auth_bp.route("/onboarding", methods=["POST"])
def onboarding_submit():
    """Process onboarding form (name entry), redirect to Gmail OAuth."""
    email = session.get("onboarding_email")
    if not email:
        return redirect(url_for("auth.login"))

    name = request.form.get("name", "").strip()
    if not name:
        return render_template("onboarding.html", email=email, error="Name is required.")

    user = User(email=email, name=name, role="user", data_root=_user_data_root())
    save_user(user, path=_users_path())

    data_dir = user_data_dir(email, root=_user_data_root())
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "tokens").mkdir(exist_ok=True)

    login_user(user, remember=True)

    return redirect(url_for("auth.start_gmail_oauth", scope="readonly"))


@auth_bp.route("/auth/gmail")
@login_required
def start_gmail_oauth():
    """Initiate Gmail OAuth flow."""
    from google_auth_oauthlib.flow import Flow
    from config.settings import settings

    scope_label = request.args.get("scope", "readonly")
    scopes = {
        "readonly": ["https://www.googleapis.com/auth/gmail.readonly"],
        "send": ["https://www.googleapis.com/auth/gmail.send"],
    }[scope_label]

    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
        scopes=scopes,
        redirect_uri=url_for("auth.oauth_callback", _external=True),
    )
    auth_url, state = flow.authorization_url(
        access_type="offline",
        prompt="consent",
        login_hint=current_user.email,
    )
    session["oauth_state"] = state
    session["oauth_scope_label"] = scope_label
    return redirect(auth_url)


@auth_bp.route("/auth/callback")
def oauth_callback():
    """Handle Google OAuth callback."""
    from google_auth_oauthlib.flow import Flow
    from config.settings import settings
    from auth.gmail_oauth import _safe_email

    scope_label = session.pop("oauth_scope_label", "readonly")
    scopes = {
        "readonly": ["https://www.googleapis.com/auth/gmail.readonly"],
        "send": ["https://www.googleapis.com/auth/gmail.send"],
    }[scope_label]

    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
        scopes=scopes,
        redirect_uri=url_for("auth.oauth_callback", _external=True),
    )
    flow.fetch_token(authorization_response=request.url)
    creds = flow.credentials

    from googleapiclient.discovery import build
    service = build("gmail", "v1", credentials=creds)
    profile = service.users().getProfile(userId="me").execute()
    gmail_email = profile["emailAddress"]

    safe = _safe_email(gmail_email)
    tokens_dir = user_data_dir(
        current_user.email, root=_user_data_root()
    ) / "tokens"
    tokens_dir.mkdir(parents=True, exist_ok=True)
    token_file = tokens_dir / f"{safe}_{scope_label}.json"
    token_file.write_text(creds.to_json())

    if session.pop("onboarding_email", None):
        return redirect(url_for("index"))

    flash(f"Gmail account {gmail_email} connected ({scope_label}).")
    return redirect(url_for("index"))


@auth_bp.route("/login")
def login():
    if current_user.is_authenticated:
        return redirect(url_for("index"))
    return render_template("login.html")


@auth_bp.route("/login/google")
def login_google():
    session["login_flow"] = True
    return redirect(url_for("auth.start_gmail_oauth", scope="readonly"))


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))
