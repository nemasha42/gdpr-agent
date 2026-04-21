"""Unit tests for gdpr_universe.compare — per-company metric computation."""

from __future__ import annotations

import json
from datetime import datetime, timezone

import pytest

from gdpr_universe.db import (
    Company,
    Edge,
    FetchLog,
    get_engine,
    get_session,
    init_db,
)
from gdpr_universe.compare import (
    _grade,
    _transparency_score,
    compute_company_metrics,
    compute_shared_sps,
    compute_alternatives,
    compute_sector_averages,
)


# ── Fixtures ──────────────────────────────────────────────────────────────────

EU_EEA = {"AT", "BE", "BG", "HR", "CY", "CZ", "DK", "EE", "FI", "FR", "DE",
           "GR", "HU", "IE", "IT", "LV", "LT", "LU", "MT", "NL", "PL", "PT",
           "RO", "SK", "SI", "ES", "SE", "NO", "IS", "LI", "CH"}


def _now() -> datetime:
    return datetime.now(timezone.utc)


@pytest.fixture()
def engine(tmp_path):
    """Isolated SQLite engine with schema created."""
    db_path = str(tmp_path / "compare_test.db")
    eng = get_engine(db_path)
    init_db(eng)
    return eng


@pytest.fixture()
def populated_engine(engine):
    """2 seed companies, 4 SPs, edges, fetch_logs.

    acme.com (DE, Manufacturing):
        → stripe.com  (US, payments,      transfer_basis="SCCs")
        → aws.com     (US, infrastructure, transfer_basis="SCCs")
        → hetzner.com (DE, infrastructure, transfer_basis="adequacy")

    globex.com (NL, Fintech):
        → stripe.com  (US, payments,       transfer_basis="SCCs")
        → aws.com     (US, infrastructure,  transfer_basis="SCCs")
        → datadog.com (US, analytics,       transfer_basis="SCCs")
    """
    with get_session(engine) as session:
        # Seed companies
        session.add(Company(
            domain="acme.com", company_name="Acme Corp",
            hq_country="Germany", hq_country_code="DE",
            sector="Manufacturing", is_seed=True,
        ))
        session.add(Company(
            domain="globex.com", company_name="Globex BV",
            hq_country="Netherlands", hq_country_code="NL",
            sector="Fintech", is_seed=True,
        ))
        # SPs
        session.add(Company(
            domain="stripe.com", company_name="Stripe Inc",
            hq_country="United States", hq_country_code="US",
            service_category="payments", is_seed=False,
        ))
        session.add(Company(
            domain="aws.com", company_name="Amazon Web Services",
            hq_country="United States", hq_country_code="US",
            service_category="infrastructure", is_seed=False,
        ))
        session.add(Company(
            domain="hetzner.com", company_name="Hetzner Online",
            hq_country="Germany", hq_country_code="DE",
            service_category="infrastructure", is_seed=False,
        ))
        session.add(Company(
            domain="datadog.com", company_name="Datadog Inc",
            hq_country="United States", hq_country_code="US",
            service_category="analytics", is_seed=False,
        ))

        # Edges for acme.com
        session.add(Edge(
            parent_domain="acme.com", child_domain="stripe.com",
            depth=0, transfer_basis="SCCs",
            purposes=json.dumps(["payments"]),
            data_categories=json.dumps(["financial"]),
        ))
        session.add(Edge(
            parent_domain="acme.com", child_domain="aws.com",
            depth=0, transfer_basis="SCCs",
            purposes=json.dumps(["hosting"]),
            data_categories=json.dumps(["all data"]),
        ))
        session.add(Edge(
            parent_domain="acme.com", child_domain="hetzner.com",
            depth=0, transfer_basis="adequacy",
            purposes=json.dumps(["hosting"]),
            data_categories=json.dumps(["all data"]),
        ))

        # Edges for globex.com
        session.add(Edge(
            parent_domain="globex.com", child_domain="stripe.com",
            depth=0, transfer_basis="SCCs",
            purposes=json.dumps(["payments"]),
            data_categories=json.dumps(["financial"]),
        ))
        session.add(Edge(
            parent_domain="globex.com", child_domain="aws.com",
            depth=0, transfer_basis="SCCs",
            purposes=json.dumps(["hosting"]),
            data_categories=json.dumps(["all data"]),
        ))
        session.add(Edge(
            parent_domain="globex.com", child_domain="datadog.com",
            depth=0, transfer_basis="SCCs",
            purposes=json.dumps(["analytics"]),
            data_categories=json.dumps(["usage"]),
        ))

        # Fetch logs (latest per seed)
        session.add(FetchLog(
            domain="acme.com",
            fetched_at=_now(),
            source_url="https://acme.com/sub-processors",
            fetch_status="ok",
            sp_count=3,
        ))
        session.add(FetchLog(
            domain="globex.com",
            fetched_at=_now(),
            source_url="https://globex.com/privacy",
            fetch_status="ok",
            sp_count=3,
        ))

    return engine


