"""Integration smoke test — seed import → mock crawl → dashboard routes."""

import csv
import json
import os
import tempfile

import pytest

from gdpr_universe.app import create_app
from gdpr_universe.db import get_engine, init_db, get_session, Company, Edge
from gdpr_universe.seed_importer import import_from_csv
from gdpr_universe.adapters import store_fetch_result
from gdpr_universe.analytics import refresh_analytics
from contact_resolver.models import Subprocessor, SubprocessorRecord
from datetime import datetime, timezone


def _make_sp(domain: str, name: str, country: str, category: str) -> Subprocessor:
    return Subprocessor(
        domain=domain,
        company_name=name,
        hq_country=country,
        hq_country_code="US",
        purposes=["data processing"],
        data_categories=["personal data"],
        transfer_basis="SCCs",
        source="scrape_subprocessor_page",
    )


def _make_record(sps: list[Subprocessor]) -> SubprocessorRecord:
    return SubprocessorRecord(
        fetched_at=datetime.now(timezone.utc).isoformat(),
        source_url="https://example.com/subprocessors",
        subprocessors=sps,
        fetch_status="ok",
    )


@pytest.fixture()
def app_and_client(tmp_path):
    """Create a temp DB, import seeds, simulate crawl, refresh analytics."""
    db_path = str(tmp_path / "test_universe.db")

    # 1. Create app with temp DB
    app = create_app(db_path)
    app.config["TESTING"] = True
    engine = app.config["DB_ENGINE"]

    # 2. Write seed CSV
    csv_path = str(tmp_path / "seeds.csv")
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=["company_name", "domain", "country", "index_name"])
        writer.writeheader()
        writer.writerow({
            "company_name": "Vodafone",
            "domain": "vodafone.com",
            "country": "GB",
            "index_name": "ftse350",
        })
        writer.writerow({
            "company_name": "Siemens",
            "domain": "siemens.com",
            "country": "DE",
            "index_name": "eurostoxx600",
        })

    # 3. Import seeds
    result = import_from_csv(engine, csv_path)
    assert result["imported"] == 2

    # 4. Simulate crawl results — both companies get stripe.com and aws.com
    stripe_sp = _make_sp("stripe.com", "Stripe", "US", "payments")
    aws_sp = _make_sp("aws.com", "Amazon Web Services", "US", "infrastructure")

    for domain in ("vodafone.com", "siemens.com"):
        record = _make_record([stripe_sp, aws_sp])
        store_fetch_result(engine, domain, record, depth=0)

    # Set service_category directly (Subprocessor model doesn't carry this field)
    with get_session(engine) as session:
        stripe_co = session.query(Company).filter_by(domain="stripe.com").first()
        if stripe_co:
            stripe_co.service_category = "payments"
        aws_co = session.query(Company).filter_by(domain="aws.com").first()
        if aws_co:
            aws_co.service_category = "infrastructure"

    # 5. Refresh analytics cache
    refresh_analytics(engine)

    # 6. Return app and test client
    with app.test_client() as client:
        yield app, client


class TestUniverseIntegration:
    """Integration smoke tests for the GDPR Universe dashboard."""

    def test_dashboard_renders(self, app_and_client):
        """GET / returns 200 and lists both seed companies."""
        _, client = app_and_client
        resp = client.get("/")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Vodafone" in html
        assert "Siemens" in html

    def test_company_detail_renders(self, app_and_client):
        """GET /company/vodafone.com returns 200 with both SPs listed."""
        _, client = app_and_client
        resp = client.get("/company/vodafone.com")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "stripe.com" in html
        assert "aws.com" in html

    def test_company_404(self, app_and_client):
        """GET /company/nonexistent.com returns 404."""
        _, client = app_and_client
        resp = client.get("/company/nonexistent.com")
        assert resp.status_code == 404

    def test_graph_api(self, app_and_client):
        """GET /api/graph returns JSON with nodes and edges."""
        _, client = app_and_client
        resp = client.get("/api/graph?domain=vodafone.com&hops=1")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "nodes" in data
        assert "edges" in data
        node_ids = {n["id"] for n in data["nodes"]}
        assert "vodafone.com" in node_ids
        assert "stripe.com" in node_ids

    def test_contagion_renders(self, app_and_client):
        """GET /contagion/stripe.com shows both affected seed companies."""
        _, client = app_and_client
        resp = client.get("/contagion/stripe.com")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "Vodafone" in html
        assert "Siemens" in html

    def test_analytics_renders(self, app_and_client):
        """GET /analytics returns 200 and contains top SP domain."""
        _, client = app_and_client
        resp = client.get("/analytics")
        assert resp.status_code == 200
        html = resp.data.decode()
        assert "stripe.com" in html

    def test_crawl_status(self, app_and_client):
        """GET /crawl/status returns JSON with running field and correct total_fetched."""
        _, client = app_and_client
        resp = client.get("/crawl/status")
        assert resp.status_code == 200
        data = resp.get_json()
        assert "running" in data
        assert data["total_fetched"] == 2
