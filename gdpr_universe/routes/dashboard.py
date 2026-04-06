"""Dashboard blueprint — main overview page."""

from __future__ import annotations

from flask import Blueprint, current_app, render_template, request
from sqlalchemy import text
from sqlalchemy.engine import Engine

from gdpr_universe.db import get_session
from gdpr_universe.graph_builder import build_full_graph
from gdpr_universe.graph_queries import sharing_counts

bp = Blueprint("dashboard", __name__)


def _get_engine() -> Engine:
    return current_app.config["DB_ENGINE"]


def _get_stats(engine: Engine) -> dict:
    """Compute aggregate stats for the navbar and stats bar."""
    with get_session(engine) as session:
        total_companies = session.execute(
            text("SELECT COUNT(*) FROM companies WHERE is_seed = 1")
        ).scalar() or 0

        total_sps = session.execute(
            text("SELECT COUNT(*) FROM companies WHERE is_seed = 0")
        ).scalar() or 0

        total_edges = session.execute(
            text("SELECT COUNT(*) FROM edges")
        ).scalar() or 0

        countries_reached = session.execute(
            text(
                "SELECT COUNT(DISTINCT hq_country_code) FROM companies "
                "WHERE hq_country_code IS NOT NULL AND hq_country_code != ''"
            )
        ).scalar() or 0

        # Coverage: seeds with at least one fetch_status='ok' / total seeds
        seeds_fetched = session.execute(
            text(
                "SELECT COUNT(DISTINCT fl.domain) FROM fetch_log fl "
                "JOIN companies c ON fl.domain = c.domain "
                "WHERE c.is_seed = 1 AND fl.fetch_status = 'ok'"
            )
        ).scalar() or 0

    coverage_pct = round(seeds_fetched / total_companies * 100, 1) if total_companies else 0.0

    return {
        "total_companies": total_companies,
        "total_sps": total_sps,
        "total_edges": total_edges,
        "countries_reached": countries_reached,
        "coverage_pct": coverage_pct,
    }


def _get_company_rows(
    engine: Engine, *, search: str = "", sort: str = "company_name", order: str = "asc"
) -> list[dict]:
    """Return seed companies with SP count and latest fetch status."""
    allowed_sorts = {
        "company_name": "c.company_name",
        "hq_country_code": "c.hq_country_code",
        "sector": "c.sector",
        "sp_count": "sp_count",
        "fetch_status": "fetch_status",
    }
    sort_col = allowed_sorts.get(sort, "c.company_name")
    order_dir = "DESC" if order == "desc" else "ASC"

    sql = text(
        f"SELECT c.domain, c.company_name, c.hq_country_code, c.sector, "
        f"       COALESCE(sp.cnt, 0) AS sp_count, "
        f"       fl.fetch_status "
        f"FROM companies c "
        f"LEFT JOIN ("
        f"    SELECT parent_domain, COUNT(DISTINCT child_domain) AS cnt "
        f"    FROM edges GROUP BY parent_domain"
        f") sp ON sp.parent_domain = c.domain "
        f"LEFT JOIN ("
        f"    SELECT domain, fetch_status FROM fetch_log "
        f"    WHERE id IN (SELECT MAX(id) FROM fetch_log GROUP BY domain)"
        f") fl ON fl.domain = c.domain "
        f"WHERE c.is_seed = 1 "
        f"{'AND (c.company_name LIKE :search OR c.domain LIKE :search) ' if search else ''}"
        f"ORDER BY {sort_col} {order_dir}"
    )

    params = {}
    if search:
        params["search"] = f"%{search}%"

    with get_session(engine) as session:
        rows = session.execute(sql, params).fetchall()

    return [
        {
            "domain": row[0],
            "company_name": row[1] or row[0],
            "hq_country_code": row[2] or "",
            "sector": row[3] or "",
            "sp_count": row[4],
            "fetch_status": row[5] or "pending",
        }
        for row in rows
    ]


@bp.route("/")
def index():
    """Main dashboard page."""
    engine = _get_engine()
    stats = _get_stats(engine)

    search = request.args.get("search", "").strip()
    sort = request.args.get("sort", "company_name")
    order = request.args.get("order", "asc")

    companies = _get_company_rows(engine, search=search, sort=sort, order=order)
    graph_data = build_full_graph(engine)

    # Top 20 subprocessors by sharing count
    sc = sharing_counts(engine, limit=20)
    # Resolve names
    top_sps: list[dict] = []
    if sc:
        with get_session(engine) as session:
            for domain, count in sc.items():
                row = session.execute(
                    text("SELECT company_name FROM companies WHERE domain = :d"),
                    {"d": domain},
                ).fetchone()
                top_sps.append({
                    "domain": domain,
                    "company_name": row[0] if row and row[0] else domain,
                    "count": count,
                })

    return render_template(
        "dashboard.html",
        active_tab="dashboard",
        stats=stats,
        companies=companies,
        top_sps=top_sps,
        search=search,
        sort=sort,
        order=order,
        graph_json=graph_data,
    )
