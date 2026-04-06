"""Tests for gdpr_universe.graph_builder."""

from __future__ import annotations

import json

import pytest

from gdpr_universe.db import Company, Edge, get_engine, get_session, init_db


# ── Helpers ───────────────────────────────────────────────────────


def _seed(engine, domain, name, country_code=None, is_seed=False, service_category=None):
    with get_session(engine) as session:
        session.add(
            Company(
                domain=domain,
                company_name=name,
                hq_country_code=country_code,
                is_seed=is_seed,
                service_category=service_category,
            )
        )


def _edge(engine, parent, child, transfer_basis=None, purposes=None, depth=0):
    with get_session(engine) as session:
        session.add(
            Edge(
                parent_domain=parent,
                child_domain=child,
                depth=depth,
                purposes=json.dumps(purposes) if purposes else None,
                transfer_basis=transfer_basis,
            )
        )


@pytest.fixture()
def empty_engine(tmp_path):
    eng = get_engine(str(tmp_path / "test.db"))
    init_db(eng)
    return eng


@pytest.fixture()
def simple_engine(tmp_path):
    """vodafone.com (seed, GB) -> stripe.com (US, payments)"""
    eng = get_engine(str(tmp_path / "test.db"))
    init_db(eng)
    _seed(eng, "vodafone.com", "Vodafone", country_code="GB", is_seed=True)
    _seed(eng, "stripe.com", "Stripe", country_code="US", service_category="payments")
    _edge(eng, "vodafone.com", "stripe.com", transfer_basis="SCCs", purposes=["analytics"])
    return eng


@pytest.fixture()
def shared_sp_engine(tmp_path):
    """Two seeds sharing a single SP:
    vodafone.com (GB, seed) -> stripe.com (US)
    siemens.com  (DE, seed) -> stripe.com (US)
    """
    eng = get_engine(str(tmp_path / "test.db"))
    init_db(eng)
    _seed(eng, "vodafone.com", "Vodafone", country_code="GB", is_seed=True)
    _seed(eng, "siemens.com", "Siemens", country_code="DE", is_seed=True)
    _seed(eng, "stripe.com", "Stripe", country_code="US", service_category="payments")
    _edge(eng, "vodafone.com", "stripe.com")
    _edge(eng, "siemens.com", "stripe.com")
    return eng


# ── build_full_graph ─────────────────────────────────────────────


def test_full_graph_empty_db(empty_engine):
    from gdpr_universe.graph_builder import build_full_graph

    result = build_full_graph(empty_engine)
    assert result["nodes"] == []
    assert result["edges"] == []
    assert result["stats"]["total_seeds"] == 0
    assert result["stats"]["total_subprocessors"] == 0


def test_full_graph_seed_with_sp(simple_engine):
    from gdpr_universe.graph_builder import build_full_graph

    result = build_full_graph(simple_engine)

    nodes_by_domain = {n["domain"]: n for n in result["nodes"]}
    assert "vodafone.com" in nodes_by_domain
    assert "stripe.com" in nodes_by_domain

    # Seed node properties
    vodafone = nodes_by_domain["vodafone.com"]
    assert vodafone["type"] == "seed"
    assert vodafone["is_seed"] is True
    assert vodafone["country"] == "GB"
    assert vodafone["risk"] == "adequate"
    assert vodafone["depth"] == 0
    assert vodafone["label"] == "Vodafone"
    assert vodafone["is_center"] is False

    # SP node properties
    stripe = nodes_by_domain["stripe.com"]
    assert stripe["type"] == "subprocessor"
    assert stripe["is_seed"] is False
    assert stripe["country"] == "US"
    assert stripe["risk"] == "adequate"  # US is adequate (DPF)
    assert stripe["service_category"] == "payments"
    assert stripe["label"] == "Stripe"

    # Edge
    assert len(result["edges"]) == 1
    edge = result["edges"][0]
    assert edge["source"] == "vodafone.com"
    assert edge["target"] == "stripe.com"
    assert edge["type"] == "data_flow"
    assert edge["transfer_basis"] == "SCCs"
    assert edge["purposes"] == ["analytics"]
    assert edge["confirmed"] is False
    assert edge["color"] == "#484f58"

    # Stats
    stats = result["stats"]
    assert stats["total_seeds"] == 1
    assert stats["total_subprocessors"] == 1
    assert stats["countries_reached"] == 2  # GB + US
    # Both adequate
    assert stats["non_adequate_count"] == 0
    assert stats["transfer_basis_breakdown"].get("SCCs") == 1


