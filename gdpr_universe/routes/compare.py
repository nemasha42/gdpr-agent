"""Compare blueprint — company benchmarking and side-by-side analysis."""

from __future__ import annotations

import logging

from flask import Blueprint, current_app, jsonify, render_template, request
from sqlalchemy import text
from sqlalchemy.engine import Engine

from gdpr_universe.compare import (
    compute_company_metrics,
    compute_side_by_side,
    get_compare_data,
    refresh_compare,
)
from gdpr_universe.db import get_session
from gdpr_universe.routes.dashboard import _get_filter_options, _get_stats

logger = logging.getLogger(__name__)

bp = Blueprint("compare", __name__)


def _get_engine() -> Engine:
    return current_app.config["DB_ENGINE"]


def _get_categories(engine: Engine) -> list[str]:
    """Return sorted list of distinct service_category values from companies table."""
    with get_session(engine) as session:
        rows = session.execute(
            text(
                "SELECT DISTINCT service_category FROM companies "
                "WHERE service_category IS NOT NULL AND service_category != '' "
                "ORDER BY service_category"
            )
        ).fetchall()
    return [r[0] for r in rows]


@bp.route("/compare")
def compare() -> str:
    """Render the compare/benchmarking page."""
    engine = _get_engine()

    stats = _get_stats(engine)
    available_countries, available_sectors = _get_filter_options(engine)
    available_categories = _get_categories(engine)

    filter_country = request.args.get("country", "").strip()
    filter_sector = request.args.get("sector", "").strip()
    filter_category = request.args.get("category", "").strip()

    # Load cached comparison data
    compare_data = get_compare_data(engine)

    # If category filter is active, recompute metrics for that category
    if filter_category:
        matrix = compute_company_metrics(engine, category=filter_category)
    else:
        matrix = compare_data.get("matrix")

    # Apply country/sector filters to matrix rows
    if matrix is not None and (filter_country or filter_sector):
        matrix = [
            row for row in matrix
            if (not filter_country or row.get("hq_country_code") == filter_country)
            and (not filter_sector or row.get("sector") == filter_sector)
        ]

    has_data = compare_data.get("matrix") is not None

    return render_template(
        "compare.html",
        active_tab="compare",
        stats=stats,
        has_data=has_data,
        matrix=matrix,
        shared_sps=compare_data.get("shared_sps"),
        alternatives=compare_data.get("alternatives"),
        sector_averages=compare_data.get("sector_averages"),
        compare_data=compare_data,
        filter_country=filter_country,
        filter_sector=filter_sector,
        filter_category=filter_category,
        available_countries=available_countries,
        available_sectors=available_sectors,
        available_categories=available_categories,
    )


@bp.route("/compare/refresh", methods=["POST"])
def compare_refresh():
    """Recompute all comparison metrics and return JSON result."""
    engine = _get_engine()
    result = refresh_compare(engine)
    return jsonify(result)


@bp.route("/api/compare/side-by-side")
def side_by_side():
    """Return side-by-side comparison JSON for 2-5 selected domains.

    Query param: domains — comma-separated list of seed domains.
    Returns 400 if fewer than 2 or more than 5 domains are provided.
    """
    engine = _get_engine()

    domains_param = request.args.get("domains", "").strip()
    domains = [d.strip() for d in domains_param.split(",") if d.strip()]

    if len(domains) < 2:
        return jsonify({"error": "At least 2 domains are required"}), 400
    if len(domains) > 5:
        return jsonify({"error": "At most 5 domains are allowed"}), 400

    try:
        result = compute_side_by_side(engine, domains)
    except ValueError as exc:
        return jsonify({"error": str(exc)}), 400

    return jsonify(result)
