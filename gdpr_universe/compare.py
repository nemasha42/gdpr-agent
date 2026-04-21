"""Per-company metric computation for the /compare page.

Produces one row of comparison data per seed company:
    sp_count, category_count, adequate_pct, risky_pct, basis_pct,
    lockin_pct, xborder_count, xborder_total, max_depth, quality,
    transparency_score, geo_top_country, geo_top_pct,
    composite_score, grade

Cross-company analysis functions:
    compute_shared_sps   — which SPs are used by the most seeds
    compute_alternatives — group SPs by service_category to reveal vendor choices
    compute_sector_averages — per-sector mean of all metric columns
"""

from __future__ import annotations

import json
import logging
from collections import Counter, defaultdict
from datetime import datetime, timezone

from sqlalchemy import bindparam, text
from sqlalchemy.engine import Engine

from gdpr_universe.analytics import get_cached
from gdpr_universe.db import AnalyticsCache, get_session
from gdpr_universe.graph_builder import _SAFEGUARDED_COUNTRIES
from gdpr_universe.graph_queries import ADEQUATE_COUNTRIES
from gdpr_universe.routes.dashboard import _derive_quality

logger = logging.getLogger(__name__)

# EU/EEA countries — transfers within this zone are NOT cross-border
_EU_EEA = frozenset({
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE",
    "GR", "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT",
    "RO", "SK", "SI", "ES", "SE", "NO", "IS", "LI", "CH",
})


def _basis_is_documented(transfer_basis: str | None) -> bool:
    """Return True if the transfer basis is a recognised GDPR mechanism."""
    if not transfer_basis:
        return False
    tb = transfer_basis.strip()
    if tb.lower() in ("", "unknown", "none"):
        return False
    return True


def _quality_points(quality: str) -> int:
    """Convert quality label to numeric points (0-40)."""
    return {"high": 40, "medium": 20}.get(quality, 0)


def _transparency_score(quality: str, basis_pct: float, field_coverage_pct: float) -> float:
    """Transparency sub-score (0-100).

    Formula:
        quality_pts (high=40, medium=20, low/unknown=0)
        + basis_pct          * 30 / 100
        + field_coverage_pct * 30 / 100
    Capped at 100.

    field_coverage_pct is the percentage of edge fields (purposes, data_categories,
    transfer_basis) populated for this seed's SPs, read from AnalyticsCache
    "field_coverage" key. Defaults to 0 when analytics cache is not yet populated.
    """
    pts = (
        _quality_points(quality)
        + (basis_pct * 30 / 100)
        + (field_coverage_pct * 30 / 100)
    )
    return min(pts, 100.0)


def _composite_score(
    adequate_pct: float,
    basis_pct: float,
    transparency: float,
    lockin_pct: float,
    geo_top_pct: float,
) -> float:
    """Composite score (0-100).

    adequate_pct * 0.30
    + basis_pct  * 0.25
    + transparency_score * 0.20
    + (100 - lockin_pct)  * 0.15
    + (100 - geo_top_pct) * 0.10
    """
    return (
        adequate_pct * 0.30
        + basis_pct * 0.25
        + transparency * 0.20
        + (100 - lockin_pct) * 0.15
        + (100 - geo_top_pct) * 0.10
    )


def _grade(score: float) -> str:
    """Convert composite score to letter grade: A>=75, B>=50, C>=25, D<25."""
    if score >= 75:
        return "A"
    if score >= 50:
        return "B"
    if score >= 25:
        return "C"
    return "D"