# ── Tests ─────────────────────────────────────────────────────────────────────


class TestCompanyMetrics:
    """Tests for compute_company_metrics()."""

    def test_compute_company_metrics_returns_all_seeds(self, populated_engine):
        """Result contains one row per seed company."""
        rows = compute_company_metrics(populated_engine)
        domains = {r["domain"] for r in rows}
        assert domains == {"acme.com", "globex.com"}

    def test_sp_count(self, populated_engine):
        """sp_count equals the number of direct child edges per seed."""
        rows = compute_company_metrics(populated_engine)
        by_domain = {r["domain"]: r for r in rows}
        assert by_domain["acme.com"]["sp_count"] == 3
        assert by_domain["globex.com"]["sp_count"] == 3

    def test_category_count(self, populated_engine):
        """category_count equals distinct service categories among SPs."""
        rows = compute_company_metrics(populated_engine)
        by_domain = {r["domain"]: r for r in rows}
        # acme: payments, infrastructure (×2) → 2 distinct
        assert by_domain["acme.com"]["category_count"] == 2
        # globex: payments, infrastructure, analytics → 3 distinct
        assert by_domain["globex.com"]["category_count"] == 3

    def test_adequate_pct(self, populated_engine):
        """adequate_pct = SPs in adequate jurisdictions / sp_count * 100."""
        rows = compute_company_metrics(populated_engine)
        by_domain = {r["domain"]: r for r in rows}
        # acme: stripe(US=adequate), aws(US=adequate), hetzner(DE=adequate) → 3/3 = 100%
        assert by_domain["acme.com"]["adequate_pct"] == pytest.approx(100.0)
        # globex: stripe(US), aws(US), datadog(US) → 3/3 = 100%
        assert by_domain["globex.com"]["adequate_pct"] == pytest.approx(100.0)

    def test_basis_pct(self, populated_engine):
        """basis_pct = SPs with known transfer_basis / sp_count * 100."""
        rows = compute_company_metrics(populated_engine)
        by_domain = {r["domain"]: r for r in rows}
        # acme: all 3 edges have transfer_basis set → 100%
        assert by_domain["acme.com"]["basis_pct"] == pytest.approx(100.0)
        # globex: all 3 edges have transfer_basis set → 100%
        assert by_domain["globex.com"]["basis_pct"] == pytest.approx(100.0)

    def test_lockin_pct(self, populated_engine):
        """lockin_pct = SPs used by only 1 seed / sp_count * 100."""
        rows = compute_company_metrics(populated_engine)
        by_domain = {r["domain"]: r for r in rows}
        # stripe and aws are shared between acme and globex (sharing_count=2, NOT lock-in)
        # hetzner is used only by acme (sharing_count=1, IS lock-in) → 1/3 ≈ 33.33%
        assert by_domain["acme.com"]["lockin_pct"] == pytest.approx(100 / 3, rel=0.01)
        # datadog is used only by globex (sharing_count=1, IS lock-in) → 1/3 ≈ 33.33%
        assert by_domain["globex.com"]["lockin_pct"] == pytest.approx(100 / 3, rel=0.01)

    def test_xborder_count(self, populated_engine):
        """xborder_count = SPs outside EU/EEA."""
        rows = compute_company_metrics(populated_engine)
        by_domain = {r["domain"]: r for r in rows}
        # acme: stripe(US), aws(US) outside EU → 2; hetzner(DE) inside EU → 0
        assert by_domain["acme.com"]["xborder_count"] == 2
        # globex: stripe(US), aws(US), datadog(US) all outside EU → 3
        assert by_domain["globex.com"]["xborder_count"] == 3

    def test_quality(self, populated_engine):
        """quality field reflects _derive_quality() from routes.dashboard."""
        rows = compute_company_metrics(populated_engine)
        by_domain = {r["domain"]: r for r in rows}
        # acme source_url contains /sub-processors → "high"
        assert by_domain["acme.com"]["quality"] == "high"
        # globex source_url contains /privacy → "medium"
        assert by_domain["globex.com"]["quality"] == "medium"

    def test_category_filter(self, populated_engine):
        """category="" returns all SPs; category="payments" restricts to payment SPs."""
        rows_all = compute_company_metrics(populated_engine, category="")
        rows_pay = compute_company_metrics(populated_engine, category="payments")

        # With category="payments", each seed has only stripe.com as an SP
        by_domain = {r["domain"]: r for r in rows_pay}
        assert by_domain["acme.com"]["sp_count"] == 1
        assert by_domain["globex.com"]["sp_count"] == 1

        # Without filter, 3 SPs each
        by_domain_all = {r["domain"]: r for r in rows_all}
        assert by_domain_all["acme.com"]["sp_count"] == 3

    def test_zero_sp_company(self, engine):
        """A seed with no edges returns zeros for all percentage metrics, no ZeroDivisionError."""
        with get_session(engine) as session:
            session.add(Company(
                domain="empty.com", company_name="Empty Corp",
                hq_country="France", hq_country_code="FR",
                sector="Retail", is_seed=True,
            ))

        rows = compute_company_metrics(engine)
        assert len(rows) == 1
        row = rows[0]
        assert row["domain"] == "empty.com"
        assert row["sp_count"] == 0
        assert row["category_count"] == 0
        assert row["adequate_pct"] == 0.0
        assert row["basis_pct"] == 0.0
        assert row["lockin_pct"] == 0.0
        assert row["xborder_count"] == 0
        assert row["quality"] in ("unknown", "low", "medium", "high")
        # Grade must be one of A/B/C/D
        assert row["grade"] in ("A", "B", "C", "D")

    def test_risky_pct(self, engine):
        """risky_pct only counts SPs with known, non-adequate, non-safeguarded countries.

        IN (India) is safeguarded → must NOT count as risky.
        CN (China) is neither adequate nor safeguarded → must count as risky.
        """
        with get_session(engine) as session:
            session.add(Company(
                domain="riskco.com", company_name="Risk Co",
                hq_country="UK", hq_country_code="GB",
                sector="Tech", is_seed=True,
            ))
            # Safeguarded — should NOT raise risky_count
            session.add(Company(
                domain="india-sp.com", company_name="India SP",
                hq_country="India", hq_country_code="IN",
                service_category="analytics", is_seed=False,
            ))
            # Truly risky — should raise risky_count
            session.add(Company(
                domain="china-sp.com", company_name="China SP",
                hq_country="China", hq_country_code="CN",
                service_category="analytics", is_seed=False,
            ))
            # Adequate — should NOT raise risky_count
            session.add(Company(
                domain="us-sp.com", company_name="US SP",
                hq_country="United States", hq_country_code="US",
                service_category="infrastructure", is_seed=False,
            ))
            session.add(Edge(
                parent_domain="riskco.com", child_domain="india-sp.com",
                depth=0, transfer_basis="SCCs",
                purposes=json.dumps(["analytics"]),
                data_categories=json.dumps(["personal"]),
            ))
            session.add(Edge(
                parent_domain="riskco.com", child_domain="china-sp.com",
                depth=0, transfer_basis="SCCs",
                purposes=json.dumps(["analytics"]),
                data_categories=json.dumps(["personal"]),
            ))
            session.add(Edge(
                parent_domain="riskco.com", child_domain="us-sp.com",
                depth=0, transfer_basis="SCCs",
                purposes=json.dumps(["hosting"]),
                data_categories=json.dumps(["personal"]),
            ))

        rows = compute_company_metrics(engine)
        assert len(rows) == 1
        row = rows[0]
        assert row["sp_count"] == 3
        # Only CN is truly risky → 1/3 ≈ 33.3%
        assert row["risky_pct"] == pytest.approx(100 / 3, rel=0.01)

    def test_xborder_total_equals_sp_count(self, populated_engine):
        """xborder_total must equal sp_count (it is the denominator for xborder percentage)."""
        rows = compute_company_metrics(populated_engine)
        for row in rows:
            assert row["xborder_total"] == row["sp_count"], (
                f"{row['domain']}: xborder_total={row['xborder_total']} != sp_count={row['sp_count']}"
            )

    def test_grade_boundaries(self):
        """_grade() returns correct letter at each boundary score."""
        assert _grade(75.0) == "A"
        assert _grade(74.9) == "B"
        assert _grade(50.0) == "B"
        assert _grade(49.9) == "C"
        assert _grade(25.0) == "C"
        assert _grade(24.9) == "D"
        assert _grade(0.0) == "D"

    def test_transparency_score_uses_field_coverage(self):
        """_transparency_score third component is field_coverage_pct, not basis_pct again."""
        # high quality=40pts, basis_pct=100 → +30pts, field_coverage_pct=0 → +0pts = 70
        score_no_coverage = _transparency_score("high", 100.0, 0.0)
        assert score_no_coverage == pytest.approx(70.0)

        # high quality=40pts, basis_pct=100 → +30pts, field_coverage_pct=100 → +30pts = 100 (capped)
        score_full_coverage = _transparency_score("high", 100.0, 100.0)
        assert score_full_coverage == pytest.approx(100.0)

        # medium quality=20pts, basis_pct=0, field_coverage_pct=50 → 20 + 0 + 15 = 35
        score_mixed = _transparency_score("medium", 0.0, 50.0)
        assert score_mixed == pytest.approx(35.0)


