"""Analytics computation and caching layer."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.engine import Engine

from gdpr_universe.db import AnalyticsCache, get_session
from gdpr_universe.graph_queries import (
    blast_radius,
    chain_depth_distribution,
    concentration_risk,
    sharing_counts,
)

_CONCENTRATION_CATEGORIES = [
    "payments",
    "infrastructure",
    "analytics",
    "communication",
    "crm",
    "security",
    "advertising",
    "storage",
    "ai_ml",
]


def refresh_analytics(engine: Engine) -> dict:
    """Recompute all analytics and store in AnalyticsCache table.

    Returns {"keys_updated": [list of keys]}.
    """
    now = datetime.now(timezone.utc)
    results: dict[str, object] = {}

    # 1. sharing_counts
    results["sharing_counts"] = sharing_counts(engine)

    # 2. blast_radius_top50 — top 50 SPs by sharing count
    top50_domains = list(results["sharing_counts"].keys())[:50]
    blast: dict[str, int] = {}
    for domain in top50_domains:
        affected = blast_radius(engine, domain)
        blast[domain] = len(affected)
    results["blast_radius_top50"] = blast

    # 3. concentration — per category
    concentration: dict[str, list[dict]] = {}
    for cat in _CONCENTRATION_CATEGORIES:
        concentration[cat] = concentration_risk(engine, cat, limit=5)
    results["concentration"] = concentration

    # 4. chain_depth
    results["chain_depth"] = chain_depth_distribution(engine)

    # 5. sp_countries
    sql_countries = text(
        "SELECT hq_country_code, COUNT(*) AS cnt "
        "FROM companies "
        "WHERE hq_country_code != '' AND is_seed = 0 "
        "GROUP BY hq_country_code "
        "ORDER BY cnt DESC"
    )
    with get_session(engine) as session:
        rows = session.execute(sql_countries).fetchall()
    results["sp_countries"] = {row[0]: row[1] for row in rows}

    # 6. category_edges
    sql_edges = text(
        "SELECT c.service_category, COUNT(*) AS edge_count "
        "FROM edges e "
        "JOIN companies c ON e.child_domain = c.domain "
        "WHERE c.service_category != '' "
        "GROUP BY c.service_category "
        "ORDER BY edge_count DESC"
    )
    with get_session(engine) as session:
        rows = session.execute(sql_edges).fetchall()
    results["category_edges"] = {row[0]: row[1] for row in rows}

    # ── Data Quality metrics ──────────────────────────────────────

    # 7. edge_completeness — how many of 4 fields populated per edge
    sql_completeness = text(
        "SELECT "
        "  (CASE WHEN purposes IS NOT NULL AND purposes != '' AND purposes != '[]' THEN 1 ELSE 0 END) + "
        "  (CASE WHEN data_categories IS NOT NULL AND data_categories != '' AND data_categories != '[]' THEN 1 ELSE 0 END) + "
        "  (CASE WHEN transfer_basis IS NOT NULL AND transfer_basis != '' THEN 1 ELSE 0 END) + "
        "  (CASE WHEN confirmed = 1 THEN 1 ELSE 0 END) AS score, "
        "  COUNT(*) AS edge_count "
        "FROM edges "
        "GROUP BY score "
        "ORDER BY score"
    )
    with get_session(engine) as session:
        rows = session.execute(sql_completeness).fetchall()
    results["edge_completeness"] = {str(row[0]): row[1] for row in rows}

    # 8. source_breakdown — edge count by source column
    sql_source = text(
        "SELECT COALESCE(source, 'unknown') AS src, COUNT(*) AS cnt "
        "FROM edges "
        "GROUP BY src "
        "ORDER BY cnt DESC"
    )
    with get_session(engine) as session:
        rows = session.execute(sql_source).fetchall()
    results["source_breakdown"] = {row[0]: row[1] for row in rows}

    # 9. fetch_staleness — latest fetch per domain bucketed by age
    sql_staleness = text(
        "WITH latest AS ( "
        "  SELECT domain, MAX(fetched_at) AS latest_at "
        "  FROM fetch_log GROUP BY domain "
        ") "
        "SELECT "
        "  CASE "
        "    WHEN julianday('now') - julianday(latest_at) < 7 THEN '< 7 days' "
        "    WHEN julianday('now') - julianday(latest_at) < 30 THEN '7–30 days' "
        "    WHEN julianday('now') - julianday(latest_at) < 90 THEN '30–90 days' "
        "    ELSE '> 90 days' "
        "  END AS bucket, "
        "  COUNT(*) AS cnt "
        "FROM latest "
        "GROUP BY bucket "
        "ORDER BY MIN(julianday('now') - julianday(latest_at))"
    )
    with get_session(engine) as session:
        rows = session.execute(sql_staleness).fetchall()
    results["fetch_staleness"] = {row[0]: row[1] for row in rows}

    # 10. fetch_success — latest status per domain
    sql_fetch_status = text(
        "WITH latest AS ( "
        "  SELECT fl.domain, fl.fetch_status "
        "  FROM fetch_log fl "
        "  INNER JOIN ( "
        "    SELECT domain, MAX(id) AS max_id FROM fetch_log GROUP BY domain "
        "  ) li ON fl.id = li.max_id "
        ") "
        "SELECT fetch_status, COUNT(*) AS cnt "
        "FROM latest "
        "GROUP BY fetch_status "
        "ORDER BY cnt DESC"
    )
    with get_session(engine) as session:
        rows = session.execute(sql_fetch_status).fetchall()
    results["fetch_success"] = {row[0]: row[1] for row in rows}

    # 11. field_coverage — per seed company, % of edges with each field populated
    sql_coverage = text(
        "SELECT "
        "  e.parent_domain, "
        "  c.company_name, "
        "  COUNT(*) AS total, "
        "  SUM(CASE WHEN e.purposes IS NOT NULL AND e.purposes != '' AND e.purposes != '[]' THEN 1 ELSE 0 END) AS has_purposes, "
        "  SUM(CASE WHEN e.data_categories IS NOT NULL AND e.data_categories != '' AND e.data_categories != '[]' THEN 1 ELSE 0 END) AS has_categories, "
        "  SUM(CASE WHEN e.transfer_basis IS NOT NULL AND e.transfer_basis != '' THEN 1 ELSE 0 END) AS has_basis "
        "FROM edges e "
        "JOIN companies c ON e.parent_domain = c.domain "
        "WHERE c.is_seed = 1 "
        "GROUP BY e.parent_domain "
        "ORDER BY (has_purposes + has_categories + has_basis) * 1.0 / (total * 3) ASC"
    )
    with get_session(engine) as session:
        rows = session.execute(sql_coverage).fetchall()
    results["field_coverage"] = [
        {
            "domain": row[0],
            "company_name": row[1],
            "total": row[2],
            "has_purposes": row[3],
            "has_categories": row[4],
            "has_basis": row[5],
            "pct": round((row[3] + row[4] + row[5]) * 100.0 / (row[2] * 3)) if row[2] > 0 else 0,
        }
        for row in rows
    ]

    # 12. quality_breakdown — data quality per seed company based on source_url
    sql_quality = text(
        "WITH latest AS ( "
        "  SELECT fl.domain, fl.source_url, fl.fetch_status "
        "  FROM fetch_log fl "
        "  INNER JOIN ( "
        "    SELECT domain, MAX(id) AS max_id FROM fetch_log GROUP BY domain "
        "  ) li ON fl.id = li.max_id "
        ") "
        "SELECT "
        "  CASE "
        "    WHEN l.source_url LIKE '%sub-processor%' OR l.source_url LIKE '%subprocessor%' "
        "         OR l.source_url LIKE '%sub_processor%' OR l.source_url LIKE '%third-part%' "
        "         OR l.source_url LIKE '%vendors%' OR l.source_url LIKE '%trust-center%' "
        "         OR l.source_url LIKE '%trust_center%' OR l.source_url LIKE '%data-processing%' "
        "         OR l.source_url LIKE '%data_processing%' "
        "      THEN 'high' "
        "    WHEN l.source_url LIKE '%privacy%' OR l.source_url LIKE '%gdpr%' "
        "         OR l.source_url LIKE '%legal%' OR l.source_url LIKE '%cookie%' "
        "         OR l.source_url LIKE '%data-protection%' OR l.source_url LIKE '%compliance%' "
        "      THEN 'medium' "
        "    WHEN l.source_url IS NOT NULL AND l.source_url != '' "
        "      THEN 'low' "
        "    WHEN l.fetch_status = 'ok' "
        "      THEN 'low' "
        "    ELSE 'unknown' "
        "  END AS quality, "
        "  COUNT(*) AS cnt "
        "FROM companies c "
        "LEFT JOIN latest l ON c.domain = l.domain "
        "WHERE c.is_seed = 1 "
        "GROUP BY quality"
    )
    with get_session(engine) as session:
        rows = session.execute(sql_quality).fetchall()
    results["quality_breakdown"] = {row[0]: row[1] for row in rows}

    # 13. country_coverage — SP nodes with known vs unknown country
    sql_cc = text(
        "SELECT "
        "  CASE WHEN hq_country_code IS NOT NULL AND hq_country_code != '' THEN 'known' ELSE 'unknown' END AS coverage, "
        "  COUNT(*) AS cnt "
        "FROM companies "
        "WHERE is_seed = 0 "
        "GROUP BY coverage"
    )
    with get_session(engine) as session:
        rows = session.execute(sql_cc).fetchall()
    results["country_coverage"] = {row[0]: row[1] for row in rows}

    # Upsert all into AnalyticsCache
    with get_session(engine) as session:
        for key, value in results.items():
            # Convert dict keys that are ints (chain_depth) to strings for JSON
            json_value = json.dumps(value, default=str)
            existing = session.query(AnalyticsCache).filter(AnalyticsCache.key == key).first()
            if existing:
                existing.value = json_value
                existing.computed_at = now
            else:
                session.add(AnalyticsCache(key=key, value=json_value, computed_at=now))

    return {"keys_updated": list(results.keys())}


def get_cached(engine: Engine, key: str):
    """Read from AnalyticsCache, parse JSON, return. Returns None if not found."""
    with get_session(engine) as session:
        row = session.query(AnalyticsCache).filter(AnalyticsCache.key == key).first()
        if row is None:
            return None
        return json.loads(row.value)