def compute_company_metrics(engine: Engine, *, category: str = "") -> list[dict]:
    """Return one metrics dict per seed company.

    Args:
        engine: SQLAlchemy engine pointing at the universe SQLite DB.
        category: Optional service_category filter. When non-empty, only SPs
                  in that category are counted for per-company metrics.
                  Seeds are always returned regardless of their own category.

    Returns:
        list[dict] with keys:
            domain, company_name, hq_country_code, sector,
            sp_count, category_count,
            adequate_pct, risky_pct,
            basis_pct,
            lockin_pct,
            xborder_count, xborder_total,
            max_depth,
            quality,
            transparency_score,
            geo_top_country, geo_top_pct,
            composite_score, grade
    """
    with get_session(engine) as session:
        # ── 1. Load all seed companies ────────────────────────────────────
        seed_rows = session.execute(
            text(
                "SELECT domain, company_name, hq_country_code, sector "
                "FROM companies WHERE is_seed = 1"
            )
        ).fetchall()

        if not seed_rows:
            return []

        # ── 2. Load edges with child company info ─────────────────────────
        # Optionally filter by service_category on the child company.
        if category:
            edge_rows = session.execute(
                text(
                    "SELECT e.parent_domain, e.child_domain, e.depth, "
                    "       e.transfer_basis, "
                    "       c.hq_country_code, c.service_category "
                    "FROM edges e "
                    "JOIN companies c ON e.child_domain = c.domain "
                    "WHERE c.service_category = :cat"
                ),
                {"cat": category},
            ).fetchall()
        else:
            edge_rows = session.execute(
                text(
                    "SELECT e.parent_domain, e.child_domain, e.depth, "
                    "       e.transfer_basis, "
                    "       c.hq_country_code, c.service_category "
                    "FROM edges e "
                    "JOIN companies c ON e.child_domain = c.domain"
                )
            ).fetchall()

        # ── 3. Load latest fetch_log per seed ────────────────────────────
        fetch_rows = session.execute(
            text(
                "SELECT domain, source_url, fetch_status "
                "FROM fetch_log "
                "WHERE id IN (SELECT MAX(id) FROM fetch_log GROUP BY domain)"
            )
        ).fetchall()

    # ── 4. Build helper structures ────────────────────────────────────────

    # fetch info keyed by domain
    fetch_by_domain: dict[str, tuple[str | None, str | None]] = {
        row[0]: (row[1], row[2]) for row in fetch_rows
    }

    # field_coverage from analytics cache: {domain: pct (0-100)}
    field_coverage_cache: dict[str, float] = {}
    fc_data = get_cached(engine, "field_coverage")
    if fc_data is not None:
        for entry in fc_data:
            domain_key = entry.get("domain")
            pct = entry.get("pct", 0)
            if domain_key:
                try:
                    field_coverage_cache[domain_key] = float(pct)
                except (TypeError, ValueError):
                    field_coverage_cache[domain_key] = 0.0

    # Group edges by parent seed domain
    # edges_by_seed: {seed_domain: [(child_domain, depth, transfer_basis, country_code, category)]}
    edges_by_seed: dict[str, list[tuple[str, int, str | None, str | None, str | None]]] = {}
    for row in edge_rows:
        parent, child, depth, basis, country_code, svc_cat = row
        edges_by_seed.setdefault(parent, []).append(
            (child, depth or 0, basis, country_code, svc_cat)
        )

    # Compute global sharing count: child_domain → number of distinct parent seeds
    # (used for lock-in calculation across ALL edges, ignoring category filter)
    all_sharing: dict[str, set[str]] = {}
    for row in edge_rows:
        parent, child = row[0], row[1]
        all_sharing.setdefault(child, set()).add(parent)
    sharing_count: dict[str, int] = {k: len(v) for k, v in all_sharing.items()}

    # ── 5. Compute per-seed metrics ───────────────────────────────────────
    results: list[dict] = []

    for seed_row in seed_rows:
        domain, company_name, hq_country_code, sector = seed_row
        sp_edges = edges_by_seed.get(domain, [])
        sp_count = len(sp_edges)

        if sp_count == 0:
            # Zero-SP company — all percentage metrics are 0, avoid ZeroDivisionError
            source_url, fetch_status = fetch_by_domain.get(domain, (None, None))
            quality = _derive_quality(source_url, fetch_status)
            t_score = _transparency_score(quality, 0.0, 0.0)
            c_score = _composite_score(0.0, 0.0, t_score, 0.0, 0.0)
            results.append({
                "domain": domain,
                "company_name": company_name or domain,
                "hq_country_code": hq_country_code or "",
                "sector": sector or "",
                "sp_count": 0,
                "category_count": 0,
                "adequate_pct": 0.0,
                "risky_pct": 0.0,
                "basis_pct": 0.0,
                "lockin_pct": 0.0,
                "xborder_count": 0,
                "xborder_total": 0,
                "max_depth": 0,
                "quality": quality,
                "transparency_score": round(t_score, 1),
                "geo_top_country": "",
                "geo_top_pct": 0.0,
                "composite_score": round(c_score, 1),
                "grade": _grade(c_score),
            })
            continue

        # Unpack SP edge tuples
        adequate_count = 0
        risky_count = 0
        basis_count = 0
        xborder_count = 0
        lockin_count = 0
        max_depth = 0
        country_counter: Counter[str] = Counter()
        categories: set[str] = set()

        for child_domain, depth, transfer_basis, country_code, svc_cat in sp_edges:
            # Adequacy / risk classification
            code = country_code or ""
            if code in ADEQUATE_COUNTRIES:
                adequate_count += 1
            elif code and code not in _SAFEGUARDED_COUNTRIES:
                # Only truly risky: known country, not adequate, not safeguarded
                risky_count += 1

            # Transfer basis
            if _basis_is_documented(transfer_basis):
                basis_count += 1

            # Cross-border: SP outside EU/EEA
            if code and code not in _EU_EEA:
                xborder_count += 1

            # Lock-in: SP used by exactly 1 seed
            if sharing_count.get(child_domain, 1) == 1:
                lockin_count += 1

            # Max depth
            if depth > max_depth:
                max_depth = depth

            # Geo concentration
            if code:
                country_counter[code] += 1

            # Categories
            if svc_cat:
                categories.add(svc_cat)

        # Percentages
        adequate_pct = adequate_count / sp_count * 100
        risky_pct = risky_count / sp_count * 100
        basis_pct = basis_count / sp_count * 100
        lockin_pct = lockin_count / sp_count * 100

        # xborder_total is the denominator (= sp_count) so callers can compute
        # xborder percentage as xborder_count / xborder_total
        xborder_total = sp_count

        # Geo top
        if country_counter:
            geo_top_country, geo_top_cnt = country_counter.most_common(1)[0]
            geo_top_pct = geo_top_cnt / sp_count * 100
        else:
            geo_top_country = ""
            geo_top_pct = 0.0

        # Quality and field coverage from analytics cache
        source_url, fetch_status = fetch_by_domain.get(domain, (None, None))
        quality = _derive_quality(source_url, fetch_status)
        field_coverage_pct = field_coverage_cache.get(domain, 0.0)

        t_score = _transparency_score(quality, basis_pct, field_coverage_pct)
        c_score = _composite_score(adequate_pct, basis_pct, t_score, lockin_pct, geo_top_pct)

        results.append({
            "domain": domain,
            "company_name": company_name or domain,
            "hq_country_code": hq_country_code or "",
            "sector": sector or "",
            "sp_count": sp_count,
            "category_count": len(categories),
            "adequate_pct": round(adequate_pct, 1),
            "risky_pct": round(risky_pct, 1),
            "basis_pct": round(basis_pct, 1),
            "lockin_pct": round(lockin_pct, 1),
            "xborder_count": xborder_count,
            "xborder_total": xborder_total,
            "max_depth": max_depth,
            "quality": quality,
            "transparency_score": round(t_score, 1),
            "geo_top_country": geo_top_country,
            "geo_top_pct": round(geo_top_pct, 1),
            "composite_score": round(c_score, 1),
            "grade": _grade(c_score),
        })

    return results


