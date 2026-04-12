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

    from dashboard.admin_routes import admin_bp
    from dashboard.auth_routes import auth_bp

    login_manager = LoginManager(app)
    login_manager.login_view = "auth.login"
    app.register_blueprint(admin_bp)
    app.register_blueprint(auth_bp)

    @login_manager.user_loader
    def load(email):
        return load_user(email, path=app.config["USERS_PATH"],
                        data_root=app.config["USER_DATA_ROOT"])

    # Create admin user
    admin = User(email="admin@gmail.com", name="Admin", role="admin", data_root=tmp_path)
    save_user(admin, path=tmp_path / "users.json")
    (tmp_path / "admin_at_gmail_com").mkdir()

    return app


def test_admin_invite_page_loads(app):
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["_user_id"] = "admin@gmail.com"
        resp = client.get("/admin/invite")
        assert resp.status_code == 200


def test_admin_invite_generates_link(app):
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["_user_id"] = "admin@gmail.com"
        resp = client.post("/admin/invite", data={"email": "friend@gmail.com"})
        assert resp.status_code == 200
        assert b"join/" in resp.data


def test_non_admin_gets_403(app, tmp_path):
    regular = User(email="user@gmail.com", name="User", role="user", data_root=tmp_path)
    save_user(regular, path=tmp_path / "users.json")

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["_user_id"] = "user@gmail.com"
        resp = client.get("/admin/invite")
        assert resp.status_code == 403


def test_admin_users_list(app, tmp_path):
    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["_user_id"] = "admin@gmail.com"
        resp = client.get("/admin/users")
        assert resp.status_code == 200
        assert b"admin@gmail.com" in resp.data


def test_unauthenticated_redirects_to_login(app):
    with app.test_client() as client:
        resp = client.get("/admin/invite")
        assert resp.status_code == 302
        assert "/login" in resp.headers["Location"]
