"""Authentication routes: invite, login, logout, OAuth callback."""

from __future__ import annotations

from pathlib import Path

from flask import (
    Blueprint,
    current_app,
    flash,
    redirect,
    render_template,
    request,
    session,
    url_for,
)
from flask_login import current_user, login_required, login_user, logout_user

from dashboard.user_model import (
    User,
    load_user,
    save_user,
    validate_invite_token,
    user_data_dir,
)

auth_bp = Blueprint("auth", __name__)

# Path to the canonical OAuth credentials file (same one used by CLI).
_CREDENTIALS_JSON = Path(__file__).resolve().parent.parent / "credentials.json"


def _users_path() -> Path:
    return current_app.config.get("USERS_PATH", Path("user_data/users.json"))


def _user_data_root() -> Path:
    return current_app.config.get("USER_DATA_ROOT", Path("user_data"))


@auth_bp.route("/join/<token>")
def join(token: str):
    """Invite link landing page."""
    email = validate_invite_token(token, secret_key=current_app.config["SECRET_KEY"])
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
        return render_template(
            "onboarding.html", email=email, error="Name is required."
        )

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

    scope_label = request.args.get("scope", "readonly")
    scopes = {
        "readonly": ["https://www.googleapis.com/auth/gmail.readonly"],
        "send": ["https://www.googleapis.com/auth/gmail.send"],
    }[scope_label]

    flow = Flow.from_client_secrets_file(
        str(_CREDENTIALS_JSON),
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
    session["oauth_code_verifier"] = flow.code_verifier
    return redirect(auth_url)


@auth_bp.route("/auth/callback")
def oauth_callback():
    """Handle Google OAuth callback."""
    from google_auth_oauthlib.flow import Flow
    from auth.gmail_oauth import _safe_email

    scope_label = session.pop("oauth_scope_label", "readonly")
    scopes = {
        "readonly": ["https://www.googleapis.com/auth/gmail.readonly"],
        "send": ["https://www.googleapis.com/auth/gmail.send"],
    }[scope_label]

    flow = Flow.from_client_secrets_file(
        str(_CREDENTIALS_JSON),
        scopes=scopes,
        redirect_uri=url_for("auth.oauth_callback", _external=True),
    )
    flow.code_verifier = session.pop("oauth_code_verifier", None)
    flow.fetch_token(authorization_response=request.url)
    creds = flow.credentials

    from googleapiclient.discovery import build

    service = build("gmail", "v1", credentials=creds)
    profile = service.users().getProfile(userId="me").execute()
    gmail_email = profile["emailAddress"]

    # Login flow: find existing user by Gmail email and log them in
    if session.pop("login_flow", False):
        user = load_user(gmail_email, path=_users_path())
        if user is None:
            flash(
                "No account found for this Google account. Ask your admin for an invite link.",
                "danger",
            )
            return redirect(url_for("auth.login"))
        login_user(user, remember=True)
        # Save/refresh the token
        safe = _safe_email(gmail_email)
        tokens_dir = user_data_dir(user.email, root=_user_data_root()) / "tokens"
        tokens_dir.mkdir(parents=True, exist_ok=True)
        token_file = tokens_dir / f"{safe}_{scope_label}.json"
        token_file.write_text(creds.to_json())
        return redirect(url_for("dashboard"))

    # Normal flow: user is already logged in, connecting a Gmail account
    safe = _safe_email(gmail_email)
    tokens_dir = user_data_dir(current_user.email, root=_user_data_root()) / "tokens"
    tokens_dir.mkdir(parents=True, exist_ok=True)
    token_file = tokens_dir / f"{safe}_{scope_label}.json"
    token_file.write_text(creds.to_json())

    if session.pop("onboarding_email", None):
        return redirect(url_for("dashboard"))

    flash(f"Gmail account {gmail_email} connected ({scope_label}).")
    return redirect(url_for("dashboard"))


@auth_bp.route("/login")
def login():
    if current_user.is_authenticated:
        return redirect(url_for("dashboard"))
    return render_template("login.html")


@auth_bp.route("/login/google")
def login_google():
    """Start OAuth flow for login — no @login_required since user isn't logged in yet."""
    from google_auth_oauthlib.flow import Flow

    session["login_flow"] = True
    scopes = ["https://www.googleapis.com/auth/gmail.readonly"]

    flow = Flow.from_client_secrets_file(
        str(_CREDENTIALS_JSON),
        scopes=scopes,
        redirect_uri=url_for("auth.oauth_callback", _external=True),
    )
    auth_url, state = flow.authorization_url(
        access_type="offline",
        prompt="consent",
    )
    session["oauth_state"] = state
    session["oauth_scope_label"] = "readonly"
    session["oauth_code_verifier"] = flow.code_verifier
    return redirect(auth_url)


@auth_bp.route("/logout")
@login_required
def logout():
    logout_user()
    return redirect(url_for("auth.login"))
