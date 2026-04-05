from __future__ import annotations

import pytest
from sqlalchemy import text

from gdpr_universe.db import Company, Edge, get_engine, get_session, init_db


@pytest.fixture()
def engine(tmp_path):
    db_path = str(tmp_path / "test.db")
    eng = get_engine(db_path)
    init_db(eng)
    _seed_test_graph(eng)
    return eng


def _seed_test_graph(engine):
    """Seed the test graph:

    vodafone.com (seed, GB) -> stripe.com (US, payments)  -> aws.com (US, infrastructure)
    vodafone.com (seed, GB) -> cloudflare.com (US, infrastructure)
    siemens.com  (seed, DE) -> stripe.com (US, payments)
    siemens.com  (seed, DE) -> salesforce.com (US, crm) -> aws.com (US, infrastructure)
    bmw.com      (seed, DE) -> cloudflare.com (US, infrastructure)
    """
    companies = [
        Company(domain="vodafone.com", company_name="Vodafone", hq_country="United Kingdom", hq_country_code="GB", is_seed=True),
        Company(domain="siemens.com", company_name="Siemens", hq_country="Germany", hq_country_code="DE", is_seed=True),
        Company(domain="bmw.com", company_name="BMW", hq_country="Germany", hq_country_code="DE", is_seed=True),
        Company(domain="stripe.com", company_name="Stripe", hq_country="United States", hq_country_code="US", service_category="payments"),
        Company(domain="cloudflare.com", company_name="Cloudflare", hq_country="United States", hq_country_code="US", service_category="infrastructure"),
        Company(domain="aws.com", company_name="AWS", hq_country="United States", hq_country_code="US", service_category="infrastructure"),
        Company(domain="salesforce.com", company_name="Salesforce", hq_country="United States", hq_country_code="US", service_category="crm"),
    ]
    edges = [
        Edge(parent_domain="vodafone.com", child_domain="stripe.com", depth=0),
        Edge(parent_domain="vodafone.com", child_domain="cloudflare.com", depth=0),
        Edge(parent_domain="siemens.com", child_domain="stripe.com", depth=0),
        Edge(parent_domain="siemens.com", child_domain="salesforce.com", depth=0),
        Edge(parent_domain="bmw.com", child_domain="cloudflare.com", depth=0),
        Edge(parent_domain="stripe.com", child_domain="aws.com", depth=1),
        Edge(parent_domain="salesforce.com", child_domain="aws.com", depth=1),
    ]

    with get_session(engine) as session:
        session.add_all(companies)
    with get_session(engine) as session:
        session.add_all(edges)


# ── 1. sharing_counts ────────────────────────────────────────────


def test_sharing_counts(engine):
    from gdpr_universe.graph_queries import sharing_counts

    result = sharing_counts(engine)
    assert result["stripe.com"] == 2
    assert result["cloudflare.com"] == 2
    assert result["aws.com"] == 2
    assert result["salesforce.com"] == 1


# ── 2. blast_radius ─────────────────────────────────────────────


def test_blast_radius(engine):
    from gdpr_universe.graph_queries import blast_radius

    result = blast_radius(engine, "aws.com")
    domains = {r["domain"] for r in result}
    # aws.com breach affects stripe, salesforce (hop 1), vodafone, siemens (hop 2)
    assert domains == {"stripe.com", "salesforce.com", "vodafone.com", "siemens.com"}
    # aws itself must NOT be in the result
    assert "aws.com" not in domains

    # Check hop distances
    by_domain = {r["domain"]: r["hops"] for r in result}
    assert by_domain["stripe.com"] == 1
    assert by_domain["salesforce.com"] == 1
    assert by_domain["vodafone.com"] == 2
    assert by_domain["siemens.com"] == 2


def test_blast_radius_max_hops(engine):
    from gdpr_universe.graph_queries import blast_radius

    result = blast_radius(engine, "aws.com", max_hops=1)
    domains = {r["domain"] for r in result}
    # With max_hops=1, only direct parents
    assert domains == {"stripe.com", "salesforce.com"}


# ── 3. concentration_risk ────────────────────────────────────────


def test_concentration_risk(engine):
    from gdpr_universe.graph_queries import concentration_risk

    result = concentration_risk(engine, "infrastructure")
    domains = [r["domain"] for r in result]
    assert "cloudflare.com" in domains
    assert "aws.com" in domains
    # Both have 2 parents
    for r in result:
        assert r["user_count"] == 2


# ── 4. neighborhood ─────────────────────────────────────────────


def test_neighborhood(engine):
    from gdpr_universe.graph_queries import neighborhood

    nodes, edges = neighborhood(engine, "stripe.com", hops=1)
    node_domains = {n["domain"] for n in nodes}
    # stripe + its parents (vodafone, siemens) + its child (aws)
    assert node_domains == {"stripe.com", "vodafone.com", "siemens.com", "aws.com"}
    # bmw should NOT appear (only connected to cloudflare)
    assert "bmw.com" not in node_domains
    assert len(edges) == 3  # vodafone->stripe, siemens->stripe, stripe->aws


# ── 5. risky_chains ─────────────────────────────────────────────


def test_risky_chains(engine):
    from gdpr_universe.graph_queries import risky_chains

    result = risky_chains(engine)
    # US is in ADEQUATE_COUNTRIES (DPF), so 0 risky chains
    assert result == []


# ── 6. chain_depth_distribution ──────────────────────────────────


def test_chain_depth_distribution(engine):
    from gdpr_universe.graph_queries import chain_depth_distribution

    result = chain_depth_distribution(engine)
    assert result[0] == 5  # 5 edges at depth 0
    assert result[1] == 2  # 2 edges at depth 1