def test_full_graph_shared_sp_sharing_count(shared_sp_engine):
    from gdpr_universe.graph_builder import build_full_graph

    result = build_full_graph(shared_sp_engine)

    nodes_by_domain = {n["domain"]: n for n in result["nodes"]}
    stripe = nodes_by_domain["stripe.com"]
    assert stripe["sharing_count"] == 2

    # Two seed nodes
    stats = result["stats"]
    assert stats["total_seeds"] == 2
    assert stats["total_subprocessors"] == 1


def test_full_graph_risky_sp(tmp_path):
    """SP in China (CN) — not adequate, not safeguarded -> risky."""
    from gdpr_universe.graph_builder import build_full_graph

    eng = get_engine(str(tmp_path / "risky.db"))
    init_db(eng)
    _seed(eng, "vodafone.com", "Vodafone", country_code="GB", is_seed=True)
    _seed(eng, "alibaba.cn", "Alibaba", country_code="CN", service_category="cloud")
    _edge(eng, "vodafone.com", "alibaba.cn")

    result = build_full_graph(eng)
    nodes_by_domain = {n["domain"]: n for n in result["nodes"]}
    alibaba = nodes_by_domain["alibaba.cn"]
    assert alibaba["risk"] == "risky"
    assert result["stats"]["non_adequate_count"] == 1


def test_full_graph_safeguarded_sp(tmp_path):
    """SP in India (IN) — safeguarded."""
    from gdpr_universe.graph_builder import build_full_graph

    eng = get_engine(str(tmp_path / "safeguarded.db"))
    init_db(eng)
    _seed(eng, "vodafone.com", "Vodafone", country_code="GB", is_seed=True)
    _seed(eng, "infosys.com", "Infosys", country_code="IN", service_category="it")
    _edge(eng, "vodafone.com", "infosys.com")

    result = build_full_graph(eng)
    nodes_by_domain = {n["domain"]: n for n in result["nodes"]}
    infosys = nodes_by_domain["infosys.com"]
    assert infosys["risk"] == "safeguarded"
    assert result["stats"]["non_adequate_count"] == 1


def test_full_graph_unknown_country(tmp_path):
    """SP with no country and non-ccTLD -> unknown risk."""
    from gdpr_universe.graph_builder import build_full_graph

    eng = get_engine(str(tmp_path / "unknown.db"))
    init_db(eng)
    _seed(eng, "vodafone.com", "Vodafone", country_code="GB", is_seed=True)
    _seed(eng, "mystery.io", "Mystery Corp", country_code=None)
    _edge(eng, "vodafone.com", "mystery.io")

    result = build_full_graph(eng)
    nodes_by_domain = {n["domain"]: n for n in result["nodes"]}
    mystery = nodes_by_domain["mystery.io"]
    assert mystery["risk"] == "unknown"
    assert mystery["country"] is None


def test_full_graph_cctld_country_inference(tmp_path):
    """SP with no stored country code but .de domain -> infers DE -> adequate."""
    from gdpr_universe.graph_builder import build_full_graph

    eng = get_engine(str(tmp_path / "cctld.db"))
    init_db(eng)
    _seed(eng, "vodafone.com", "Vodafone", country_code="GB", is_seed=True)
    _seed(eng, "sap.de", "SAP", country_code=None)
    _edge(eng, "vodafone.com", "sap.de")

    result = build_full_graph(eng)
    nodes_by_domain = {n["domain"]: n for n in result["nodes"]}
    sap = nodes_by_domain["sap.de"]
    assert sap["country"] == "DE"
    assert sap["risk"] == "adequate"


