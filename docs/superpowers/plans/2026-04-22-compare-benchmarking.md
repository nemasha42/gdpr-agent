# Company Benchmarking & Comparison — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a `/compare` page to GDPR Universe that benchmarks all seed companies across GDPR compliance dimensions with Chart.js visualizations, a sortable matrix, and a side-by-side detail view for up to 5 selected companies.

**Architecture:** Server-side metric computation in `compare.py` (follows `analytics.py` pattern), cached in `AnalyticsCache`. Flask blueprint serves the page with precomputed data. Chart.js renders 7 charts client-side. Vanilla JS handles search, sort, checkboxes, and a fetch-based side-by-side panel.

**Tech Stack:** Python 3 / Flask / SQLAlchemy (existing), Chart.js v4 (CDN), vanilla JS

**Spec:** `docs/superpowers/specs/2026-04-22-compare-benchmarking-design.md`

---

## File Structure

| File | Action | Responsibility |
|------|--------|---------------|
| `gdpr_universe/compare.py` | Create | All metric computation: per-company matrix, shared SPs, alternatives, sector averages, composite score |
| `gdpr_universe/routes/compare.py` | Create | Flask blueprint: `/compare`, `/compare/refresh`, `/api/compare/side-by-side` |
| `gdpr_universe/templates/compare.html` | Create | Jinja template: charts, panels, matrix, side-by-side container |
| `gdpr_universe/static/js/compare.js` | Create | Chart.js init, search, sort, checkboxes, side-by-side fetch |
| `gdpr_universe/app.py` | Modify | Register `compare_bp` blueprint |
| `gdpr_universe/templates/base.html` | Modify | Add "Compare" nav link |
| `tests/unit/test_universe_compare.py` | Create | All comparison metric and route tests |

---

### Task 1: Per-Company Metric Computation

**Files:**
- Create: `tests/unit/test_universe_compare.py`
- Create: `gdpr_universe/compare.py`

This task builds the core `compute_company_metrics()` function that produces one row of comparison data per seed company: SP count, category count, jurisdiction percentages, basis coverage, lock-in ratio, cross-border count, max depth, quality, transparency score, and composite grade.

- [ ] **Step 1: Write failing tests for per-company metrics**

```python
# tests/unit/test_universe_compare.py
"""Tests for the company benchmarking comparison module."""

import json

import pytest

from gdpr_universe.app import create_app
from gdpr_universe.db import Company, Edge, FetchLog, get_engine, get_session, init_db


@pytest.fixture()
def engine(tmp_path):
    """Create a temp DB with seed companies and edges for testing."""
    db_path = str(tmp_path / "test_compare.db")
    eng = get_engine(db_path)
    init_db(eng)

    with get_session(eng) as session:
        # Seed companies
        session.add(Company(
            domain="acme.com", company_name="Acme Corp",
            hq_country_code="DE", sector="Manufacturing",
            is_seed=True, service_category="",
        ))
        session.add(Company(
            domain="globex.com", company_name="Globex Inc",
            hq_country_code="NL", sector="Fintech",
            is_seed=True, service_category="",
        ))

        # Subprocessor companies
        session.add(Company(
            domain="stripe.com", company_name="Stripe",
            hq_country_code="US", is_seed=False,
            service_category="payments",
        ))
        session.add(Company(
            domain="aws.com", company_name="AWS",
            hq_country_code="US", is_seed=False,
            service_category="infrastructure",
        ))
        session.add(Company(
            domain="hetzner.com", company_name="Hetzner",
            hq_country_code="DE", is_seed=False,
            service_category="infrastructure",
        ))
        session.add(Company(
            domain="datadog.com", company_name="Datadog",
            hq_country_code="US", is_seed=False,
            service_category="analytics",
        ))

        # Edges: acme uses stripe, aws, hetzner (3 SPs)
        session.add(Edge(
            parent_domain="acme.com", child_domain="stripe.com",
            depth=0, transfer_basis="SCCs",
            purposes=json.dumps(["payments"]),
            source="manual",
        ))
        session.add(Edge(
            parent_domain="acme.com", child_domain="aws.com",
            depth=0, transfer_basis="DPF",
            purposes=json.dumps(["hosting"]),
            source="manual",
        ))
        session.add(Edge(
            parent_domain="acme.com", child_domain="hetzner.com",
            depth=0, transfer_basis=None,  # no basis documented
            purposes=json.dumps(["backup"]),
            source="manual",
        ))

        # Edges: globex uses stripe, aws, datadog (3 SPs)
        session.add(Edge(
            parent_domain="globex.com", child_domain="stripe.com",
            depth=0, transfer_basis="SCCs",
            purposes=json.dumps(["payments"]),
            source="manual",
        ))
        session.add(Edge(
            parent_domain="globex.com", child_domain="aws.com",
            depth=0, transfer_basis="DPF",
            purposes=json.dumps(["hosting"]),
            source="manual",
        ))
        session.add(Edge(
            parent_domain="globex.com", child_domain="datadog.com",
            depth=0, transfer_basis=None,
            purposes=json.dumps(["monitoring"]),
            source="manual",
        ))

        # Fetch logs
        session.add(FetchLog(
            domain="acme.com", fetch_status="ok",
            source_url="https://acme.com/sub-processors",
            sp_count=3,
        ))
        session.add(FetchLog(
            domain="globex.com", fetch_status="ok",
            source_url="https://globex.com/privacy",
            sp_count=3,
        ))

    return eng


class TestCompanyMetrics:
    """Test per-company metric computation."""

    def test_compute_company_metrics_returns_all_seeds(self, engine):
        from gdpr_universe.compare import compute_company_metrics

        rows = compute_company_metrics(engine)
        domains = {r["domain"] for r in rows}
        assert domains == {"acme.com", "globex.com"}

    def test_sp_count(self, engine):
        from gdpr_universe.compare import compute_company_metrics

        rows = compute_company_metrics(engine)
        by_domain = {r["domain"]: r for r in rows}
        assert by_domain["acme.com"]["sp_count"] == 3
        assert by_domain["globex.com"]["sp_count"] == 3

    def test_category_count(self, engine):
        from gdpr_universe.compare import compute_company_metrics

        rows = compute_company_metrics(engine)
        by_domain = {r["domain"]: r for r in rows}
        # acme: payments, infrastructure (stripe, aws, hetzner -> 2 categories)
        assert by_domain["acme.com"]["category_count"] == 2
        # globex: payments, infrastructure, analytics (stripe, aws, datadog -> 3)
        assert by_domain["globex.com"]["category_count"] == 3

    def test_adequate_pct(self, engine):
        from gdpr_universe.compare import compute_company_metrics

        rows = compute_company_metrics(engine)
        by_domain = {r["domain"]: r for r in rows}
        # acme: hetzner=DE(adequate), stripe=US(adequate), aws=US(adequate) -> 100%
        assert by_domain["acme.com"]["adequate_pct"] == 100
        # globex: all US -> all adequate (US in ADEQUATE_COUNTRIES via DPF)
        assert by_domain["globex.com"]["adequate_pct"] == 100

    def test_basis_pct(self, engine):
        from gdpr_universe.compare import compute_company_metrics

        rows = compute_company_metrics(engine)
        by_domain = {r["domain"]: r for r in rows}
        # acme: 2 of 3 edges have transfer_basis -> 67%
        assert by_domain["acme.com"]["basis_pct"] == 67
        # globex: 2 of 3 edges have transfer_basis -> 67%
        assert by_domain["globex.com"]["basis_pct"] == 67

    def test_lockin_pct(self, engine):
        from gdpr_universe.compare import compute_company_metrics

        rows = compute_company_metrics(engine)
        by_domain = {r["domain"]: r for r in rows}
        # acme: hetzner is unique (not used by globex) -> 1/3 = 33%
        assert by_domain["acme.com"]["lockin_pct"] == 33
        # globex: datadog is unique -> 1/3 = 33%
        assert by_domain["globex.com"]["lockin_pct"] == 33

    def test_xborder_count(self, engine):
        from gdpr_universe.compare import compute_company_metrics

        rows = compute_company_metrics(engine)
        by_domain = {r["domain"]: r for r in rows}
        # acme: stripe(US), aws(US) are non-EU; hetzner(DE) is EU -> 2 xborder
        assert by_domain["acme.com"]["xborder_count"] == 2
        assert by_domain["acme.com"]["xborder_total"] == 3

    def test_quality(self, engine):
        from gdpr_universe.compare import compute_company_metrics

        rows = compute_company_metrics(engine)
        by_domain = {r["domain"]: r for r in rows}
        # acme: source_url has /sub-processors -> high
        assert by_domain["acme.com"]["quality"] == "high"
        # globex: source_url has /privacy -> medium
        assert by_domain["globex.com"]["quality"] == "medium"

    def test_category_filter(self, engine):
        from gdpr_universe.compare import compute_company_metrics

        rows = compute_company_metrics(engine, category="payments")
        by_domain = {r["domain"]: r for r in rows}
        # Both companies use stripe (payments) only when filtered
        assert by_domain["acme.com"]["sp_count"] == 1
        assert by_domain["globex.com"]["sp_count"] == 1

    def test_zero_sp_company(self, engine):
        """Company with no edges gets 0 for all metrics, no division error."""
        with get_session(engine) as session:
            session.add(Company(
                domain="empty.com", company_name="Empty Co",
                hq_country_code="FR", sector="Other",
                is_seed=True, service_category="",
            ))

        from gdpr_universe.compare import compute_company_metrics

        rows = compute_company_metrics(engine)
        by_domain = {r["domain"]: r for r in rows}
        assert by_domain["empty.com"]["sp_count"] == 0
        assert by_domain["empty.com"]["lockin_pct"] == 0
        assert by_domain["empty.com"]["basis_pct"] == 0
        assert by_domain["empty.com"]["adequate_pct"] == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/test_universe_compare.py -v`
