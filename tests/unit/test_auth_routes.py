import pytest
from flask import Flask
from flask_login import LoginManager

from dashboard.user_model import User, save_user, load_user


@pytest.fixture
def app(tmp_path):
    app = Flask(
        __name__,
        template_folder=str(
            __import__("pathlib").Path(__file__).parent.parent.parent
            / "dashboard"
            / "templates"
        ),
    )
    app.config["SECRET_KEY"] = "test-secret"
    app.config["TESTING"] = True
    app.config["USERS_PATH"] = tmp_path / "users.json"
    app.config["USER_DATA_ROOT"] = tmp_path

    from dashboard.auth_routes import auth_bp

    login_manager = LoginManager(app)
    login_manager.login_view = "auth.login"
    app.register_blueprint(auth_bp)

    @login_manager.user_loader
    def load(email):
        return load_user(
            email, path=app.config["USERS_PATH"], data_root=app.config["USER_DATA_ROOT"]
        )

    # Dummy dashboard route so url_for("main.dashboard") resolves in tests
    from flask import Blueprint
    _main_bp = Blueprint("main", __name__)

    @_main_bp.route("/")
    def dashboard():
        return "dashboard"

    app.register_blueprint(_main_bp)

    return app


def test_join_valid_token(app):
    from dashboard.user_model import generate_invite_token

    token = generate_invite_token("friend@gmail.com", secret_key="test-secret")
    with app.test_client() as client:
        resp = client.get(f"/join/{token}")
        assert resp.status_code == 200


def test_join_invalid_token(app):
    with app.test_client() as client:
        resp = client.get("/join/bad-token")
        assert resp.status_code == 403


def test_join_existing_user_redirects(app, tmp_path):
    from dashboard.user_model import generate_invite_token

    save_user(
        User(email="friend@gmail.com", name="Friend", role="user", data_root=tmp_path),
        path=tmp_path / "users.json",
    )
    token = generate_invite_token("friend@gmail.com", secret_key="test-secret")
    with app.test_client() as client:
        resp = client.get(f"/join/{token}")
        assert resp.status_code == 302


def test_login_page_renders(app):
    with app.test_client() as client:
        resp = client.get("/login")
        assert resp.status_code == 200
        assert b"Sign in with Google" in resp.data


def test_logout_redirects_to_login(app, tmp_path):
    admin = User(
        email="admin@gmail.com", name="Admin", role="admin", data_root=tmp_path
    )
    save_user(admin, path=tmp_path / "users.json")
    (tmp_path / "admin_at_gmail_com").mkdir(exist_ok=True)

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["_user_id"] = "admin@gmail.com"
        resp = client.get("/logout")
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]


def test_login_google_redirects_to_oauth(app, tmp_path):
    """Test that /login/google starts the OAuth flow and redirects to Google."""
    from unittest.mock import patch, MagicMock

    mock_flow = MagicMock()
    mock_flow.authorization_url.return_value = (
        "https://accounts.google.com/o/oauth2/auth?state=xyz",
        "xyz",
    )
    mock_flow.code_verifier = "test-verifier-123"

    with patch(
        "google_auth_oauthlib.flow.Flow.from_client_secrets_file",
        return_value=mock_flow,
    ):
        with app.test_client() as client:
            resp = client.get("/login/google")
            assert resp.status_code == 302
            assert "accounts.google.com" in resp.headers["Location"]

            # Verify session state was set
            with client.session_transaction() as sess:
                assert sess["login_flow"] is True
                assert sess["oauth_scope_label"] == "readonly"
                assert sess["oauth_code_verifier"] == "test-verifier-123"


def test_oauth_callback_login_flow(app, tmp_path):
    """Test the full login callback: existing user logs in and redirects to dashboard."""
    from unittest.mock import patch, MagicMock

    # Pre-create the user
    user = User(
        email="traderm1620@gmail.com", name="Maria", role="user", data_root=tmp_path
    )
    save_user(user, path=tmp_path / "users.json")
    user_dir = tmp_path / "traderm1620_at_gmail_com" / "tokens"
    user_dir.mkdir(parents=True, exist_ok=True)

    mock_creds = MagicMock()
    mock_creds.to_json.return_value = '{"token": "test"}'

    mock_flow = MagicMock()
    mock_flow.credentials = mock_creds

    mock_service = MagicMock()
    mock_service.users.return_value.getProfile.return_value.execute.return_value = {
        "emailAddress": "traderm1620@gmail.com"
    }

    with patch(
        "google_auth_oauthlib.flow.Flow.from_client_secrets_file",
        return_value=mock_flow,
    ), patch("googleapiclient.discovery.build", return_value=mock_service), patch(
        "auth.gmail_oauth._safe_email", return_value="traderm1620_at_gmail_com"
    ):
        with app.test_client() as client:
            # Set up session as if /login/google was just called
            with client.session_transaction() as sess:
                sess["login_flow"] = True
                sess["oauth_scope_label"] = "readonly"
                sess["oauth_code_verifier"] = "test-verifier-123"

            resp = client.get("/auth/callback?code=test-auth-code&state=xyz")

            # Should redirect to dashboard (not "index")
            assert resp.status_code == 302
            loc = resp.headers["Location"]
            # Should NOT contain "index" — that was the old bug
            assert "index" not in loc
            # Should redirect to the dashboard root
            assert loc.endswith("/")  # url_for("dashboard") = "/"

            # code_verifier should have been restored on the flow
            assert mock_flow.code_verifier == "test-verifier-123"

            # Token should have been fetched
            mock_flow.fetch_token.assert_called_once()


def test_oauth_callback_no_account_flashes_error(app, tmp_path):
    """Test that login callback for non-existent user redirects to login with flash."""
    from unittest.mock import patch, MagicMock

    mock_creds = MagicMock()
    mock_creds.to_json.return_value = '{"token": "test"}'

    mock_flow = MagicMock()
    mock_flow.credentials = mock_creds

    mock_service = MagicMock()
    mock_service.users.return_value.getProfile.return_value.execute.return_value = {
        "emailAddress": "nobody@gmail.com"
    }

    with patch(
        "google_auth_oauthlib.flow.Flow.from_client_secrets_file",
        return_value=mock_flow,
    ), patch("googleapiclient.discovery.build", return_value=mock_service), patch(
        "auth.gmail_oauth._safe_email", return_value="nobody_at_gmail_com"
    ):
        with app.test_client() as client:
            with client.session_transaction() as sess:
                sess["login_flow"] = True
                sess["oauth_scope_label"] = "readonly"
                sess["oauth_code_verifier"] = "verifier"

            resp = client.get("/auth/callback?code=test&state=xyz")
            assert resp.status_code == 302
            assert "/login" in resp.headers["Location"]


def test_login_redirects_authenticated_user_to_dashboard(app, tmp_path):
    """Test that /login redirects already-authenticated users to dashboard."""
    user = User(email="admin@gmail.com", name="Admin", role="admin", data_root=tmp_path)
    save_user(user, path=tmp_path / "users.json")
    (tmp_path / "admin_at_gmail_com").mkdir(exist_ok=True)

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["_user_id"] = "admin@gmail.com"
        resp = client.get("/login")
        assert resp.status_code == 302
        assert resp.headers["Location"].endswith("/")