def compute_shared_sps(engine: Engine, *, top_n: int = 0) -> dict[str, dict]:
    """Return SPs sorted by the number of distinct seed companies that use them.

    Args:
        engine: SQLAlchemy engine pointing at the universe SQLite DB.
        top_n:  If > 0, limit results to the top_n most-shared SPs.
                0 (default) returns all.

    Returns:
        Ordered dict keyed by SP domain:
            {sp_domain: {"count": int, "pct": int, "name": str}}
        Sorted count descending.  ``pct`` = round(count * 100 / total_seeds).
    """
    with get_session(engine) as session:
        # Count of distinct seeds
        total_seeds_row = session.execute(
            text("SELECT COUNT(*) FROM companies WHERE is_seed = 1")
        ).fetchone()
        total_seeds: int = total_seeds_row[0] if total_seeds_row else 0

        if total_seeds == 0:
            return {}

        # For each SP child_domain, count distinct seed parent_domains.
        # We restrict parent_domain to is_seed=1 companies so sub-SP edges
        # don't inflate counts.
        rows = session.execute(
            text(
                "SELECT e.child_domain, COUNT(DISTINCT e.parent_domain) AS cnt, "
                "       c.company_name "
                "FROM edges e "
                "JOIN companies seed ON e.parent_domain = seed.domain AND seed.is_seed = 1 "
                "JOIN companies c    ON e.child_domain  = c.domain "
                "GROUP BY e.child_domain "
                "ORDER BY cnt DESC"
            )
        ).fetchall()

    result: dict[str, dict] = {}
    for row in rows:
        child_domain, count, company_name = row
        pct = round(count * 100 / total_seeds)
        result[child_domain] = {
            "count": count,
            "pct": pct,
            "name": company_name or child_domain,
        }

    if top_n > 0:
        result = dict(list(result.items())[:top_n])

    return result


