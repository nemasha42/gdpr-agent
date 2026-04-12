"""Graph query functions using SQLite recursive CTEs."""

from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.engine import Engine

from gdpr_universe.db import get_session

# EU adequacy decisions + US (DPF) + EEA/EFTA members
ADEQUATE_COUNTRIES = {
    "AD", "AR", "AT", "BE", "BG", "CA", "CH", "CY", "CZ", "DE",
    "DK", "EE", "ES", "FI", "FO", "FR", "GB", "GG", "GR", "HR",
    "HU", "IE", "IL", "IM", "IS", "IT", "JE", "JP", "KR", "LI",
    "LT", "LU", "LV", "MT", "NL", "NO", "NZ", "PL", "PT", "RO",
    "SE", "SI", "SK", "US", "UY",
}


def sharing_counts(engine: Engine, *, limit: int = 0) -> dict[str, int]:
    """Return {child_domain: count_of_distinct_parents}, descending by count."""
    sql = text(
        "SELECT child_domain, COUNT(DISTINCT parent_domain) AS cnt "
        "FROM edges "
        "GROUP BY child_domain "
        "ORDER BY cnt DESC"
    )
    with get_session(engine) as session:
        rows = session.execute(sql).fetchall()

    result: dict[str, int] = {}
    for row in rows:
        result[row[0]] = row[1]
        if limit and len(result) >= limit:
            break
    return result


def blast_radius(engine: Engine, domain: str, *, max_hops: int = 5) -> list[dict]:
    """Recursive CTE tracing all upstream parents affected if domain is compromised.

    Returns [{"domain": str, "hops": int}] sorted by hops.
    The target domain itself is excluded.
    """
    sql = text(
        "WITH RECURSIVE upstream AS ( "
        "    SELECT parent_domain, child_domain, 1 AS hops "
        "    FROM edges WHERE child_domain = :target "
        "    UNION ALL "
        "    SELECT e.parent_domain, e.child_domain, u.hops + 1 "
        "    FROM edges e JOIN upstream u ON e.child_domain = u.parent_domain "
        "    WHERE u.hops < :max_hops "
        ") "
        "SELECT parent_domain, MIN(hops) AS min_hops "
        "FROM upstream GROUP BY parent_domain ORDER BY min_hops ASC"
    )
    with get_session(engine) as session:
        rows = session.execute(sql, {"target": domain, "max_hops": max_hops}).fetchall()

    return [{"domain": row[0], "hops": row[1]} for row in rows]


def concentration_risk(
    engine: Engine, service_category: str, *, limit: int = 10
) -> list[dict]:
    """Top SPs in a category by parent company count."""
    sql = text(
        "SELECT c.domain, c.company_name, COUNT(DISTINCT e.parent_domain) AS user_count "
        "FROM companies c "
        "JOIN edges e ON c.domain = e.child_domain "
        "WHERE c.service_category = :category "
        "GROUP BY c.domain, c.company_name "
        "ORDER BY user_count DESC "
        "LIMIT :limit"
    )
    with get_session(engine) as session:
        rows = session.execute(
            sql, {"category": service_category, "limit": limit}
        ).fetchall()

    return [
        {"domain": row[0], "company_name": row[1], "user_count": row[2]}
        for row in rows
    ]