Expected: All tests FAIL with `ModuleNotFoundError: No module named 'gdpr_universe.compare'`

- [ ] **Step 3: Implement `compute_company_metrics()`**

```python
# gdpr_universe/compare.py
"""Company benchmarking — metric computation and caching."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy import text
from sqlalchemy.engine import Engine

from gdpr_universe.db import AnalyticsCache, get_session
from gdpr_universe.graph_builder import _assess_risk, _effective_country
from gdpr_universe.graph_queries import ADEQUATE_COUNTRIES
from gdpr_universe.routes.dashboard import _derive_quality

# EU/EEA countries for cross-border detection
_EU_EEA = {
    "AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR",
    "DE", "GR", "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL",
    "PL", "PT", "RO", "SK", "SI", "ES", "SE",
    "NO", "IS", "LI", "CH",  # EEA + CH
}

# Countries with safeguards (SCCs/BCRs) but not adequate
_SAFEGUARDED = {"IN", "BR", "PH", "SG", "MY", "TH", "MX", "CO", "CL", "AE"}


def compute_company_metrics(
    engine: Engine,
    *,
    category: str = "",
) -> list[dict]:
    """Compute comparison metrics for every seed company.

    Args:
        engine: SQLAlchemy engine.
        category: If non-empty, scope metrics to edges where the child
                  company's service_category matches this value.

    Returns:
        List of dicts, one per seed company, with all comparison fields.
    """
    # 1. Load all seed companies
    with get_session(engine) as session:
        seed_rows = session.execute(
            text(
                "SELECT domain, company_name, hq_country_code, sector "
                "FROM companies WHERE is_seed = 1"
            )
        ).fetchall()

        # 2. Load edges with child company info, optionally filtered by category
        edge_sql = (
            "SELECT e.parent_domain, e.child_domain, e.depth, "
            "       e.transfer_basis, c.hq_country_code, c.service_category "
            "FROM edges e "
            "JOIN companies c ON e.child_domain = c.domain"
        )
        params: dict = {}
        if category:
            edge_sql += " WHERE c.service_category = :cat"
            params["cat"] = category

        edge_rows = session.execute(text(edge_sql), params).fetchall()

        # 3. Load latest fetch log per seed
        fetch_rows = session.execute(
            text(
                "SELECT fl.domain, fl.fetch_status, fl.source_url "
                "FROM fetch_log fl "
                "INNER JOIN ("
                "    SELECT domain, MAX(id) AS max_id FROM fetch_log GROUP BY domain"
                ") li ON fl.id = li.max_id"
            )
        ).fetchall()

    fetch_map: dict[str, tuple[str, str]] = {}
    for row in fetch_rows:
        fetch_map[row[0]] = (row[1] or "", row[2] or "")

    # 4. Build per-parent edge data
    parent_edges: dict[str, list[dict]] = {}
    all_child_parents: dict[str, set[str]] = {}  # child_domain -> set of parent_domains

    for row in edge_rows:
        parent, child, depth, basis, child_country, child_cat = row
        entry = {
            "child": child,
            "depth": depth or 0,
            "basis": basis,
            "child_country": _effective_country(child_country, child),
        }
        parent_edges.setdefault(parent, []).append(entry)
        all_child_parents.setdefault(child, set()).add(parent)

    # 5. Compute metrics per seed
    results: list[dict] = []
    for seed_row in seed_rows:
        domain, name, country_code, sector = seed_row
        edges = parent_edges.get(domain, [])
        total = len(edges)

        if total == 0:
            results.append({
                "domain": domain,
                "company_name": name or domain,
                "hq_country_code": country_code or "",
                "sector": sector or "",
                "sp_count": 0,
                "category_count": 0,
                "adequate_pct": 0,
                "risky_pct": 0,
                "basis_pct": 0,
                "lockin_pct": 0,
                "xborder_count": 0,
                "xborder_total": 0,
                "max_depth": 0,
                "quality": _get_quality(domain, fetch_map),
                "transparency_score": 0,
                "geo_top_country": "",
                "geo_top_pct": 0,
                "composite_score": 0,
                "grade": "D",
            })
            continue

        # Jurisdiction counts
        adequate = sum(
            1 for e in edges
            if e["child_country"] and e["child_country"] in ADEQUATE_COUNTRIES
        )
        risky = sum(
            1 for e in edges
            if e["child_country"]
            and e["child_country"] not in ADEQUATE_COUNTRIES
            and e["child_country"] not in _SAFEGUARDED
        )

        # Basis coverage
        with_basis = sum(1 for e in edges if e["basis"])

        # Lock-in: SPs used only by this seed
        unique_sps = sum(
            1 for e in edges
            if len(all_child_parents.get(e["child"], set())) == 1
        )

        # Cross-border: child not in EU/EEA
        xborder = sum(
            1 for e in edges
            if e["child_country"] and e["child_country"] not in _EU_EEA
        )

        # Max depth
        max_depth = max((e["depth"] for e in edges), default=0)

        # Quality
        quality = _get_quality(domain, fetch_map)

        # Geographic concentration
        country_counts: dict[str, int] = {}
        for e in edges:
            cc = e["child_country"] or "unknown"
            country_counts[cc] = country_counts.get(cc, 0) + 1
        geo_top = max(country_counts, key=country_counts.get) if country_counts else ""
        geo_top_pct = round(country_counts.get(geo_top, 0) * 100 / total) if geo_top else 0

        adequate_pct = round(adequate * 100 / total)
        risky_pct = round(risky * 100 / total)
        basis_pct = round(with_basis * 100 / total)
        lockin_pct = round(unique_sps * 100 / total)

        # Transparency score (0-100)
        quality_pts = {"high": 40, "medium": 20, "low": 0, "unknown": 0}.get(quality, 0)
        basis_pts = round(basis_pct * 30 / 100)
        # field_coverage_pct placeholder — use basis_pct as proxy for now
        field_pts = round(basis_pct * 30 / 100)
        transparency_score = min(quality_pts + basis_pts + field_pts, 100)

        # Composite score
        composite = round(
            adequate_pct * 0.30
            + basis_pct * 0.25
            + transparency_score * 0.20
            + (100 - lockin_pct) * 0.15
            + (100 - geo_top_pct) * 0.10
        )
        grade = _score_to_grade(composite)

        results.append({
            "domain": domain,
            "company_name": name or domain,
            "hq_country_code": country_code or "",
            "sector": sector or "",
            "sp_count": total,
            "category_count": 0,  # filled below
            "adequate_pct": adequate_pct,
            "risky_pct": risky_pct,
            "basis_pct": basis_pct,
            "lockin_pct": lockin_pct,
            "xborder_count": xborder,
            "xborder_total": total,
            "max_depth": max_depth,
            "quality": quality,
            "transparency_score": transparency_score,
            "geo_top_country": geo_top,
            "geo_top_pct": geo_top_pct,
            "composite_score": composite,
            "grade": grade,
        })

    # Fill category_count in a second pass (need child company data)
    _fill_category_counts(engine, results, category=category)

    return results


def _fill_category_counts(
    engine: Engine,
    rows: list[dict],
    *,
    category: str = "",
) -> None:
    """Fill category_count on each row by querying distinct child categories."""
    cat_filter = ""
    params: dict = {}
    if category:
        cat_filter = " AND c.service_category = :cat"
        params["cat"] = category

    with get_session(engine) as session:
        for row in rows:
            result = session.execute(
                text(
                    "SELECT COUNT(DISTINCT c.service_category) "
                    "FROM edges e "
                    "JOIN companies c ON e.child_domain = c.domain "
                    "WHERE e.parent_domain = :parent "
                    "AND c.service_category IS NOT NULL "
                    "AND c.service_category != ''"
                    + cat_filter
                ),
                {"parent": row["domain"], **params},
            ).scalar() or 0
            row["category_count"] = result


def _get_quality(domain: str, fetch_map: dict[str, tuple[str, str]]) -> str:
    """Look up quality for a domain from the fetch_map."""
    if domain not in fetch_map:
        return "unknown"
    status, url = fetch_map[domain]
    return _derive_quality(url or None, status or None)


def _score_to_grade(score: int) -> str:
    """Map a 0-100 composite score to a letter grade."""
    if score >= 75:
        return "A"
    if score >= 50:
        return "B"
    if score >= 25:
        return "C"
    return "D"
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_universe_compare.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add gdpr_universe/compare.py tests/unit/test_universe_compare.py
git commit -m "feat(compare): add per-company metric computation with tests"
```