def compute_alternatives(engine: Engine) -> list[dict]:
    """Group SPs by service_category to expose alternative vendor choices.

    Only categories with 2+ distinct SP vendors are included — a single vendor
    in a category means there is no choice to highlight.

    Returns:
        list of dicts, each with:
            {
                "category": str,
                "vendors": [
                    {"domain": str, "name": str, "used_by": [seed_domain, ...]},
                    ...
                ],
            }
        Sorted by category name ascending.
    """
    with get_session(engine) as session:
        # Load all seed→SP edges enriched with SP service_category.
        # Restrict parent to seed companies only.
        rows = session.execute(
            text(
                "SELECT e.parent_domain, e.child_domain, "
                "       sp.service_category, sp.company_name "
                "FROM edges e "
                "JOIN companies seed ON e.parent_domain = seed.domain AND seed.is_seed = 1 "
                "JOIN companies sp   ON e.child_domain  = sp.domain "
                "WHERE sp.service_category IS NOT NULL AND sp.service_category != ''"
            )
        ).fetchall()

    # category → {sp_domain → {"name": str, "used_by": set[seed_domain]}}
    category_map: dict[str, dict[str, dict]] = defaultdict(dict)
    for parent_domain, child_domain, svc_cat, sp_name in rows:
        if child_domain not in category_map[svc_cat]:
            category_map[svc_cat][child_domain] = {
                "domain": child_domain,
                "name": sp_name or child_domain,
                "used_by": set(),
            }
        category_map[svc_cat][child_domain]["used_by"].add(parent_domain)

    result: list[dict] = []
    for category in sorted(category_map.keys()):
        vendors_map = category_map[category]
        if len(vendors_map) < 2:
            continue  # only show categories with genuine alternatives
        vendors = [
            {
                "domain": v["domain"],
                "name": v["name"],
                "used_by": sorted(v["used_by"]),
            }
            for v in sorted(vendors_map.values(), key=lambda x: x["domain"])
        ]
        result.append({"category": category, "vendors": vendors})

    return result


def compute_sector_averages(engine: Engine) -> dict[str, dict]:
    """Compute mean metric values grouped by seed company sector.

    Calls ``compute_company_metrics()`` internally to get per-company data,
    then groups by sector and computes column means.

    Returns:
        {sector: {avg_sp_count, avg_adequate_pct, avg_risky_pct, avg_basis_pct,
                  avg_lockin_pct, avg_xborder_count, avg_max_depth,
                  avg_transparency_score, avg_composite_score, count}}
    """
    per_company = compute_company_metrics(engine)

    # Group rows by sector
    by_sector: dict[str, list[dict]] = defaultdict(list)
    for row in per_company:
        sector = row.get("sector") or "Unknown"
        by_sector[sector].append(row)

    metric_cols = [
        "sp_count", "adequate_pct", "risky_pct", "basis_pct",
        "lockin_pct", "xborder_count", "max_depth",
        "transparency_score", "composite_score",
    ]

    result: dict[str, dict] = {}
    for sector, rows in sorted(by_sector.items()):
        n = len(rows)
        averages: dict[str, float | int] = {"count": n}
        for col in metric_cols:
            total = sum(r.get(col, 0) or 0 for r in rows)
            averages[f"avg_{col}"] = round(total / n, 2) if n else 0.0
        result[sector] = averages

    return result


def refresh_compare(engine: Engine) -> dict:
    """Recompute all comparison metrics and store in AnalyticsCache.

    Returns {"keys_updated": [list of key names]}.
    """
    now = datetime.now(timezone.utc)
    results: dict[str, object] = {
        "compare_matrix": compute_company_metrics(engine),
        "compare_shared_sps": compute_shared_sps(engine, top_n=20),
        "compare_alternatives": compute_alternatives(engine),
        "compare_sector_averages": compute_sector_averages(engine),
    }

    with get_session(engine) as session:
        for key, value in results.items():
            json_value = json.dumps(value, default=str)
            existing = session.query(AnalyticsCache).filter(AnalyticsCache.key == key).first()
            if existing:
                existing.value = json_value
                existing.computed_at = now
            else:
                session.add(AnalyticsCache(key=key, value=json_value, computed_at=now))

    return {"keys_updated": list(results.keys())}


