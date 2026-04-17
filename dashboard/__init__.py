"""Dashboard package — app factory for the GDPR SAR monitoring dashboard."""

from __future__ import annotations

import secrets
from pathlib import Path

from flask import Flask, g, request
from flask_login import LoginManager, current_user

from dashboard.shared import (
    _USER_DATA,
    inject_globals,
    flag_emoji_filter,
)
from dashboard.user_model import (
    load_user as _load_user_by_email,
)
from dashboard.auth_routes import auth_bp
from dashboard.admin_routes import admin_bp


def create_app() -> Flask:
    """Create and configure the Flask application.

    Sets up secret key, Flask-Login, auth blueprints, before_request hook,
    context processor, and template filter. Routes are registered by the
    caller (dashboard/app.py) after this returns.
    """
    app = Flask(__name__, template_folder="templates")

    # --- Secret key (persistent across restarts) ---
    secret_key_path = _USER_DATA / "secret_key.txt"
    _USER_DATA.mkdir(parents=True, exist_ok=True)
    if secret_key_path.exists():
        app.config["SECRET_KEY"] = secret_key_path.read_text().strip()
    else:
        key = secrets.token_hex(32)
        secret_key_path.write_text(key)
        app.config["SECRET_KEY"] = key

    app.config["USERS_PATH"] = _USER_DATA / "users.json"
    app.config["USER_DATA_ROOT"] = _USER_DATA

    # --- Flask-Login ---
    login_manager = LoginManager(app)
    login_manager.login_view = "auth.login"
    login_manager.login_message = None  # suppress flash on redirect to login

    @login_manager.user_loader
    def _flask_load_user(email: str):
        users_path = app.config.get("USERS_PATH", _USER_DATA / "users.json")
        data_root = app.config.get("USER_DATA_ROOT", _USER_DATA)
        return _load_user_by_email(email, path=users_path, data_root=data_root)

    # --- Auth blueprints ---
    app.register_blueprint(auth_bp)
    app.register_blueprint(admin_bp)

    # --- Leaf blueprints (Phase 1) ---
    from dashboard.blueprints.costs_bp import costs_bp
    from dashboard.blueprints.settings_bp import settings_bp
    from dashboard.blueprints.api_bp import api_bp

    app.register_blueprint(costs_bp)
    app.register_blueprint(settings_bp)
    app.register_blueprint(api_bp)

    # --- Phase 2 blueprints ---
    from dashboard.blueprints.data_bp import data_bp
    from dashboard.blueprints.monitor_bp import monitor_bp

    app.register_blueprint(data_bp)
    app.register_blueprint(monitor_bp)

    # --- Phase 3 blueprints ---
    from dashboard.blueprints.portal_bp import portal_bp
    from dashboard.blueprints.transfers_bp import transfers_bp
    from dashboard.blueprints.company_bp import company_bp
    from dashboard.blueprints.dashboard_bp import dashboard_bp

    app.register_blueprint(portal_bp)
    app.register_blueprint(transfers_bp)
    app.register_blueprint(company_bp)
    app.register_blueprint(dashboard_bp)

    # --- Phase 4 blueprint (pipeline — final extraction) ---
    from dashboard.blueprints.pipeline_bp import pipeline_bp

    app.register_blueprint(pipeline_bp)

    # --- Before-request hook ---
    @app.before_request
    def _inject_user():
        """Set g.data_dir for authenticated users. Redirect to login for protected routes."""
        if request.endpoint and request.endpoint.startswith(("auth.", "static")):
            return
        if not current_user.is_authenticated:
            return login_manager.unauthorized()
        g.user = current_user
        g.data_dir = current_user.data_dir

    # --- Context processor & template filter ---
    app.context_processor(inject_globals)
    app.template_filter("flag_emoji")(flag_emoji_filter)

    return app