---

### Task 2: Cross-Company Metrics (Shared SPs, Alternatives, Sector Averages)

**Files:**
- Modify: `tests/unit/test_universe_compare.py`
- Modify: `gdpr_universe/compare.py`

This task adds three cross-company analysis functions: shared SP detection, common alternatives grouping, and sector peer averages.

- [ ] **Step 1: Write failing tests for cross-company metrics**

Append to `tests/unit/test_universe_compare.py`:

```python
class TestSharedSPs:
    """Test shared subprocessor computation."""

    def test_shared_sps(self, engine):
        from gdpr_universe.compare import compute_shared_sps

        shared = compute_shared_sps(engine)
        # stripe.com and aws.com are used by both seeds
        assert shared["stripe.com"]["count"] == 2
        assert shared["aws.com"]["count"] == 2
        # hetzner and datadog are used by 1 seed each
        assert shared["hetzner.com"]["count"] == 1
        assert shared["datadog.com"]["count"] == 1

    def test_shared_sps_pct(self, engine):
        from gdpr_universe.compare import compute_shared_sps

        shared = compute_shared_sps(engine)
        # 2 seed companies total, so stripe used by 100%
        assert shared["stripe.com"]["pct"] == 100

    def test_shared_sps_top_n(self, engine):
        from gdpr_universe.compare import compute_shared_sps

        shared = compute_shared_sps(engine, top_n=2)
        assert len(shared) == 2
        # Top 2 should be stripe and aws (both count=2)
        assert "stripe.com" in shared
        assert "aws.com" in shared


class TestAlternatives:
    """Test common alternatives grouping by category."""

    def test_alternatives_groups_by_category(self, engine):
        from gdpr_universe.compare import compute_alternatives

        alts = compute_alternatives(engine)
        categories = {a["category"] for a in alts}
        # infrastructure has aws + hetzner (different seeds use different ones)
        assert "infrastructure" in categories

    def test_alternatives_vendors_have_used_by(self, engine):
        from gdpr_universe.compare import compute_alternatives

        alts = compute_alternatives(engine)
        infra = next(a for a in alts if a["category"] == "infrastructure")
        for vendor in infra["vendors"]:
            assert "domain" in vendor
            assert "used_by" in vendor
            assert len(vendor["used_by"]) > 0


class TestSectorAverages:
    """Test sector peer benchmarking averages."""

    def test_sector_averages(self, engine):
        from gdpr_universe.compare import compute_sector_averages

        avgs = compute_sector_averages(engine)
        # Two sectors: Manufacturing (acme), Fintech (globex)
        assert "Manufacturing" in avgs
        assert "Fintech" in avgs

    def test_sector_average_values(self, engine):
        from gdpr_universe.compare import compute_sector_averages

        avgs = compute_sector_averages(engine)
        # Manufacturing has only acme (sp_count=3), so avg=3
        assert avgs["Manufacturing"]["avg_sp_count"] == 3


class TestCompositeScore:
    """Test composite score grade boundaries."""

    def test_grade_a(self):
        from gdpr_universe.compare import _score_to_grade
        assert _score_to_grade(75) == "A"
        assert _score_to_grade(100) == "A"

    def test_grade_b(self):
        from gdpr_universe.compare import _score_to_grade
        assert _score_to_grade(50) == "B"
        assert _score_to_grade(74) == "B"

    def test_grade_c(self):
        from gdpr_universe.compare import _score_to_grade
        assert _score_to_grade(25) == "C"
        assert _score_to_grade(49) == "C"

    def test_grade_d(self):
        from gdpr_universe.compare import _score_to_grade
        assert _score_to_grade(0) == "D"
        assert _score_to_grade(24) == "D"
```

- [ ] **Step 2: Run tests to verify new ones fail**

Run: `.venv/bin/pytest tests/unit/test_universe_compare.py -v -k "Shared or Alternatives or Sector or Composite"`
Expected: FAIL with `ImportError` for the new functions

- [ ] **Step 3: Implement cross-company functions**

Append to `gdpr_universe/compare.py`:

