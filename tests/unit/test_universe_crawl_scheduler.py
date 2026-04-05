"""Tests for gdpr_universe.crawl_scheduler — wave-based crawl scheduling."""

from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock

from sqlalchemy import create_engine

from contact_resolver.models import Subprocessor, SubprocessorRecord
from gdpr_universe.db import Base, Company, Edge, FetchLog, get_session
from gdpr_universe.crawl_scheduler import collect_domains_for_wave, run_wave


def _make_engine():
    engine = create_engine("sqlite:///:memory:", echo=False)
    Base.metadata.create_all(engine)
    return engine


def _seed(engine, domain, name="Co"):
    with get_session(engine) as s:
        s.add(Company(domain=domain, company_name=name, is_seed=True))


def _non_seed(engine, domain, name="Co"):
    with get_session(engine) as s:
        s.add(Company(domain=domain, company_name=name, is_seed=False))


def _add_fetch_log(engine, domain, status="ok", age_days=0):
    with get_session(engine) as s:
        s.add(FetchLog(
            domain=domain,
            fetched_at=datetime.now(timezone.utc) - timedelta(days=age_days),
            fetch_status=status,
            sp_count=0,
        ))


def _add_edge(engine, parent, child, depth=0):
    with get_session(engine) as s:
        s.add(Edge(
            parent_domain=parent,
            child_domain=child,
            depth=depth,
            purposes="[]",
            data_categories="[]",
        ))


def _make_record(sps=None, status="ok"):
    return SubprocessorRecord(
        fetched_at=datetime.now(timezone.utc).isoformat(),
        source_url="https://example.com/sp",
        subprocessors=sps or [],
        fetch_status=status,
    )


class TestCollectWave0Domains:
    def test_collect_wave0_domains(self):
        """3 seeds, no fetch log -> returns all 3."""
        engine = _make_engine()
        _seed(engine, "alpha.com")
        _seed(engine, "beta.com")
        _seed(engine, "gamma.com")

        result = collect_domains_for_wave(engine, wave=0, ttl_days=30)
        assert result == ["alpha.com", "beta.com", "gamma.com"]

    def test_collect_wave0_skips_fetched(self):
        """2 seeds, one with fresh 'ok' log -> returns only unfetched."""
        engine = _make_engine()
        _seed(engine, "alpha.com")
        _seed(engine, "beta.com")
        _add_fetch_log(engine, "alpha.com", status="ok", age_days=5)

        result = collect_domains_for_wave(engine, wave=0, ttl_days=30)
        assert result == ["beta.com"]

    def test_collect_wave0_retries_errors(self):
        """1 seed with 'error' log -> returns it."""
        engine = _make_engine()
        _seed(engine, "alpha.com")
        _add_fetch_log(engine, "alpha.com", status="error", age_days=1)

        result = collect_domains_for_wave(engine, wave=0, ttl_days=30)
        assert result == ["alpha.com"]


class TestCollectWave1Domains:
    def test_collect_wave1_domains(self):
        """1 seed fetched, 2 SPs discovered -> wave 1 returns the 2 SPs."""
        engine = _make_engine()
        _seed(engine, "parent.com")
        _add_fetch_log(engine, "parent.com", status="ok", age_days=5)
        # SPs discovered as children at depth 0
        _non_seed(engine, "sp-a.com")
        _non_seed(engine, "sp-b.com")
        _add_edge(engine, "parent.com", "sp-a.com", depth=0)
        _add_edge(engine, "parent.com", "sp-b.com", depth=0)

        result = collect_domains_for_wave(engine, wave=1, ttl_days=30)
        assert result == ["sp-a.com", "sp-b.com"]


class TestRunWave:
    @patch("gdpr_universe.crawl_scheduler._do_fetch")
    def test_run_wave_calls_fetch(self, mock_fetch):
        """Mock _do_fetch, run wave 0 with 2 seeds -> fetch called twice, stats correct."""
        engine = _make_engine()
        _seed(engine, "alpha.com")
        _seed(engine, "beta.com")

        mock_fetch.return_value = _make_record(sps=[], status="ok")

        stats = run_wave(engine, wave=0, max_llm=500, delay=0, ttl_days=30)

        assert mock_fetch.call_count == 2
        assert stats["fetched"] == 2
        assert stats["errors"] == 0
        assert stats["total"] == 2