def get_compare_data(engine: Engine) -> dict:
    """Read all comparison data from AnalyticsCache.

    Returns a dict with keys: matrix, shared_sps, alternatives, sector_averages.
    Values are None if not yet computed.
    """
    key_map = {
        "compare_matrix": "matrix",
        "compare_shared_sps": "shared_sps",
        "compare_alternatives": "alternatives",
        "compare_sector_averages": "sector_averages",
    }
    result: dict[str, object] = {alias: None for alias in key_map.values()}
    for cache_key, alias in key_map.items():
        cached = get_cached(engine, cache_key)
        if cached is not None:
            result[alias] = cached
    return result


def compute_side_by_side(engine: Engine, domains: list[str]) -> dict:
    """Compute overlap data for 2-5 selected companies.

    Args:
        engine:  SQLAlchemy engine pointing at the universe SQLite DB.
        domains: List of 2-5 seed company domains to compare.

    Returns a dict with:
        companies: list of per-domain dicts (domain, company_name, hq_country_code,
                   sector, metrics, grade, sps)
        overlap:   shared_by_all, shared_by_some, total_unique, combined_xborder_pct

    Raises:
        ValueError: if len(domains) is not between 2 and 5 inclusive.
    """
    if not (2 <= len(domains) <= 5):
        raise ValueError(f"domains must have 2–5 entries, got {len(domains)}")

    # Fetch cached matrix rows, or compute fresh if cache is empty.
    cached_matrix = get_cached(engine, "compare_matrix")
    if cached_matrix:
        matrix_by_domain: dict[str, dict] = {r["domain"]: r for r in cached_matrix}
    else:
        fresh = compute_company_metrics(engine)
        matrix_by_domain = {r["domain"]: r for r in fresh}

    # Load SP lists from edges table for requested domains.
    # Use expanding bindparam so SQLAlchemy generates the correct number of
    # placeholders for SQLite (IN (?, ?, ...)).
    with get_session(engine) as session:
        sp_rows = session.execute(
            text(
                "SELECT parent_domain, child_domain "
                "FROM edges "
                "WHERE parent_domain IN :domains"
            ).bindparams(bindparam("domains", expanding=True)),
            {"domains": list(domains)},
        ).fetchall()

    # Build per-domain SP sets.
    sps_by_domain: dict[str, set[str]] = {d: set() for d in domains}
    for parent, child in sp_rows:
        if parent in sps_by_domain:
            sps_by_domain[parent].add(child)

    # Compute overlap metrics.
    all_sp_sets = [sps_by_domain[d] for d in domains]
    shared_by_all_set: set[str] = all_sp_sets[0].copy()
    for s in all_sp_sets[1:]:
        shared_by_all_set &= s

    all_unique: set[str] = set()
    for s in all_sp_sets:
        all_unique |= s

    # shared_by_some: SP → which selected domains use it (at least 2 domains, not all).
    shared_by_some: dict[str, list[str]] = {}
    for sp in all_unique - shared_by_all_set:
        users = [d for d in domains if sp in sps_by_domain[d]]
        if len(users) >= 2:
            shared_by_some[sp] = users

    # combined_xborder_pct: weighted average of each domain's xborder pct.
    xborder_parts: list[float] = []
    for d in domains:
        row = matrix_by_domain.get(d, {})
        total = row.get("xborder_total") or 0
        count = row.get("xborder_count") or 0
        if total > 0:
            xborder_parts.append(count / total * 100)
    combined_xborder_pct = round(sum(xborder_parts) / len(xborder_parts)) if xborder_parts else 0

    companies = []
    for d in domains:
        row = matrix_by_domain.get(d, {})
        companies.append({
            "domain": d,
            "company_name": row.get("company_name", d),
            "hq_country_code": row.get("hq_country_code", ""),
            "sector": row.get("sector", ""),
            "metrics": {
                k: row.get(k)
                for k in (
                    "sp_count", "category_count", "adequate_pct", "risky_pct",
                    "basis_pct", "lockin_pct", "xborder_count", "xborder_total",
                    "max_depth", "transparency_score", "composite_score",
                )
            },
            "grade": row.get("grade", "D"),
            "sps": sorted(sps_by_domain[d]),
        })

    return {
        "companies": companies,
        "overlap": {
            "shared_by_all": sorted(shared_by_all_set),
            "shared_by_some": {sp: sorted(users) for sp, users in shared_by_some.items()},
            "total_unique": len(all_unique),
            "combined_xborder_pct": combined_xborder_pct,
        },
    }