```python
def compute_shared_sps(
    engine: Engine,
    *,
    top_n: int = 0,
) -> dict[str, dict]:
    """Compute how many seed companies use each subprocessor.

    Returns: {sp_domain: {"count": int, "pct": int, "name": str}}
    Sorted by count descending.
    """
    with get_session(engine) as session:
        total_seeds = session.execute(
            text("SELECT COUNT(*) FROM companies WHERE is_seed = 1")
        ).scalar() or 1

        rows = session.execute(
            text(
                "SELECT e.child_domain, c.company_name, "
                "       COUNT(DISTINCT e.parent_domain) AS cnt "
                "FROM edges e "
                "JOIN companies c ON e.child_domain = c.domain "
                "JOIN companies p ON e.parent_domain = p.domain AND p.is_seed = 1 "
                "GROUP BY e.child_domain "
                "ORDER BY cnt DESC"
            )
        ).fetchall()

    result: dict[str, dict] = {}
    for row in rows:
        domain, name, count = row
        result[domain] = {
            "count": count,
            "pct": round(count * 100 / total_seeds),
            "name": name or domain,
        }
        if top_n and len(result) >= top_n:
            break
    return result


def compute_alternatives(engine: Engine) -> list[dict]:
    """Group subprocessors by service_category to show alternative vendor choices.

    Returns: [{category, vendors: [{domain, name, used_by: [seed_domains]}]}]
    Only includes categories with 2+ distinct vendors.
    """
    with get_session(engine) as session:
        rows = session.execute(
            text(
                "SELECT c.service_category, e.child_domain, c.company_name, "
                "       e.parent_domain "
                "FROM edges e "
                "JOIN companies c ON e.child_domain = c.domain "
                "JOIN companies p ON e.parent_domain = p.domain AND p.is_seed = 1 "
                "WHERE c.service_category IS NOT NULL AND c.service_category != '' "
                "ORDER BY c.service_category, e.child_domain"
            )
        ).fetchall()

    # Group: category -> vendor_domain -> {name, used_by set}
    cat_vendors: dict[str, dict[str, dict]] = {}
    for row in rows:
        cat, child_domain, child_name, parent_domain = row
        cat_vendors.setdefault(cat, {})
        vendor = cat_vendors[cat].setdefault(child_domain, {
            "domain": child_domain,
            "name": child_name or child_domain,
            "used_by": set(),
        })
        vendor["used_by"].add(parent_domain)

    result: list[dict] = []
    for cat, vendors in sorted(cat_vendors.items()):
        if len(vendors) < 2:
            continue
        vendor_list = [
            {
                "domain": v["domain"],
                "name": v["name"],
                "used_by": sorted(v["used_by"]),
            }
            for v in sorted(vendors.values(), key=lambda x: -len(x["used_by"]))
        ]
        result.append({"category": cat, "vendors": vendor_list})
    return result


def compute_sector_averages(engine: Engine) -> dict[str, dict]:
    """Compute per-sector averages of all per-company metrics.

    Returns: {sector: {avg_sp_count, avg_adequate_pct, avg_basis_pct, ...}}
    """
    rows = compute_company_metrics(engine)

    sector_groups: dict[str, list[dict]] = {}
    for row in rows:
        sector = row["sector"]
        if sector:
            sector_groups.setdefault(sector, []).append(row)

    result: dict[str, dict] = {}
    numeric_fields = [
        "sp_count", "adequate_pct", "risky_pct", "basis_pct",
        "lockin_pct", "xborder_count", "max_depth", "transparency_score",
        "composite_score",
    ]
    for sector, group in sector_groups.items():
        n = len(group)
        avgs: dict[str, int] = {}
        for field in numeric_fields:
            total = sum(r[field] for r in group)
            avgs[f"avg_{field}"] = round(total / n)
        avgs["count"] = n
        result[sector] = avgs
    return result
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_universe_compare.py -v`
Expected: All tests PASS

- [ ] **Step 5: Commit**

```bash
git add gdpr_universe/compare.py tests/unit/test_universe_compare.py
git commit -m "feat(compare): add shared SPs, alternatives, and sector averages"
```

---

### Task 3: Cache Layer and Refresh

**Files:**
- Modify: `tests/unit/test_universe_compare.py`
- Modify: `gdpr_universe/compare.py`

Add `refresh_compare()` that computes all metrics and stores them in `AnalyticsCache`, plus `get_compare_data()` that reads them back.

- [ ] **Step 1: Write failing tests for caching**

Append to `tests/unit/test_universe_compare.py`:

```python
class TestCompareCache:
    """Test caching of comparison metrics."""

    def test_refresh_stores_all_keys(self, engine):
        from gdpr_universe.compare import refresh_compare

        result = refresh_compare(engine)
        assert "compare_matrix" in result["keys_updated"]
        assert "compare_shared_sps" in result["keys_updated"]
        assert "compare_alternatives" in result["keys_updated"]
        assert "compare_sector_averages" in result["keys_updated"]

    def test_get_compare_data_after_refresh(self, engine):
        from gdpr_universe.compare import get_compare_data, refresh_compare

        refresh_compare(engine)
        data = get_compare_data(engine)
        assert data["matrix"] is not None
        assert len(data["matrix"]) == 2  # 2 seed companies
        assert data["shared_sps"] is not None
        assert data["alternatives"] is not None
        assert data["sector_averages"] is not None

    def test_get_compare_data_empty_before_refresh(self, engine):
        from gdpr_universe.compare import get_compare_data

        data = get_compare_data(engine)
        assert data["matrix"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/test_universe_compare.py::TestCompareCache -v`
Expected: FAIL with `ImportError`

- [ ] **Step 3: Implement cache functions**

Append to `gdpr_universe/compare.py`:

```python
def refresh_compare(engine: Engine) -> dict:
    """Recompute all comparison metrics and store in AnalyticsCache.

    Returns {"keys_updated": [list of keys]}.
    """
    now = datetime.now(timezone.utc)
    results: dict[str, object] = {}

    results["compare_matrix"] = compute_company_metrics(engine)
    results["compare_shared_sps"] = compute_shared_sps(engine, top_n=20)
    results["compare_alternatives"] = compute_alternatives(engine)
    results["compare_sector_averages"] = compute_sector_averages(engine)

    with get_session(engine) as session:
        for key, value in results.items():
            json_value = json.dumps(value, default=str)
            existing = session.query(AnalyticsCache).filter(
                AnalyticsCache.key == key
            ).first()
            if existing:
                existing.value = json_value
                existing.computed_at = now
            else:
                session.add(AnalyticsCache(
                    key=key, value=json_value, computed_at=now
                ))

    return {"keys_updated": list(results.keys())}


def get_compare_data(engine: Engine) -> dict:
    """Read all comparison data from cache.

    Returns {"matrix", "shared_sps", "alternatives", "sector_averages"}.
    Values are None if not yet computed.
    """
    keys = [
        "compare_matrix", "compare_shared_sps",
        "compare_alternatives", "compare_sector_averages",
    ]
    data: dict[str, object] = {}
    with get_session(engine) as session:
        for key in keys:
            row = session.query(AnalyticsCache).filter(
                AnalyticsCache.key == key
            ).first()
            short_key = key.replace("compare_", "")
            data[short_key] = json.loads(row.value) if row else None
    return data


def compute_side_by_side(
    engine: Engine,
    domains: list[str],
) -> dict:
    """Compute overlap data for 2-5 selected companies.

    Returns {"companies": [...], "overlap": {...}}.
    """
    if len(domains) < 2 or len(domains) > 5:
        raise ValueError("domains must contain 2-5 entries")

    # Get cached matrix rows for selected domains
    cached = get_compare_data(engine)
    matrix = cached.get("matrix") or compute_company_metrics(engine)
    by_domain = {r["domain"]: r for r in matrix}

    companies: list[dict] = []
    domain_sps: dict[str, set[str]] = {}

    with get_session(engine) as session:
        for d in domains:
            metrics = by_domain.get(d)
            if metrics is None:
                continue

            # Get this company's SP list
            sp_rows = session.execute(
                text(
                    "SELECT child_domain FROM edges WHERE parent_domain = :d"
                ),
                {"d": d},
            ).fetchall()
            sps = {row[0] for row in sp_rows}
            domain_sps[d] = sps

            companies.append({
                "domain": d,
                "company_name": metrics["company_name"],
                "hq_country_code": metrics["hq_country_code"],
                "sector": metrics["sector"],
                "metrics": metrics,
                "grade": metrics["grade"],
                "sps": sorted(sps),
            })

    # Overlap computation
    all_sps = set()
    for sps in domain_sps.values():
        all_sps |= sps

    shared_by_all = set.intersection(*domain_sps.values()) if domain_sps else set()

    shared_by_some: dict[str, list[str]] = {}
    for sp in all_sps - shared_by_all:
        users = [d for d, sps in domain_sps.items() if sp in sps]
        if len(users) > 1:
            shared_by_some[sp] = sorted(users)

    # Combined cross-border percentage
    total_edges = sum(len(sps) for sps in domain_sps.values())
    total_xborder = sum(
        by_domain.get(d, {}).get("xborder_count", 0) for d in domains
    )
    combined_xborder_pct = round(total_xborder * 100 / total_edges) if total_edges else 0

    return {
        "companies": companies,
        "overlap": {
            "shared_by_all": sorted(shared_by_all),
            "shared_by_some": shared_by_some,
            "total_unique": len(all_sps),
            "combined_xborder_pct": combined_xborder_pct,
        },
    }
```

