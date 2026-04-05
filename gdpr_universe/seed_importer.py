"""Seed importer — load companies from CSV into the universe database."""

from __future__ import annotations

import argparse
import csv
import json
import sys
from pathlib import Path

from sqlalchemy.engine import Engine

from gdpr_universe.db import Company, IndexConstituent, get_engine, get_session, init_db

# ── Known stock indices ──────────────────────────────────────────

KNOWN_INDICES: dict[str, dict] = {
    "ftse350": {
        "label": "FTSE 350",
        "country": "United Kingdom",
        "description": "Top 350 companies on the London Stock Exchange",
    },
    "eurostoxx600": {
        "label": "EURO STOXX 600",
        "country": "Europe",
        "description": "600 large/mid/small-cap companies across 17 European countries",
    },
    "smi": {
        "label": "SMI",
        "country": "Switzerland",
        "description": "20 largest Swiss companies on the SIX Swiss Exchange",
    },
    "omx_nordic40": {
        "label": "OMX Nordic 40",
        "country": "Nordics",
        "description": "40 most-traded stocks across Nordic exchanges",
    },
    "ibex35": {
        "label": "IBEX 35",
        "country": "Spain",
        "description": "35 most liquid stocks on the Bolsa de Madrid",
    },
    "bel20": {
        "label": "BEL 20",
        "country": "Belgium",
        "description": "20 largest companies on Euronext Brussels",
    },
    "aex25": {
        "label": "AEX 25",
        "country": "Netherlands",
        "description": "25 most-traded stocks on Euronext Amsterdam",
    },
    "atx20": {
        "label": "ATX 20",
        "country": "Austria",
        "description": "20 largest stocks on the Vienna Stock Exchange",
    },
}

_DOMAIN_MAP_PATH = Path(__file__).parent / "data" / "domain_map.json"


# ── Helpers ──────────────────────────────────────────────────────


def _load_domain_map() -> dict[str, str]:
    """Load domain_map.json, skipping keys that start with '_'."""
    if not _DOMAIN_MAP_PATH.exists():
        return {}
    with open(_DOMAIN_MAP_PATH) as f:
        raw = json.load(f)
    return {k: v for k, v in raw.items() if not k.startswith("_")}


def list_indices() -> dict[str, dict]:
    """Return the KNOWN_INDICES dict with metadata about available stock indices."""
    return dict(KNOWN_INDICES)


# ── Main import ──────────────────────────────────────────────────


def import_from_csv(
    engine: Engine,
    csv_path: str,
    *,
    default_index: str = "",
) -> dict:
    """Import companies from a CSV file into the database.

    Returns {"imported": int, "skipped": int, "skipped_names": list[str]}
    """
    domain_map = _load_domain_map()
    imported_domains: set[str] = set()
    skipped_names: list[str] = []

    with open(csv_path, newline="") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    with get_session(engine) as session:
        for row in rows:
            company_name = row.get("company_name", "").strip()
            domain = row.get("domain", "").strip().lower()

            # Fallback to domain_map if no domain provided
            if not domain:
                domain = domain_map.get(company_name, "").lower()

            if not domain:
                skipped_names.append(company_name)
                continue

            # Upsert Company
            existing = session.query(Company).filter_by(domain=domain).first()
            if existing is None:
                company = Company(
                    domain=domain,
                    company_name=company_name,
                    hq_country=row.get("country", "").strip() or None,
                    sector=row.get("sector", "").strip() or None,
                    is_seed=True,
                )
                session.add(company)
            else:
                existing.is_seed = True
                if company_name:
                    existing.company_name = company_name
                country = row.get("country", "").strip()
                if country:
                    existing.hq_country = country
                sector = row.get("sector", "").strip()
                if sector:
                    existing.sector = sector

            imported_domains.add(domain)

            # Add IndexConstituent row
            index_name = row.get("index_name", "").strip() or default_index
            if index_name:
                ic_exists = (
                    session.query(IndexConstituent)
                    .filter_by(domain=domain, index_name=index_name)
                    .first()
                )
                if ic_exists is None:
                    market_cap = row.get("market_cap_eur", "").strip() if "market_cap_eur" in row else ""
                    employees = row.get("employees", "").strip() if "employees" in row else ""
                    session.add(IndexConstituent(
                        domain=domain,
                        index_name=index_name,
                        ticker=row.get("ticker", "").strip() or None,
                        market_cap_eur=float(market_cap) if market_cap else None,
                        employees=int(employees) if employees else None,
                        sector=row.get("sector", "").strip() or None,
                    ))

    return {
        "imported": len(imported_domains),
        "skipped": len(skipped_names),
        "skipped_names": skipped_names,
    }


# ── CLI ──────────────────────────────────────────────────────────


def main() -> None:
    parser = argparse.ArgumentParser(description="Seed importer for GDPR Universe")
    parser.add_argument("--csv", metavar="PATH", help="Import companies from CSV")
    parser.add_argument("--list-indices", action="store_true", help="Print available indices")
    parser.add_argument(
        "--db",
        metavar="PATH",
        default="gdpr_universe/data/universe.db",
        help="Database path (default: gdpr_universe/data/universe.db)",
    )
    args = parser.parse_args()

    if args.list_indices:
        indices = list_indices()
        for key, meta in indices.items():
            print(f"  {key:20s}  {meta['label']} — {meta['description']}")
        return

    if args.csv:
        engine = get_engine(args.db)
        init_db(engine)
        result = import_from_csv(engine, args.csv)
        print(f"Imported {result['imported']} companies, skipped {result['skipped']}")
        if result["skipped_names"]:
            print("Skipped names:")
            for name in result["skipped_names"]:
                print(f"  - {name}")
        return

    parser.print_help()


if __name__ == "__main__":
    main()
