"""Tests for gdpr_universe.adapters — bridging SubprocessorRecord → SQLite."""

import json
from datetime import datetime, timezone

from sqlalchemy import create_engine

from contact_resolver.models import Subprocessor, SubprocessorRecord
from gdpr_universe.db import Base, Company, Edge, FetchLog, get_session
from gdpr_universe.adapters import store_fetch_result, _merge_json_arrays


def _make_engine():
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    return engine


def _make_record(sps=None, status="ok", error=""):
    return SubprocessorRecord(
        fetched_at="2026-04-05T10:00:00Z",
        source_url="https://example.com/subprocessors",
        subprocessors=sps or [],
        fetch_status=status,
        error_message=error,
    )


def _make_sp(domain, name="Acme Inc", purposes=None, data_categories=None, **kwargs):
    return Subprocessor(
        domain=domain,
        company_name=name,
        hq_country=kwargs.get("hq_country", "US"),
        hq_country_code=kwargs.get("hq_country_code", "US"),
        purposes=purposes or [],
        data_categories=data_categories or [],
        transfer_basis=kwargs.get("transfer_basis", "unknown"),
        source=kwargs.get("source", "scrape_subprocessor_page"),
    )


class TestStoreFetchResult:
    def test_creates_companies_and_edges(self):
        engine = _make_engine()
        parent = "parent.com"
        # Ensure parent company exists (FK requirement)
        with get_session(engine) as s:
            s.add(Company(domain=parent, company_name="Parent Co", is_seed=True))

        record = _make_record(sps=[
            _make_sp("sp1.com", "SP One", purposes=["analytics"], data_categories=["usage"]),
            _make_sp("sp2.com", "SP Two", purposes=["billing"], data_categories=["payment"]),
        ])
        store_fetch_result(engine, parent, record, depth=0)

        with get_session(engine) as s:
            # Companies created
            c1 = s.query(Company).filter_by(domain="sp1.com").one()
            assert c1.company_name == "SP One"
            c2 = s.query(Company).filter_by(domain="sp2.com").one()
            assert c2.company_name == "SP Two"

            # Edges created
            edges = s.query(Edge).filter_by(parent_domain=parent).all()
            assert len(edges) == 2
            domains = {e.child_domain for e in edges}
            assert domains == {"sp1.com", "sp2.com"}

            e1 = s.query(Edge).filter_by(parent_domain=parent, child_domain="sp1.com").one()
            assert json.loads(e1.purposes) == ["analytics"]
            assert json.loads(e1.data_categories) == ["usage"]

            # FetchLog entry
            logs = s.query(FetchLog).all()
            assert len(logs) == 1
            assert logs[0].domain == parent
            assert logs[0].fetch_status == "ok"
            assert logs[0].sp_count == 2

    def test_updates_existing_company(self):
        engine = _make_engine()
        parent = "parent.com"
        with get_session(engine) as s:
            s.add(Company(domain=parent, company_name="Parent Co", is_seed=True))
            s.add(Company(domain="sp1.com", company_name="Old Name", hq_country="UK"))

        record = _make_record(sps=[
            _make_sp("sp1.com", "New Name", hq_country="DE", hq_country_code="DE"),
        ])
        store_fetch_result(engine, parent, record, depth=0)

        with get_session(engine) as s:
            c = s.query(Company).filter_by(domain="sp1.com").one()
            assert c.company_name == "New Name"
            assert c.hq_country == "DE"
            assert c.hq_country_code == "DE"

    def test_error_status(self):
        engine = _make_engine()
        parent = "parent.com"
        with get_session(engine) as s:
            s.add(Company(domain=parent, company_name="Parent Co", is_seed=True))

        record = _make_record(status="error", error="Connection timeout")
        store_fetch_result(engine, parent, record, depth=0)

        with get_session(engine) as s:
            logs = s.query(FetchLog).all()
            assert len(logs) == 1
            assert logs[0].fetch_status == "error"
            assert logs[0].error_message == "Connection timeout"
            assert logs[0].sp_count == 0

            # No companies or edges created
            companies = s.query(Company).filter(Company.domain != parent).all()
            assert len(companies) == 0
            edges = s.query(Edge).all()
            assert len(edges) == 0

    def test_merges_edge_purposes(self):
        engine = _make_engine()
        parent = "parent.com"
        with get_session(engine) as s:
            s.add(Company(domain=parent, company_name="Parent Co", is_seed=True))
            s.add(Company(domain="sp1.com", company_name="SP One"))
            s.add(Edge(
                parent_domain=parent,
                child_domain="sp1.com",
                depth=0,
                purposes=json.dumps(["billing"]),
                data_categories=json.dumps(["payment"]),
                transfer_basis="unknown",
                source="scrape_subprocessor_page",
            ))

        record = _make_record(sps=[
            _make_sp("sp1.com", "SP One", purposes=["payments"], data_categories=["usage"]),
        ])
        store_fetch_result(engine, parent, record, depth=0)

        with get_session(engine) as s:
            edge = s.query(Edge).filter_by(parent_domain=parent, child_domain="sp1.com").one()
            purposes = sorted(json.loads(edge.purposes))
            assert purposes == ["billing", "payments"]
            data_cats = sorted(json.loads(edge.data_categories))
            assert data_cats == ["payment", "usage"]


class TestMergeJsonArrays:
    def test_merge_deduplicates(self):
        result = _merge_json_arrays('["a", "b"]', ["b", "c"])
        assert sorted(json.loads(result)) == ["a", "b", "c"]

    def test_merge_with_empty_existing(self):
        result = _merge_json_arrays("[]", ["x"])
        assert json.loads(result) == ["x"]

    def test_merge_with_none_existing(self):
        result = _merge_json_arrays(None, ["x"])
        assert json.loads(result) == ["x"]