- [ ] **Step 4: Run all tests**

Run: `.venv/bin/pytest tests/unit/test_universe_compare.py -v`
Expected: All PASS

- [ ] **Step 5: Commit**

```bash
git add gdpr_universe/compare.py tests/unit/test_universe_compare.py
git commit -m "feat(compare): add cache layer, refresh, and side-by-side computation"
```

---

### Task 4: Flask Blueprint and Routes

**Files:**
- Create: `gdpr_universe/routes/compare.py`
- Modify: `gdpr_universe/app.py`
- Modify: `tests/unit/test_universe_compare.py`

- [ ] **Step 1: Write failing route tests**

Append to `tests/unit/test_universe_compare.py`:

```python
@pytest.fixture()
def client(engine, tmp_path):
    """Create a Flask test client with the test DB."""
    db_path = str(tmp_path / "test_compare.db")
    app = create_app(db_path)
    app.config["TESTING"] = True
    # Reuse the existing engine's data by pointing at same path
    # (engine fixture already created db at this path)
    with app.test_client() as c:
        yield c


class TestCompareRoutes:
    """Test Flask routes for comparison page."""

    def test_compare_page_renders(self, client):
        resp = client.get("/compare")
        assert resp.status_code == 200
        assert b"Company Benchmarking" in resp.data

    def test_compare_refresh(self, client):
        resp = client.post("/compare/refresh")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "keys_updated" in data

    def test_compare_with_filters(self, client):
        resp = client.get("/compare?country=DE")
        assert resp.status_code == 200

    def test_side_by_side_api(self, client):
        # Need to refresh first so cache exists
        client.post("/compare/refresh")
        resp = client.get("/api/compare/side-by-side?domains=acme.com,globex.com")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "companies" in data
        assert "overlap" in data
        assert len(data["companies"]) == 2

    def test_side_by_side_too_few(self, client):
        resp = client.get("/api/compare/side-by-side?domains=acme.com")
        assert resp.status_code == 400

    def test_side_by_side_too_many(self, client):
        resp = client.get(
            "/api/compare/side-by-side?domains=a.com,b.com,c.com,d.com,e.com,f.com"
        )
        assert resp.status_code == 400
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/test_universe_compare.py::TestCompareRoutes -v`
Expected: FAIL (404 on /compare — route not registered yet)

- [ ] **Step 3: Create the blueprint**

```python
# gdpr_universe/routes/compare.py
"""Compare blueprint — company benchmarking and comparison."""

from __future__ import annotations

from flask import Blueprint, current_app, jsonify, render_template, request
from sqlalchemy.engine import Engine

from gdpr_universe.compare import (
    compute_company_metrics,
    compute_side_by_side,
    get_compare_data,
    refresh_compare,
)
from gdpr_universe.routes.dashboard import _get_filter_options, _get_stats

bp = Blueprint("compare", __name__)


def _get_engine() -> Engine:
    return current_app.config["DB_ENGINE"]


@bp.route("/compare")
def compare_page() -> str:
    """Render the company benchmarking page."""
    engine = _get_engine()
    stats = _get_stats(engine)

    # Filters
    filter_country = request.args.get("country", "").strip()
    filter_sector = request.args.get("sector", "").strip()
    filter_category = request.args.get("category", "").strip()

    # Get cached data (or None if not refreshed yet)
    cached = get_compare_data(engine)
    matrix = cached["matrix"]
    shared_sps = cached["shared_sps"]
    alternatives = cached["alternatives"]
    sector_averages = cached["sector_averages"]

    # If category filter is active, recompute metrics at request time
    if filter_category and matrix is not None:
        matrix = compute_company_metrics(engine, category=filter_category)

    # Apply country/sector filters to matrix rows
    if matrix:
        if filter_country:
            matrix = [r for r in matrix if r["hq_country_code"] == filter_country]
        if filter_sector:
            matrix = [r for r in matrix if r["sector"] == filter_sector]

    has_data = matrix is not None

    # Filter options
    available_countries, available_sectors = _get_filter_options(engine)
    available_categories = _get_categories(engine)

    # Build JSON blob for Chart.js
    compare_data = {
        "matrix": matrix or [],
        "shared_sps": shared_sps or {},
        "alternatives": alternatives or [],
        "sector_averages": sector_averages or {},
    }

    return render_template(
        "compare.html",
        active_tab="compare",
        stats=stats,
        has_data=has_data,
        matrix=matrix or [],
        shared_sps=shared_sps or {},
        alternatives=alternatives or [],
        sector_averages=sector_averages or {},
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
    """Recompute all comparison metrics."""
    engine = _get_engine()
    result = refresh_compare(engine)
    return jsonify(result)


@bp.route("/api/compare/side-by-side")
def side_by_side_api():
    """Return JSON for side-by-side comparison of 2-5 companies."""
    engine = _get_engine()
    domains_param = request.args.get("domains", "")
    domains = [d.strip() for d in domains_param.split(",") if d.strip()]

    if len(domains) < 2 or len(domains) > 5:
        return jsonify({"error": "Provide 2-5 comma-separated domains"}), 400

    try:
        data = compute_side_by_side(engine, domains)
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    return jsonify(data)


def _get_categories(engine: Engine) -> list[str]:
    """Return distinct service categories from SP companies."""
    from gdpr_universe.db import get_session
    from sqlalchemy import text

    with get_session(engine) as session:
        rows = session.execute(
            text(
                "SELECT DISTINCT service_category FROM companies "
                "WHERE service_category IS NOT NULL AND service_category != '' "
                "ORDER BY service_category"
            )
        ).fetchall()
    return [r[0] for r in rows]
```

- [ ] **Step 4: Register blueprint in app.py**

Add to `gdpr_universe/app.py` after the crawl blueprint registration (line 53):

