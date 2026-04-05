from __future__ import annotations

import pytest
from sqlalchemy import inspect, text
from sqlalchemy.exc import IntegrityError

from gdpr_universe.db import (
    Company,
    Edge,
    FetchLog,
    IndexConstituent,
    AnalyticsCache,
    get_engine,
    get_session,
    init_db,
)


@pytest.fixture()
def engine(tmp_path):
    db_path = str(tmp_path / "test.db")
    eng = get_engine(db_path)
    init_db(eng)
    return eng


# ── 1. init_db creates all 5 tables ──────────────────────────────

def test_init_db_creates_all_tables(engine):
    inspector = inspect(engine)
    tables = set(inspector.get_table_names())
    expected = {"companies", "index_constituents", "edges", "fetch_log", "analytics_cache"}
    assert expected.issubset(tables), f"Missing tables: {expected - tables}"


# ── 2. Company insert and query ──────────────────────────────────

def test_company_insert_and_query(engine):
    with get_session(engine) as session:
        session.add(Company(
            domain="example.com",
            company_name="Example Inc.",
            hq_country="Germany",
            hq_country_code="DE",
            sector="Technology",
            service_category="analytics",
            is_seed=True,
        ))

    with get_session(engine) as session:
        company = session.query(Company).filter_by(domain="example.com").one()
        assert company.company_name == "Example Inc."
        assert company.hq_country_code == "DE"
        assert company.is_seed is True
        assert company.created_at is not None


# ── 3. Edge insert and query ─────────────────────────────────────

def test_edge_insert_and_query(engine):
    # Insert two companies first
    with get_session(engine) as session:
        session.add(Company(domain="parent.com", company_name="Parent"))
        session.add(Company(domain="child.com", company_name="Child"))

    with get_session(engine) as session:
        session.add(Edge(
            parent_domain="parent.com",
            child_domain="child.com",
            depth=1,
            purposes='["analytics"]',
            data_categories='["usage"]',
            transfer_basis="SCC",
            confirmed=False,
            source="subprocessor_page",
        ))

    with get_session(engine) as session:
        edge = session.query(Edge).filter_by(
            parent_domain="parent.com",
            child_domain="child.com",
        ).one()
        assert edge.depth == 1
        assert edge.confirmed is False
        assert edge.source == "subprocessor_page"


# ── 4. Duplicate edge raises IntegrityError ──────────────────────

def test_duplicate_edge_raises_integrity_error(engine):
    with get_session(engine) as session:
        session.add(Company(domain="a.com", company_name="A"))
        session.add(Company(domain="b.com", company_name="B"))

    with get_session(engine) as session:
        session.add(Edge(parent_domain="a.com", child_domain="b.com", depth=1))

    with pytest.raises(IntegrityError):
        with get_session(engine) as session:
            session.add(Edge(parent_domain="a.com", child_domain="b.com", depth=1))


# ── 5. Foreign key pragma is enabled ─────────────────────────────

def test_foreign_keys_enabled(engine):
    with engine.connect() as conn:
        result = conn.execute(text("PRAGMA foreign_keys")).scalar()
        assert result == 1


# ── 6. Indexes exist ─────────────────────────────────────────────

def test_indexes_created(engine):
    inspector = inspect(engine)
    all_indexes: set[str] = set()
    for table_name in inspector.get_table_names():
        for idx in inspector.get_indexes(table_name):
            all_indexes.add(idx["name"])

    expected_indexes = {
        "idx_edges_child",
        "idx_edges_depth",
        "idx_companies_seed",
        "idx_companies_category",
        "idx_fetch_log_domain",
        "idx_fetch_log_status",
    }
    assert expected_indexes.issubset(all_indexes), f"Missing indexes: {expected_indexes - all_indexes}"
