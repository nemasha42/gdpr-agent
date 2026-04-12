"""Adapter bridging SubprocessorRecord (parent project) → SQLite rows."""

from __future__ import annotations

import json
from datetime import datetime, timezone

from sqlalchemy.engine import Engine

from contact_resolver.models import Subprocessor, SubprocessorRecord
from gdpr_universe.db import Company, Edge, FetchLog, get_session


def _merge_json_arrays(existing_json: str | None, new_list: list[str]) -> str:
    """Merge *new_list* into an existing JSON array string, deduplicating."""
    if existing_json:
        try:
            existing = json.loads(existing_json)
        except (json.JSONDecodeError, TypeError):
            existing = []
    else:
        existing = []
    merged = list(dict.fromkeys(existing + new_list))  # preserves order, deduplicates
    return json.dumps(merged)


def _parse_fetched_at(iso_str: str) -> datetime:
    """Parse an ISO-8601 datetime string, defaulting to UTC now on failure."""
    try:
        dt = datetime.fromisoformat(iso_str.replace("Z", "+00:00"))
        return dt
    except (ValueError, AttributeError):
        return datetime.now(timezone.utc)


def store_fetch_result(
    engine: Engine,
    domain: str,
    record: SubprocessorRecord,
    *,
    depth: int = 0,
) -> None:
    """Bridge a SubprocessorRecord into Company, Edge, and FetchLog rows.

    1. Always writes a FetchLog entry.
    2. If fetch_status != "ok", returns early (log-only).
    3. For each Subprocessor: upsert Company, upsert Edge (merging arrays).
    """
    fetched_at = _parse_fetched_at(record.fetched_at)
    sp_count = len(record.subprocessors) if record.fetch_status == "ok" else 0

    with get_session(engine) as session:
        # 1. Always write FetchLog
        log = FetchLog(
            domain=domain,
            fetched_at=fetched_at,
            source_url=record.source_url,
            fetch_status=record.fetch_status,
            error_message=record.error_message,
            sp_count=sp_count,
        )
        session.add(log)

        # 2. Early return for non-ok
        if record.fetch_status != "ok":
            return

        # 3. Upsert companies and edges
        for sp in record.subprocessors:
            _upsert_company(session, sp)
            _upsert_edge(session, domain, sp, depth)


def _upsert_company(session, sp: Subprocessor) -> None:
    """Create or update a Company row from a Subprocessor."""
    existing = session.query(Company).filter_by(domain=sp.domain).first()
    if existing is None:
        company = Company(
            domain=sp.domain,
            company_name=sp.company_name,
            hq_country=sp.hq_country,
            hq_country_code=sp.hq_country_code,
            service_category=getattr(sp, "service_category", ""),
            is_seed=False,
        )
        session.add(company)
    else:
        if sp.company_name:
            existing.company_name = sp.company_name
        if sp.hq_country:
            existing.hq_country = sp.hq_country
        if sp.hq_country_code:
            existing.hq_country_code = sp.hq_country_code
        cat = getattr(sp, "service_category", "")
        if cat:
            existing.service_category = cat


def _upsert_edge(session, parent_domain: str, sp: Subprocessor, depth: int) -> None:
    """Create or update an Edge row, merging purposes and data_categories."""
    existing = (
        session.query(Edge)
        .filter_by(parent_domain=parent_domain, child_domain=sp.domain)
        .first()
    )
    if existing is None:
        edge = Edge(
            parent_domain=parent_domain,
            child_domain=sp.domain,
            depth=depth,
            purposes=json.dumps(sp.purposes),
            data_categories=json.dumps(sp.data_categories),
            transfer_basis=sp.transfer_basis,
            source=sp.source,
        )
        session.add(edge)
    else:
        existing.purposes = _merge_json_arrays(existing.purposes, sp.purposes)
        existing.data_categories = _merge_json_arrays(
            existing.data_categories, sp.data_categories
        )
        if sp.transfer_basis and sp.transfer_basis != "unknown":
            existing.transfer_basis = sp.transfer_basis