```python
    from gdpr_universe.routes.compare import bp as compare_bp

    app.register_blueprint(compare_bp)
```

- [ ] **Step 5: Create minimal template stub**

Create `gdpr_universe/templates/compare.html` with minimal content so route tests pass:

```html
{% extends "base.html" %}

{% block title %}Compare — GDPR Universe{% endblock %}

{% block content %}
<h4>Company Benchmarking</h4>
{% if not has_data %}
<div class="alert alert-warning py-2">No comparison data cached yet. Click <strong>Refresh</strong> to compute.</div>
{% endif %}
{% endblock %}
```

- [ ] **Step 6: Add "Compare" to navbar in base.html**

In `gdpr_universe/templates/base.html`, add the Compare nav link after the Analytics link (line 16):

```html
            <a class="nav-link-custom {% if active_tab == 'compare' %}active{% endif %}" href="/compare">Compare</a>
```

- [ ] **Step 7: Run route tests**

Run: `.venv/bin/pytest tests/unit/test_universe_compare.py::TestCompareRoutes -v`
Expected: All PASS

- [ ] **Step 8: Run all tests to verify no regressions**

Run: `.venv/bin/pytest tests/unit/test_universe_compare.py -v`
Expected: All PASS

- [ ] **Step 9: Commit**

```bash
git add gdpr_universe/routes/compare.py gdpr_universe/app.py \
        gdpr_universe/templates/compare.html gdpr_universe/templates/base.html \
        tests/unit/test_universe_compare.py
git commit -m "feat(compare): add Flask blueprint, routes, and nav link"
```

---

### Task 5: Full Template (Charts + Matrix + Side-by-Side)

**Files:**
- Modify: `gdpr_universe/templates/compare.html`

This task replaces the stub template with the full page layout including Chart.js canvas elements, the comparison matrix with search/checkboxes, insight panels, and the side-by-side container.

- [ ] **Step 1: Write the full template**

Replace `gdpr_universe/templates/compare.html` with the full implementation. The template is large so it is described structurally — the implementing agent should write the full Jinja template following these sections and the mockups in `.superpowers/brainstorm/`:

**Template structure:**
1. Page header with title "Company Benchmarking" and refresh button (`POST /compare/refresh`)
2. Filter form (GET, 3 selects: country, sector, category) — same pattern as `dashboard.html` lines 12-36
3. Charts Row 1: 4 cards each containing a `<canvas>` element with unique IDs (`chart-sp-count`, `chart-jurisdiction`, `chart-xborder`, `chart-shared-sps`)
4. Charts Row 2: 3 cards (`chart-lockin`, `chart-geo-concentration`, `chart-transparency`)
5. Sector Peer Benchmarking panel: iterate `sector_averages`, for each sector show companies with up/down arrows vs average — use existing `.card` + `.card-header` classes
6. Common Alternatives table: iterate `alternatives`, group by category, show vendor pills with "used by" text — use `.table` + `.table-hover` classes
7. Comparison Matrix: `.card` with search input + "Compare Selected" button in header, `<table>` with checkbox column, heatmap-colored cells using conditional Jinja `{% if row.adequate_pct >= 70 %}bg-success{% elif ... %}` — each `<th>` has an `<span>` tooltip with `title` attribute from the spec's tooltip table
8. Side-by-side container: empty `<div id="side-by-side">` (populated by JS)
9. JSON data blob: `<script id="compare-data" type="application/json">{{ compare_data | tojson }}</script>`
10. Scripts block: Chart.js CDN + `compare.js`

**Styling rules (from `universe.css`):**
- Cards: `.card`, `.card-header` (bg `#fafbfc`)
- Tables: `.table .table-hover .table-sm`
- Column headers: uppercase, `0.72rem`, `#6c757d`, `letter-spacing: 0.04em`
- Cell font: `0.82rem`
- Hover: `#f0f4ff`
- Heatmap badges: green `bg-success` / yellow `bg-warning text-dark` / red `bg-danger`
- Grid layouts use inline `style="display:grid;grid-template-columns:..."` (no CSS file changes)

- [ ] **Step 2: Verify template renders**

Run: `.venv/bin/pytest tests/unit/test_universe_compare.py::TestCompareRoutes::test_compare_page_renders -v`
Expected: PASS

- [ ] **Step 3: Commit**

```bash
git add gdpr_universe/templates/compare.html
git commit -m "feat(compare): full template with charts, matrix, and insight panels"
```

---

### Task 6: Client-Side JavaScript (Chart.js + Interactivity)

**Files:**
- Create: `gdpr_universe/static/js/compare.js`

- [ ] **Step 1: Create `compare.js`**

