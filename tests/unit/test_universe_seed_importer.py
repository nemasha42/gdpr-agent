from __future__ import annotations

import csv
import pytest

from gdpr_universe.db import (
    Company,
    IndexConstituent,
    get_engine,
    get_session,
    init_db,
)
from gdpr_universe.seed_importer import import_from_csv, list_indices


@pytest.fixture()
def engine(tmp_path):
    db_path = str(tmp_path / "test.db")
    eng = get_engine(db_path)
    init_db(eng)
    return eng


def _write_csv(path, rows: list[dict]) -> str:
    """Helper: write a list of dicts as a CSV file and return the path."""
    fieldnames = list(rows[0].keys())
    csv_path = str(path / "seed.csv")
    with open(csv_path, "w", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)
    return csv_path


# ── 1. Basic import: 2 companies ────────────────────────────────


def test_import_from_csv(engine, tmp_path):
    csv_path = _write_csv(tmp_path, [
        {
            "company_name": "Vodafone Group",
            "ticker": "VOD.L",
            "domain": "vodafone.com",
            "sector": "Telecommunications",
            "country": "United Kingdom",
            "index_name": "ftse350",
        },
        {
            "company_name": "SAP SE",
            "ticker": "SAP.DE",
            "domain": "sap.com",
            "sector": "Technology",
            "country": "Germany",
            "index_name": "eurostoxx600",
        },
    ])

    result = import_from_csv(engine, csv_path)

    assert result["imported"] == 2
    assert result["skipped"] == 0
    assert result["skipped_names"] == []

    with get_session(engine) as session:
        companies = session.query(Company).order_by(Company.domain).all()
        assert len(companies) == 2
        assert companies[0].domain == "sap.com"
        assert companies[0].company_name == "SAP SE"
        assert companies[0].is_seed is True

        constituents = session.query(IndexConstituent).all()
        assert len(constituents) == 2


# ── 2. Dedup: same domain in two indices ────────────────────────


def test_import_csv_dedup_same_domain(engine, tmp_path):
    csv_path = _write_csv(tmp_path, [
        {
            "company_name": "Shell plc",
            "ticker": "SHEL.L",
            "domain": "shell.com",
            "sector": "Energy",
            "country": "United Kingdom",
            "index_name": "ftse350",
        },
        {
            "company_name": "Shell plc",
            "ticker": "SHELL.AS",
            "domain": "shell.com",
            "sector": "Energy",
            "country": "Netherlands",
            "index_name": "eurostoxx600",
        },
    ])

    result = import_from_csv(engine, csv_path)

    assert result["imported"] == 1  # unique domains
    assert result["skipped"] == 0

    with get_session(engine) as session:
        companies = session.query(Company).all()
        assert len(companies) == 1
        assert companies[0].domain == "shell.com"

        constituents = session.query(IndexConstituent).all()
        assert len(constituents) == 2
        index_names = {c.index_name for c in constituents}
        assert index_names == {"ftse350", "eurostoxx600"}


# ── 3. Missing domain → skipped ────────────────────────────────


def test_import_csv_missing_domain_skipped(engine, tmp_path):
    csv_path = _write_csv(tmp_path, [
        {
            "company_name": "Mystery Corp",
            "ticker": "???",
            "domain": "",
            "sector": "Unknown",
            "country": "Unknown",
            "index_name": "ftse350",
        },
    ])

    result = import_from_csv(engine, csv_path)

    assert result["imported"] == 0
    assert result["skipped"] == 1
    assert "Mystery Corp" in result["skipped_names"]

    with get_session(engine) as session:
        assert session.query(Company).count() == 0


# ── 4. list_indices returns known indices ───────────────────────


def test_list_indices():
    indices = list_indices()
    assert "ftse350" in indices
    assert "eurostoxx600" in indices
    assert len(indices) >= 8