class TestSharedSPs:
    """Tests for compute_shared_sps()."""

    def test_shared_sps(self, populated_engine):
        """stripe and aws used by both seeds (count=2); hetzner and datadog used by one (count=1)."""
        result = compute_shared_sps(populated_engine)
        assert result["stripe.com"]["count"] == 2
        assert result["aws.com"]["count"] == 2
        assert result["hetzner.com"]["count"] == 1
        assert result["datadog.com"]["count"] == 1

    def test_shared_sps_pct(self, populated_engine):
        """stripe.com used by 2 of 2 seeds → 100%."""
        result = compute_shared_sps(populated_engine)
        assert result["stripe.com"]["pct"] == 100
        assert result["hetzner.com"]["pct"] == 50

    def test_shared_sps_top_n(self, populated_engine):
        """top_n=2 limits results to the two most-shared SPs (stripe and aws)."""
        result = compute_shared_sps(populated_engine, top_n=2)
        assert len(result) == 2
        # Both returned entries must have count=2 (the top 2)
        for entry in result.values():
            assert entry["count"] == 2

    def test_shared_sps_name(self, populated_engine):
        """Each entry includes the company_name string."""
        result = compute_shared_sps(populated_engine)
        assert result["stripe.com"]["name"] == "Stripe Inc"

    def test_shared_sps_sorted_descending(self, populated_engine):
        """Results are ordered count descending."""
        result = compute_shared_sps(populated_engine)
        counts = [v["count"] for v in result.values()]
        assert counts == sorted(counts, reverse=True)

    def test_shared_sps_top_n_zero_is_unlimited(self, populated_engine):
        """top_n=0 (default) returns all SPs."""
        result = compute_shared_sps(populated_engine, top_n=0)
        assert len(result) == 4