```javascript
// gdpr_universe/static/js/compare.js
// Company Benchmarking — Chart.js rendering + matrix interactivity

(function () {
  'use strict';

  const dataEl = document.getElementById('compare-data');
  if (!dataEl) return;
  const DATA = JSON.parse(dataEl.textContent);

  // ── Color palette (matches universe.css / Bootstrap) ─────────────
  const C = {
    primary: '#0d6efd',
    purple: '#6f42c1',
    green: '#198754',
    yellow: '#ffc107',
    red: '#dc3545',
    gray: '#6c757d',
    lightGray: '#e9ecef',
  };

  // ── 1. Charts ────────────────────────────────────────────────────

  function initCharts() {
    const matrix = DATA.matrix || [];
    if (matrix.length === 0) return;

    // Sort by sp_count desc for the SP count chart
    const bySp = [...matrix].sort((a, b) => b.sp_count - a.sp_count);
    renderHorizontalBar('chart-sp-count', {
      labels: bySp.map(r => r.company_name),
      data: bySp.map(r => r.sp_count),
      color: C.primary,
      label: 'Subprocessors',
    });

    // Jurisdiction risk — stacked bar
    const byAdequate = [...matrix].sort((a, b) => b.adequate_pct - a.adequate_pct);
    renderStackedBar('chart-jurisdiction', {
      labels: byAdequate.map(r => r.company_name),
      datasets: [
        { label: 'Adequate', data: byAdequate.map(r => r.adequate_pct), color: C.green },
        {
          label: 'Safeguarded',
          data: byAdequate.map(r => 100 - r.adequate_pct - r.risky_pct),
          color: C.yellow,
        },
        { label: 'Risky', data: byAdequate.map(r => r.risky_pct), color: C.red },
      ],
    });

    // Cross-border
    const byXb = [...matrix].sort(
      (a, b) => (b.xborder_count / (b.xborder_total || 1))
              - (a.xborder_count / (a.xborder_total || 1))
    );
    renderStackedBar('chart-xborder', {
      labels: byXb.map(r => r.company_name),
      datasets: [
        {
          label: 'Non-EU',
          data: byXb.map(r => r.xborder_total ? Math.round(r.xborder_count * 100 / r.xborder_total) : 0),
          color: C.red,
        },
        {
          label: 'EU/EEA',
          data: byXb.map(r => r.xborder_total ? Math.round((r.xborder_total - r.xborder_count) * 100 / r.xborder_total) : 0),
          color: C.green,
        },
      ],
    });

    // Most shared SPs
    const shared = DATA.shared_sps || {};
    const sharedEntries = Object.entries(shared).sort((a, b) => b[1].pct - a[1].pct).slice(0, 10);
    if (sharedEntries.length > 0) {
      renderHorizontalBar('chart-shared-sps', {
        labels: sharedEntries.map(e => e[1].name || e[0]),
        data: sharedEntries.map(e => e[1].pct),
        color: C.purple,
        label: '% of seeds',
      });
    }

    // Vendor lock-in
    const byLockin = [...matrix].sort((a, b) => b.lockin_pct - a.lockin_pct);
    renderStackedBar('chart-lockin', {
      labels: byLockin.map(r => r.company_name),
      datasets: [
        { label: 'Unique', data: byLockin.map(r => r.lockin_pct), color: C.red },
        { label: 'Shared', data: byLockin.map(r => 100 - r.lockin_pct), color: C.green },
      ],
    });

    // Geographic concentration
    const byGeo = [...matrix].sort((a, b) => b.geo_top_pct - a.geo_top_pct);
    renderHorizontalBar('chart-geo-concentration', {
      labels: byGeo.map(r => r.company_name + ' (' + r.geo_top_country + ')'),
      data: byGeo.map(r => r.geo_top_pct),
      color: C.primary,
      label: 'Top country %',
    });

    // Transparency
    const byTransp = [...matrix].sort((a, b) => b.transparency_score - a.transparency_score);
    renderHorizontalBar('chart-transparency', {
      labels: byTransp.map(r => r.company_name),
      data: byTransp.map(r => r.transparency_score),
      color: C.green,
      label: 'Score (0-100)',
    });
  }

  function renderHorizontalBar(canvasId, { labels, data, color, label }) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    new Chart(canvas, {
      type: 'bar',
      data: {
        labels: labels,
        datasets: [{ label: label, data: data, backgroundColor: color, borderRadius: 3 }],
      },
      options: {
        indexAxis: 'y',
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { display: false } },
        scales: {
          x: { grid: { display: false }, ticks: { font: { size: 11 } } },
          y: { grid: { display: false }, ticks: { font: { size: 11 } } },
        },
      },
    });
  }

  function renderStackedBar(canvasId, { labels, datasets }) {
    const canvas = document.getElementById(canvasId);
    if (!canvas) return;
    new Chart(canvas, {
      type: 'bar',
      data: {
        labels: labels,
        datasets: datasets.map(ds => ({
          label: ds.label,
          data: ds.data,
          backgroundColor: ds.color,
          borderRadius: 0,
        })),
      },
      options: {
        indexAxis: 'y',
        responsive: true,
        maintainAspectRatio: false,
        plugins: { legend: { position: 'bottom', labels: { boxWidth: 12, font: { size: 10 } } } },
        scales: {
          x: { stacked: true, max: 100, grid: { display: false }, ticks: { font: { size: 10 } } },
          y: { stacked: true, grid: { display: false }, ticks: { font: { size: 11 } } },
        },
      },
    });
  }

  // ── 2. Matrix search ─────────────────────────────────────────────

  function initSearch() {
    const input = document.getElementById('compare-search');
    const tbody = document.getElementById('compare-tbody');
    if (!input || !tbody) return;

    input.addEventListener('input', function () {
      const q = this.value.toLowerCase().trim();
      const rows = tbody.querySelectorAll('tr.compare-row');
      let hiddenSelected = [];

      rows.forEach(row => {
        const name = row.dataset.name || '';
        if (!q || name.toLowerCase().includes(q)) {
          row.style.display = '';
          // Highlight match
          const nameCell = row.querySelector('.company-name');
          if (nameCell && q) {
            const original = nameCell.dataset.original || nameCell.textContent;
            nameCell.dataset.original = original;
            const re = new RegExp('(' + q.replace(/[.*+?^${}()|[\]\\]/g, '\\$&') + ')', 'gi');
            nameCell.innerHTML = original.replace(re, '<mark>$1</mark>');
          } else if (nameCell && nameCell.dataset.original) {
            nameCell.innerHTML = nameCell.dataset.original;
          }
        } else {
          row.style.display = 'none';
          if (row.querySelector('input[type=checkbox]:checked')) {
            hiddenSelected.push(row.dataset.name);
          }
        }
      });

      // Show hidden-selected summary
      const summary = document.getElementById('compare-hidden-selected');
      if (summary) {
        if (hiddenSelected.length > 0) {
          summary.style.display = '';
          summary.querySelector('.names').textContent = hiddenSelected.join(', ');
        } else {
          summary.style.display = 'none';
        }
      }
    });
  }

  // ── 3. Matrix sort ───────────────────────────────────────────────

  function initSort() {
    const headers = document.querySelectorAll('#compare-matrix th[data-sort]');
    const tbody = document.getElementById('compare-tbody');
    if (!headers.length || !tbody) return;

    let currentSort = '';
    let currentDir = 'asc';

    headers.forEach(th => {
      th.style.cursor = 'pointer';
      th.addEventListener('click', function () {
        const key = this.dataset.sort;
        if (currentSort === key) {
          currentDir = currentDir === 'asc' ? 'desc' : 'asc';
        } else {
          currentSort = key;
          currentDir = 'desc';
        }

        const rows = Array.from(tbody.querySelectorAll('tr.compare-row'));
        rows.sort((a, b) => {
          let va = parseFloat(a.dataset[key]) || 0;
          let vb = parseFloat(b.dataset[key]) || 0;
          if (key === 'company_name') {
            va = a.dataset.name || '';
            vb = b.dataset.name || '';
            return currentDir === 'asc'
              ? va.localeCompare(vb)
              : vb.localeCompare(va);
          }
          return currentDir === 'asc' ? va - vb : vb - va;
        });

        rows.forEach(row => tbody.appendChild(row));

        // Update arrow indicators
        headers.forEach(h => {
          const arrow = h.querySelector('.sort-arrow');
          if (arrow) arrow.textContent = '';
        });
        const arrow = this.querySelector('.sort-arrow');
        if (arrow) arrow.textContent = currentDir === 'asc' ? ' \u25B2' : ' \u25BC';
      });
    });
  }

  // ── 4. Checkboxes ────────────────────────────────────────────────

  function initCheckboxes() {
    const tbody = document.getElementById('compare-tbody');
    const btn = document.getElementById('compare-btn');
    if (!tbody || !btn) return;

    const MAX_SELECTED = 5;

    function updateState() {
      const checked = tbody.querySelectorAll('input[type=checkbox]:checked');
      const unchecked = tbody.querySelectorAll('input[type=checkbox]:not(:checked)');
      const count = checked.length;

      btn.querySelector('.count').textContent = count;
      btn.disabled = count < 2;

      // Disable unchecked when at max
      unchecked.forEach(cb => {
        cb.disabled = count >= MAX_SELECTED;
      });

      // Highlight selected rows
      tbody.querySelectorAll('tr.compare-row').forEach(row => {
        const cb = row.querySelector('input[type=checkbox]');
        if (cb && cb.checked) {
          row.style.backgroundColor = '#f0f4ff';
        } else {
          row.style.backgroundColor = '';
        }
      });
    }

    tbody.addEventListener('change', function (e) {
      if (e.target.type === 'checkbox') updateState();
    });

    btn.addEventListener('click', function () {
      const checked = tbody.querySelectorAll('input[type=checkbox]:checked');
      const domains = Array.from(checked).map(cb => cb.closest('tr').dataset.domain);
      if (domains.length < 2) return;

      fetch('/api/compare/side-by-side?domains=' + domains.join(','))
        .then(r => r.json())
        .then(data => renderSideBySide(data))
        .catch(err => console.error('Side-by-side fetch failed:', err));
    });

    updateState();
  }

  // ── 5. Side-by-side rendering ────────────────────────────────────

  function renderSideBySide(data) {
    const container = document.getElementById('side-by-side');
    if (!container) return;

    const companies = data.companies || [];
    const overlap = data.overlap || {};

    // Build all SP sets for shared/unique computation
    const spSets = {};
    companies.forEach(c => { spSets[c.domain] = new Set(c.sps || []); });
    const sharedByAll = new Set(overlap.shared_by_all || []);

    const cols = companies.map(c => {
      const m = c.metrics || {};
      const mySps = new Set(c.sps || []);
      const shared = [...mySps].filter(sp => {
        let count = 0;
        for (const d of Object.keys(spSets)) {
          if (spSets[d].has(sp)) count++;
        }
        return count > 1;
      });
      const unique = [...mySps].filter(sp => !shared.includes(sp));

      return `
        <div class="card" style="flex:1;min-width:0">
          <div class="card-header d-flex align-items-center gap-2" style="padding:8px 12px">
            <img src="https://www.google.com/s2/favicons?domain=${c.domain}&sz=16" width="16" height="16">
            <strong style="font-size:0.82rem">${c.company_name}</strong>
            <span class="text-muted ms-auto" style="font-size:0.68rem">${c.hq_country_code}</span>
            <strong style="color:${gradeColor(c.grade)};font-size:0.85rem">${c.grade}</strong>
          </div>
          <div style="padding:10px 12px;font-size:0.76rem">
            ${metricRow('Subprocessors', m.sp_count)}
            ${metricRow('Adequate %', m.adequate_pct + '%', m.adequate_pct >= 70 ? C.green : m.adequate_pct >= 40 ? '#e67e22' : C.red)}
            ${metricRow('Risky %', m.risky_pct + '%', m.risky_pct <= 10 ? C.green : m.risky_pct <= 20 ? '#e67e22' : C.red)}
            ${metricRow('Basis %', m.basis_pct + '%', m.basis_pct >= 70 ? C.green : m.basis_pct >= 40 ? '#e67e22' : C.red)}
            ${metricRow('Lock-in', m.lockin_pct + '%', m.lockin_pct <= 30 ? C.green : m.lockin_pct <= 50 ? '#e67e22' : C.red)}
            ${metricRow('Cross-border', m.xborder_count + '/' + m.xborder_total)}
            ${metricRow('Max depth', m.max_depth)}
            ${metricRow('Quality', m.quality)}
          </div>
          <div style="background:#f8f9fa;border-top:1px solid #e2e6ea;padding:8px 12px">
            <div style="font-size:0.65rem;font-weight:600;color:#6c757d;text-transform:uppercase;margin-bottom:4px">Shared</div>
            <div style="display:flex;flex-wrap:wrap;gap:3px">
              ${shared.slice(0, 8).map(sp => `<span style="font-size:0.62rem;background:#e8dff5;color:#6610f2;padding:1px 6px;border-radius:3px">${sp}</span>`).join('')}
              ${shared.length > 8 ? `<span style="font-size:0.62rem;color:#6c757d">+${shared.length - 8} more</span>` : ''}
            </div>
            <div style="font-size:0.65rem;font-weight:600;color:#6c757d;text-transform:uppercase;margin-top:5px;margin-bottom:3px">Unique</div>
            <div style="display:flex;flex-wrap:wrap;gap:3px">
              ${unique.slice(0, 5).map(sp => `<span style="font-size:0.62rem;background:#e9ecef;color:#495057;padding:1px 6px;border-radius:3px">${sp}</span>`).join('')}
              ${unique.length > 5 ? `<span style="font-size:0.62rem;color:#6c757d">+${unique.length - 5} more</span>` : ''}
            </div>
          </div>
        </div>`;
    }).join('');

    container.innerHTML = `
      <div style="border:2px solid #0d6efd;border-radius:8px;overflow:hidden;margin-top:16px">
        <div style="background:#0d6efd;padding:8px 16px;display:flex;justify-content:space-between;align-items:center">
          <strong style="font-size:0.85rem;color:white">Side-by-Side: ${companies.length} Companies</strong>
          <button class="btn btn-sm" style="color:white;border:1px solid rgba(255,255,255,0.4);font-size:0.7rem;padding:2px 10px"
                  onclick="document.getElementById('side-by-side').innerHTML=''">Close</button>
        </div>
        <div style="padding:16px;background:#f8f9fc">
          <div style="display:flex;gap:12px;margin-bottom:12px">${cols}</div>
          <div class="card" style="padding:10px 16px">
            <div style="display:flex;gap:24px;align-items:center;font-size:0.78rem">
              <span style="font-size:0.72rem;font-weight:600;color:#6c757d;text-transform:uppercase">SP Overlap</span>
              <span><strong style="color:#6610f2;font-size:1rem">${overlap.shared_by_all ? overlap.shared_by_all.length : 0}</strong> <span style="color:#6c757d">shared by all</span></span>
              <span><strong style="color:#0d6efd;font-size:1rem">${overlap.shared_by_some ? Object.keys(overlap.shared_by_some).length : 0}</strong> <span style="color:#6c757d">shared by 2+</span></span>
              <span><strong style="color:#495057;font-size:1rem">${overlap.total_unique || 0}</strong> <span style="color:#6c757d">total unique</span></span>
              <span><strong style="color:#dc3545;font-size:1rem">${overlap.combined_xborder_pct || 0}%</strong> <span style="color:#6c757d">non-EU exposure</span></span>
            </div>
          </div>
        </div>
      </div>`;

    container.scrollIntoView({ behavior: 'smooth' });
  }

  function metricRow(label, value, color) {
    const style = color ? `color:${color};font-weight:600` : 'font-weight:600';
    return `<div style="display:flex;justify-content:space-between;padding:3px 0;border-bottom:1px solid #f0f1f3">
      <span style="color:#6c757d">${label}</span><span style="${style}">${value}</span></div>`;
  }

  function gradeColor(grade) {
    return { A: C.green, B: '#fd7e14', C: C.red, D: C.gray }[grade] || C.gray;
  }

  // ── Init ─────────────────────────────────────────────────────────

  document.addEventListener('DOMContentLoaded', function () {
    initCharts();
    initSearch();
    initSort();
    initCheckboxes();
  });
})();
```

