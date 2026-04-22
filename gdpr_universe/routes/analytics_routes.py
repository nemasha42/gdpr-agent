"""Analytics blueprint — precomputed metrics and leaderboards."""

from __future__ import annotations

from flask import Blueprint, current_app, jsonify, render_template
from sqlalchemy.engine import Engine

from gdpr_universe.analytics import get_cached, refresh_analytics

bp = Blueprint("analytics", __name__)


def _get_engine() -> Engine:
    return current_app.config["DB_ENGINE"]


@bp.route("/analytics")
def analytics():
    """Render the analytics dashboard with cached data."""
    engine = _get_engine()

    data = {
        "sharing_counts": get_cached(engine, "sharing_counts"),
        "blast_radius_top50": get_cached(engine, "blast_radius_top50"),
        "concentration": get_cached(engine, "concentration"),
        "chain_depth": get_cached(engine, "chain_depth"),
        "sp_countries": get_cached(engine, "sp_countries"),
        "category_edges": get_cached(engine, "category_edges"),
        # Data quality panels
        "edge_completeness": get_cached(engine, "edge_completeness"),
        "source_breakdown": get_cached(engine, "source_breakdown"),
        "fetch_staleness": get_cached(engine, "fetch_staleness"),
        "fetch_success": get_cached(engine, "fetch_success"),
        "field_coverage": get_cached(engine, "field_coverage"),
        "country_coverage": get_cached(engine, "country_coverage"),
        "quality_breakdown": get_cached(engine, "quality_breakdown"),
    }

    has_data = any(v is not None for v in data.values())

    return render_template(
        "analytics.html",
        active_tab="analytics",
        has_data=has_data,
        **data,
    )


@bp.route("/analytics/refresh", methods=["POST"])
def analytics_refresh():
    """Recompute all analytics and return JSON result."""
    engine = _get_engine()
    result = refresh_analytics(engine)
    return jsonify(result)