class TestAlternatives:
    """Tests for compute_alternatives()."""

    def test_alternatives_groups_by_category(self, populated_engine):
        """'infrastructure' category is present (aws + hetzner both in infrastructure)."""
        result = compute_alternatives(populated_engine)
        categories = [r["category"] for r in result]
        assert "infrastructure" in categories

    def test_alternatives_excludes_single_vendor_categories(self, populated_engine):
        """Categories with only one distinct vendor are excluded (need 2+ to be alternatives)."""
        result = compute_alternatives(populated_engine)
        for group in result:
            assert len(group["vendors"]) >= 2, (
                f"Category '{group['category']}' has fewer than 2 vendors: {group['vendors']}"
            )

    def test_alternatives_vendors_have_used_by(self, populated_engine):
        """Each vendor entry has 'domain', 'name', and non-empty 'used_by' list."""
        result = compute_alternatives(populated_engine)
        for group in result:
            for vendor in group["vendors"]:
                assert "domain" in vendor
                assert "name" in vendor
                assert "used_by" in vendor
                assert len(vendor["used_by"]) >= 1

    def test_alternatives_infrastructure_vendors(self, populated_engine):
        """The 'infrastructure' group contains aws.com and hetzner.com."""
        result = compute_alternatives(populated_engine)
        infra = next(g for g in result if g["category"] == "infrastructure")
        vendor_domains = {v["domain"] for v in infra["vendors"]}
        assert "aws.com" in vendor_domains
        assert "hetzner.com" in vendor_domains

    def test_alternatives_payments_excluded(self, populated_engine):
        """'payments' has only stripe (1 vendor) → excluded from results."""
        result = compute_alternatives(populated_engine)
        categories = [r["category"] for r in result]
        assert "payments" not in categories

    def test_alternatives_analytics_excluded(self, populated_engine):
        """'analytics' has only datadog (1 vendor) → excluded from results."""
        result = compute_alternatives(populated_engine)
        categories = [r["category"] for r in result]
        assert "analytics" not in categories