- [ ] **Step 2: Manually verify in browser**

Run: `.venv/bin/python -m gdpr_universe.app`
Open: `http://localhost:5003/compare`
Click "Refresh" to populate data, verify charts render and matrix is interactive.

- [ ] **Step 3: Commit**

```bash
git add gdpr_universe/static/js/compare.js
git commit -m "feat(compare): add Chart.js rendering and matrix interactivity"
```

---

### Task 7: Run Full Test Suite and Final Verification

**Files:**
- No new files — verification only

- [ ] **Step 1: Run all compare tests**

Run: `.venv/bin/pytest tests/unit/test_universe_compare.py -v`
Expected: All PASS

- [ ] **Step 2: Run all universe tests to check for regressions**

Run: `.venv/bin/pytest tests/unit/test_universe_*.py -v`
Expected: All PASS — no regressions in existing tests

- [ ] **Step 3: Run placeholder check**

Run: `/find-placeholders`
Expected: No new placeholders introduced

- [ ] **Step 4: Manual smoke test**

Run: `.venv/bin/python -m gdpr_universe.app`
1. Navigate to `http://localhost:5003/compare`
2. Click "Refresh" — verify charts and matrix populate
3. Use country/sector/category filters — verify charts and table update
4. Search in matrix — verify filtering and highlighting
5. Sort columns — verify ordering
6. Check 3 companies → click "Compare Selected" — verify side-by-side panel
7. Navigate to Dashboard, Analytics — verify "Compare" tab visible in nav

- [ ] **Step 5: Final commit if any fixes needed**

```bash
git add -u
git commit -m "fix(compare): address issues found during smoke test"
```
