"""GDPR Universe — Flask application factory."""

from __future__ import annotations

import os

from flask import Flask

from gdpr_universe.db import get_engine, init_db


def create_app(db_path: str | None = None) -> Flask:
    """Create and configure the GDPR Universe Flask application."""
    app = Flask(
        __name__,
        template_folder=os.path.join(os.path.dirname(__file__), "templates"),
        static_folder=os.path.join(os.path.dirname(__file__), "static"),
    )

    app.secret_key = os.environ.get("FLASK_SECRET_KEY", "gdpr-universe-dev")

    # Database setup
    if db_path is None:
        db_path = os.path.join(
            os.path.dirname(os.path.dirname(__file__)), "gdpr_universe", "data", "universe.db"
        )
    engine = get_engine(db_path)
    init_db(engine)
    app.config["DB_ENGINE"] = engine

    # Register blueprints
    from gdpr_universe.routes.dashboard import bp as dashboard_bp

    app.register_blueprint(dashboard_bp)

    from gdpr_universe.routes.company import bp as company_bp

    app.register_blueprint(company_bp)

    from gdpr_universe.routes.graph import bp as graph_bp

    app.register_blueprint(graph_bp)

    from gdpr_universe.routes.contagion import bp as contagion_bp

    app.register_blueprint(contagion_bp)

    from gdpr_universe.routes.analytics_routes import bp as analytics_bp

    app.register_blueprint(analytics_bp)

    from gdpr_universe.routes.crawl import bp as crawl_bp

    app.register_blueprint(crawl_bp)

    from gdpr_universe.routes.compare import bp as compare_bp

    app.register_blueprint(compare_bp)

    return app


if __name__ == "__main__":
    app = create_app()
    app.run(host="127.0.0.1", port=5003, debug=True, threaded=True)
