"""Per-company metric computation for the /compare page.

Produces one row of comparison data per seed company:
    sp_count, category_count, adequate_pct, risky_pct, basis_pct,
    lockin_pct, xborder_count, xborder_total, max_depth, quality,
    transparency_score, geo_top_country, geo_top_pct,
    composite_score, grade
"""

from __future__ import annotations

import logging
from collections import Counter

from sqlalchemy import text
from sqlalchemy.engine import Engine

from gdpr_universe.analytics import get_cached
from gdpr_universe.db import get_session
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
