"""Dashboard blueprint — main overview page."""

from __future__ import annotations

from flask import Blueprint, current_app, render_template, request
from sqlalchemy import text
from sqlalchemy.engine import Engine

from gdpr_universe.db import get_session
from gdpr_universe.graph_builder import build_full_graph
from gdpr_universe.graph_queries import sharing_counts

bp = Blueprint("dashboard", __name__)

# ── Subprocessor page URL patterns that indicate an official source ──
_OFFICIAL_SP_PATTERNS = (
    "/sub-processor", "/subprocessor", "/sub_processor",
    "/third-party", "/third-parties", "/vendors",
    "/trust-center", "/trust_center",
    "/data-processing", "/data_processing",
)
_PRIVACY_PATTERNS = (
    "/privacy", "/gdpr", "/legal", "/cookie",
    "/data-protection", "/compliance",
)


def _derive_quality(source_url: str | None, fetch_status: str | None) -> str:
    """Derive a quality label from the source URL and fetch status.

    Returns: 'high', 'medium', 'low', or 'unknown'.
    """
    if not source_url:
        if fetch_status == "ok":
            return "low"
        return "unknown"

    url_lower = source_url.lower()
    if any(p in url_lower for p in _OFFICIAL_SP_PATTERNS):
        return "high"
    if any(p in url_lower for p in _PRIVACY_PATTERNS):
        return "medium"
    return "low"


def _quality_reason(source_url: str | None, fetch_status: str | None) -> str:
    """Return a human-readable reason for the quality rating."""
    if not source_url:
        if fetch_status == "ok":
            return "Data exists but source URL was not recorded (manual entry or early crawl)"
        if fetch_status == "not_found":
            return "No subprocessor page found for this domain"
        if fetch_status == "error":
            return "Fetch attempt failed with an error"
        return "Not yet fetched"

    url_lower = source_url.lower()
    if any(p in url_lower for p in _OFFICIAL_SP_PATTERNS):
        return "Official subprocessor/vendor list page"
    if any(p in url_lower for p in _PRIVACY_PATTERNS):
        return "Privacy or legal page (may have partial SP info)"
    return "Generic webpage — subprocessor list not confirmed"


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

        # Coverage: seeds that have at least one outgoing edge (SP discovered)
        seeds_with_sps = session.execute(
            text(
                "SELECT COUNT(DISTINCT e.parent_domain) FROM edges e "
                "JOIN companies c ON e.parent_domain = c.domain "
                "WHERE c.is_seed = 1"
            )
        ).scalar() or 0

        # Max depth in the graph
        max_depth = session.execute(
            text("SELECT MAX(depth) FROM edges")
        ).scalar() or 0

    coverage_pct = round(seeds_with_sps / total_companies * 100, 1) if total_companies else 0.0

    return {
        "total_companies": total_companies,
        "total_sps": total_sps,
        "total_edges": total_edges,
        "countries_reached": countries_reached,
        "coverage_pct": coverage_pct,
        "max_depth": max_depth,
    }


def _get_company_rows(
    engine: Engine,
    *,
    search: str = "",
    sort: str = "company_name",
    order: str = "asc",
    country: str = "",
    sector: str = "",
) -> list[dict]:
    """Return seed companies with SP count and latest fetch status."""
    allowed_sorts = {
        "company_name": "c.company_name",
        "hq_country_code": "c.hq_country_code",
        "sector": "c.sector",
        "sp_count": "sp_count",
        "fetch_status": "fetch_status",
        "data_quality": "fl.source_url",
    }
    sort_col = allowed_sorts.get(sort, "c.company_name")
    order_dir = "DESC" if order == "desc" else "ASC"

    where_clauses = ["c.is_seed = 1"]
    params: dict = {}
    if search:
        where_clauses.append("(c.company_name LIKE :search OR c.domain LIKE :search)")
        params["search"] = f"%{search}%"
    if country:
        where_clauses.append("c.hq_country_code = :country")
        params["country"] = country
    if sector:
        where_clauses.append("c.sector = :sector")
        params["sector"] = sector

    where = " AND ".join(where_clauses)

    sql = text(
        f"SELECT c.domain, c.company_name, c.hq_country_code, c.sector, "
        f"       COALESCE(sp.cnt, 0) AS sp_count, "
        f"       fl.fetch_status, "
        f"       fl.source_url "
        f"FROM companies c "
        f"LEFT JOIN ("
        f"    SELECT parent_domain, COUNT(DISTINCT child_domain) AS cnt "
        f"    FROM edges GROUP BY parent_domain"
        f") sp ON sp.parent_domain = c.domain "
        f"LEFT JOIN ("
        f"    SELECT domain, fetch_status, source_url FROM fetch_log "
        f"    WHERE id IN (SELECT MAX(id) FROM fetch_log GROUP BY domain)"
        f") fl ON fl.domain = c.domain "
        f"WHERE {where} "
        f"ORDER BY {sort_col} {order_dir}"
    )

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
            "source_url": row[6] or "",
            "data_quality": _derive_quality(row[6], row[5]),
            "quality_reason": _quality_reason(row[6], row[5]),
        }
        for row in rows
    ]


def _get_filter_options(engine: Engine) -> tuple[list[str], list[str]]:
    """Return sorted lists of distinct countries and sectors for filter dropdowns."""
    with get_session(engine) as session:
        countries = [
            r[0]
            for r in session.execute(
                text(
                    "SELECT DISTINCT hq_country_code FROM companies "
                    "WHERE is_seed = 1 AND hq_country_code IS NOT NULL AND hq_country_code != '' "
                    "ORDER BY hq_country_code"
                )
            ).fetchall()
        ]
        sectors = [
            r[0]
            for r in session.execute(
                text(
                    "SELECT DISTINCT sector FROM companies "
                    "WHERE is_seed = 1 AND sector IS NOT NULL AND sector != '' "
                    "ORDER BY sector"
                )
            ).fetchall()
        ]
    return countries, sectors


@bp.route("/")
def index():
    """Main dashboard page."""
    engine = _get_engine()
    stats = _get_stats(engine)

    search = request.args.get("search", "").strip()
    sort = request.args.get("sort", "company_name")
    order = request.args.get("order", "asc")
    filter_country = request.args.get("country", "").strip()
    filter_sector = request.args.get("sector", "").strip()

    companies = _get_company_rows(
        engine, search=search, sort=sort, order=order,
        country=filter_country, sector=filter_sector,
    )
    available_countries, available_sectors = _get_filter_options(engine)
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
        filter_country=filter_country,
        filter_sector=filter_sector,
        available_countries=available_countries,
        available_sectors=available_sectors,
        graph_json=graph_data,
    )
