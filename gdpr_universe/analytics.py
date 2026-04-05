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