def neighborhood(
    engine: Engine, domain: str, *, hops: int = 2
) -> tuple[list[dict], list[dict]]:
    """Return (nodes, edges) within N hops of a domain (both directions)."""
    # Collect edges within N hops using a recursive CTE in both directions
    sql = text(
        "WITH RECURSIVE reachable(domain, remaining) AS ( "
        "    SELECT :target, :hops "
        "    UNION "
        "    SELECT e.parent_domain, r.remaining - 1 "
        "    FROM edges e JOIN reachable r ON e.child_domain = r.domain "
        "    WHERE r.remaining > 0 "
        "    UNION "
        "    SELECT e.child_domain, r.remaining - 1 "
        "    FROM edges e JOIN reachable r ON e.parent_domain = r.domain "
        "    WHERE r.remaining > 0 "
        ") "
        "SELECT DISTINCT domain FROM reachable"
    )
    with get_session(engine) as session:
        domain_rows = session.execute(sql, {"target": domain, "hops": hops}).fetchall()
        reachable_domains = {row[0] for row in domain_rows}

        # Get nodes
        if not reachable_domains:
            return [], []

        placeholders = ", ".join(f":d{i}" for i in range(len(reachable_domains)))
        params = {f"d{i}": d for i, d in enumerate(reachable_domains)}

        node_sql = text(
            f"SELECT domain, company_name, hq_country_code, is_seed, service_category "
            f"FROM companies WHERE domain IN ({placeholders})"
        )
        node_rows = session.execute(node_sql, params).fetchall()

        # Get edges where both endpoints are in reachable set
        edge_sql = text(
            f"SELECT parent_domain, child_domain, depth, purposes, transfer_basis "
            f"FROM edges WHERE parent_domain IN ({placeholders}) "
            f"AND child_domain IN ({placeholders})"
        )
        # Need params for both parent and child IN clauses
        edge_params = {}
        edge_params.update(params)
        edge_rows = session.execute(edge_sql, edge_params).fetchall()

    nodes = [
        {
            "domain": r[0],
            "company_name": r[1],
            "hq_country_code": r[2],
            "is_seed": bool(r[3]),
            "service_category": r[4] or "",
        }
        for r in node_rows
    ]
    edges = [
        {
            "parent_domain": r[0],
            "child_domain": r[1],
            "depth": r[2],
            "purposes": r[3],
            "transfer_basis": r[4],
        }
        for r in edge_rows
    ]
    return nodes, edges


def risky_chains(engine: Engine, *, max_depth: int = 4) -> list[dict]:
    """Find chains from seed companies ending at non-adequate jurisdictions."""
    # Use recursive CTE to trace from seeds through edges
    sql = text(
        "WITH RECURSIVE chain AS ( "
        "    SELECT c.domain AS seed_domain, e.child_domain AS current_domain, 1 AS chain_depth "
        "    FROM companies c "
        "    JOIN edges e ON c.domain = e.parent_domain "
        "    WHERE c.is_seed = 1 "
        "    UNION ALL "
        "    SELECT ch.seed_domain, e.child_domain, ch.chain_depth + 1 "
        "    FROM chain ch "
        "    JOIN edges e ON ch.current_domain = e.parent_domain "
        "    WHERE ch.chain_depth < :max_depth "
        ") "
        "SELECT ch.seed_domain, ch.current_domain AS endpoint_domain, "
        "       c.hq_country_code AS endpoint_country, MIN(ch.chain_depth) AS chain_depth "
        "FROM chain ch "
        "JOIN companies c ON ch.current_domain = c.domain "
        "WHERE c.hq_country_code IS NOT NULL AND c.hq_country_code != '' "
        "GROUP BY ch.seed_domain, ch.current_domain, c.hq_country_code "
        "ORDER BY chain_depth ASC"
    )
    with get_session(engine) as session:
        rows = session.execute(sql, {"max_depth": max_depth}).fetchall()

    return [
        {
            "seed_domain": row[0],
            "endpoint_domain": row[1],
            "endpoint_country": row[2],
            "chain_depth": row[3],
        }
        for row in rows
        if row[2] not in ADEQUATE_COUNTRIES
    ]


def chain_depth_distribution(engine: Engine) -> dict[int, int]:
    """Return {depth: edge_count} for all edges."""
    sql = text(
        "SELECT depth, COUNT(*) AS cnt FROM edges GROUP BY depth ORDER BY depth"
    )
    with get_session(engine) as session:
        rows = session.execute(sql).fetchall()

    return {row[0]: row[1] for row in rows}