class TestSectorAverages:
    """Tests for compute_sector_averages()."""

    def test_sector_averages(self, populated_engine):
        """Both seed sectors (Manufacturing and Fintech) appear in results."""
        result = compute_sector_averages(populated_engine)
        assert "Manufacturing" in result
        assert "Fintech" in result

    def test_sector_average_values(self, populated_engine):
        """Manufacturing (only acme.com with 3 SPs) has avg_sp_count=3."""
        result = compute_sector_averages(populated_engine)
        mfg = result["Manufacturing"]
        assert mfg["avg_sp_count"] == pytest.approx(3.0)
        assert mfg["count"] == 1

    def test_sector_averages_keys(self, populated_engine):
        """Each sector entry has all required metric keys."""
        required_keys = {
            "avg_sp_count", "avg_adequate_pct", "avg_risky_pct",
            "avg_basis_pct", "avg_lockin_pct", "avg_xborder_count",
            "avg_max_depth", "avg_transparency_score", "avg_composite_score",
            "count",
        }
        result = compute_sector_averages(populated_engine)
        for sector, data in result.items():
            assert required_keys.issubset(set(data.keys())), (
                f"Sector '{sector}' missing keys: {required_keys - set(data.keys())}"
            )

    def test_sector_averages_count(self, populated_engine):
        """Each sector count matches number of seed companies in that sector."""
        result = compute_sector_averages(populated_engine)
        # acme.com is the only Manufacturing seed
        assert result["Manufacturing"]["count"] == 1
        # globex.com is the only Fintech seed
        assert result["Fintech"]["count"] == 1


class TestCompositeScore:
    """Tests for _grade() / _score_to_grade() boundary values."""

    def test_grade_a(self):
        """Scores 75 and 100 → grade A."""
        assert _grade(75) == "A"
        assert _grade(100) == "A"

    def test_grade_b(self):
        """Scores 50 and 74 → grade B."""
        assert _grade(50) == "B"
        assert _grade(74) == "B"

    def test_grade_c(self):
        """Scores 25 and 49 → grade C."""
        assert _grade(25) == "C"
        assert _grade(49) == "C"

    def test_grade_d(self):
        """Scores 0 and 24 → grade D."""
        assert _grade(0) == "D"
        assert _grade(24) == "D"