def test_full_graph_no_duplicate_edges(tmp_path):
    """Edges should not be duplicated in output."""
    from gdpr_universe.graph_builder import build_full_graph

    eng = get_engine(str(tmp_path / "dedup.db"))
    init_db(eng)
    _seed(eng, "seed.com", "Seed", country_code="GB", is_seed=True)
    _seed(eng, "sp.com", "SP", country_code="US")
    _edge(eng, "seed.com", "sp.com")

    result = build_full_graph(eng)
    assert len(result["edges"]) == 1


# ── build_neighborhood_graph ─────────────────────────────────────


def test_neighborhood_empty_db(empty_engine):
    from gdpr_universe.graph_builder import build_neighborhood_graph

    result = build_neighborhood_graph(empty_engine, "nonexistent.com", hops=2)
    assert result["nodes"] == []
    assert result["edges"] == []
    assert result["stats"]["total_seeds"] == 0


def test_neighborhood_marks_center_node(simple_engine):
    from gdpr_universe.graph_builder import build_neighborhood_graph

    result = build_neighborhood_graph(simple_engine, "stripe.com", hops=1)

    nodes_by_domain = {n["domain"]: n for n in result["nodes"]}
    assert nodes_by_domain["stripe.com"]["is_center"] is True
    assert nodes_by_domain["vodafone.com"]["is_center"] is False


def test_neighborhood_correct_nodes_and_edges(simple_engine):
    from gdpr_universe.graph_builder import build_neighborhood_graph

    result = build_neighborhood_graph(simple_engine, "vodafone.com", hops=1)

    node_domains = {n["domain"] for n in result["nodes"]}
    assert "vodafone.com" in node_domains
    assert "stripe.com" in node_domains

    assert len(result["edges"]) == 1
    edge = result["edges"][0]
    assert edge["source"] == "vodafone.com"
    assert edge["target"] == "stripe.com"
    assert edge["type"] == "data_flow"
    assert edge["color"] == "#484f58"


def test_neighborhood_sharing_count(shared_sp_engine):
    from gdpr_universe.graph_builder import build_neighborhood_graph

    # Centered on stripe.com — should see both parents
    result = build_neighborhood_graph(shared_sp_engine, "stripe.com", hops=1)

    nodes_by_domain = {n["domain"]: n for n in result["nodes"]}
    assert nodes_by_domain["stripe.com"]["is_center"] is True
    # sharing_count computed from subgraph: stripe has 2 parents
    assert nodes_by_domain["stripe.com"]["sharing_count"] == 2


def test_neighborhood_node_shape(simple_engine):
    """Every node must have the required keys."""
    from gdpr_universe.graph_builder import build_neighborhood_graph

    result = build_neighborhood_graph(simple_engine, "vodafone.com", hops=2)
    required_keys = {
        "id", "domain", "type", "label", "country", "risk",
        "is_center", "is_seed", "service_category", "sharing_count", "depth",
    }
    for node in result["nodes"]:
        assert required_keys <= set(node.keys()), f"Node missing keys: {node}"


def test_neighborhood_edge_shape(simple_engine):
    """Every edge must have the required keys."""
    from gdpr_universe.graph_builder import build_neighborhood_graph

    result = build_neighborhood_graph(simple_engine, "vodafone.com", hops=2)
    required_keys = {
        "source", "target", "type", "purposes", "transfer_basis",
        "confirmed", "depth", "color",
    }
    for edge in result["edges"]:
        assert required_keys <= set(edge.keys()), f"Edge missing keys: {edge}"
