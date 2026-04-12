import pytest
from flask import Flask
from flask_login import LoginManager

from dashboard.user_model import User, save_user, load_user


@pytest.fixture
def app(tmp_path):
    app = Flask(__name__, template_folder=str(
        __import__("pathlib").Path(__file__).parent.parent.parent / "dashboard" / "templates"
    ))
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
        return load_user(email, path=app.config["USERS_PATH"],
                        data_root=app.config["USER_DATA_ROOT"])

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
    admin = User(email="admin@gmail.com", name="Admin", role="admin", data_root=tmp_path)
    save_user(admin, path=tmp_path / "users.json")
    (tmp_path / "admin_at_gmail_com").mkdir(exist_ok=True)

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["_user_id"] = "admin@gmail.com"
        resp = client.get("/logout")
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]
