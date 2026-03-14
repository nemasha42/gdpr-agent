"""Orchestrates GDPR contact lookups: cache → datarequests.org → LLM."""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Callable

import requests as http

from contact_resolver import llm_searcher
from contact_resolver.models import (
    CompaniesDB,
    CompanyRecord,
    Contact,
    Flags,
    PostalAddress,
    RequestNotes,
)

_PROJECT_ROOT = Path(__file__).parent.parent
_DEFAULT_DB_PATH = _PROJECT_ROOT / "data" / "companies.json"

_DATAREQUESTS_INDEX_URL = (
    "https://raw.githubusercontent.com/datenanfragen/data/master/companies/_index.json"
)
_DATAREQUESTS_COMPANY_URL = (
    "https://raw.githubusercontent.com/datenanfragen/data/master/companies/{slug}.json"
)

# Number of days before a record is considered stale and re-fetched
_STALENESS_DAYS: dict[str, int] = {
    "datarequests": 180,
    "llm_search": 90,
    "user_manual": 365,
}


# ---------------------------------------------------------------------------
# Public class
# ---------------------------------------------------------------------------


class ContactResolver:
    """Resolve GDPR contact details for a company domain.

    Lookup chain:
    1. Local ``data/companies.json`` cache (skip if stale)
    2. datarequests.org GitHub company database
    3. Anthropic Claude with web search
    4. Return ``None`` if all sources fail or confidence is low

    Args:
        db_path: Path to the companies JSON database.
        http_get: Injectable HTTP GET callable for testing
                  (signature: ``(url: str, **kwargs) → requests.Response``).
        llm_search: Injectable LLM search callable for testing
                    (signature: ``(company_name, domain) → CompanyRecord | None``).
    """

    def __init__(
        self,
        db_path: Path = _DEFAULT_DB_PATH,
        http_get: Callable | None = None,
        llm_search: Callable | None = None,
    ) -> None:
        self._db_path = db_path
        self._http_get = http_get or http.get
        self._llm_search = llm_search or llm_searcher.search_company
        self._index_cache: list[dict] | None = None  # in-memory per-session cache

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def resolve(self, domain: str, company_name: str) -> CompanyRecord | None:
        """Return GDPR contact details for *domain*, or ``None`` if unresolvable.

        Side-effects: successful lookups are persisted to ``db_path``.
        """
        db = self._load_db()

        # 1. Cache
        existing = db.companies.get(domain)
        if existing and not self._is_stale(existing):
            return existing

        # 2. datarequests.org
        record = self._search_datarequests(domain, company_name)
        if record:
            self._upsert(db, domain, record)
            return record

        # 3. LLM
        record = self._llm_search(company_name, domain)
        if record:
            if record.source_confidence == "low":
                return None
            self._upsert(db, domain, record)
            return record

        return None

    # ------------------------------------------------------------------
    # DB persistence
    # ------------------------------------------------------------------

    def _load_db(self) -> CompaniesDB:
        if not self._db_path.exists():
            return CompaniesDB()
        try:
            text = self._db_path.read_text().strip()
            if not text or text in ("{}", ""):
                return CompaniesDB()
            return CompaniesDB.model_validate_json(text)
        except Exception:
            return CompaniesDB()

    def _save_db(self, db: CompaniesDB) -> None:
        db.meta.last_updated = date.today().isoformat()
        db.meta.total_companies = len(db.companies)
        self._db_path.write_text(db.model_dump_json(indent=2))

    def _upsert(self, db: CompaniesDB, domain: str, record: CompanyRecord) -> None:
        db.companies[domain] = record
        self._save_db(db)

    # ------------------------------------------------------------------
    # Staleness
    # ------------------------------------------------------------------

    def _is_stale(self, record: CompanyRecord) -> bool:
        """Return ``True`` if *record* is older than its source's TTL."""
        if not record.last_verified:
            return True
        try:
            last = date.fromisoformat(record.last_verified)
            threshold = _STALENESS_DAYS.get(record.source, 90)
            return (date.today() - last).days > threshold
        except ValueError:
            return True

    # ------------------------------------------------------------------
    # datarequests.org
    # ------------------------------------------------------------------

    def _fetch_datarequests_index(self) -> list[dict]:
        """Fetch (and in-memory cache) the full datarequests company index."""
        if self._index_cache is not None:
            return self._index_cache
        resp = self._http_get(_DATAREQUESTS_INDEX_URL, timeout=15)
        resp.raise_for_status()
        self._index_cache = resp.json()
        return self._index_cache

    def _search_datarequests(
        self, domain: str, company_name: str
    ) -> CompanyRecord | None:
        """Look up *domain* in the datarequests.org database."""
        try:
            index = self._fetch_datarequests_index()
        except Exception:
            return None

        # Find the first entry whose 'runs' list contains our domain
        match: dict | None = None
        for entry in index:
            if domain in entry.get("runs", []):
                match = entry
                break

        if not match:
            return None

        # Fetch the individual company file for authoritative data
        slug = match.get("slug", "")
        if slug:
            try:
                url = _DATAREQUESTS_COMPANY_URL.format(slug=slug)
                resp = self._http_get(url, timeout=15)
                if resp.ok:
                    match = resp.json()
            except Exception:
                pass  # fall back to index data

        return _map_datarequests_entry(match)

    # ------------------------------------------------------------------
    # Direct cache write (used by consumers who have a record already)
    # ------------------------------------------------------------------

    def save(self, domain: str, record: CompanyRecord) -> None:
        """Persist *record* under *domain* — for user_manual entries."""
        db = self._load_db()
        self._upsert(db, domain, record)


# ---------------------------------------------------------------------------
# datarequests.org → CompanyRecord mapping
# ---------------------------------------------------------------------------


def _map_datarequests_entry(entry: dict) -> CompanyRecord:
    """Convert a datarequests.org company dict to a :class:`CompanyRecord`."""
    transport = entry.get("suggested-transport", "email")
    method_map: dict[str, str] = {
        "webform": "portal",
        "email": "email",
        "letter": "postal",
    }
    preferred = method_map.get(transport, "email")

    email: str = entry.get("email", "")
    webform: str = entry.get("webform", "")
    postal = _parse_postal_address(entry.get("address", ""))

    return CompanyRecord(
        company_name=entry.get("name", ""),
        legal_entity_name="",
        source="datarequests",
        source_confidence="high",
        last_verified=date.today().isoformat(),
        contact=Contact(
            dpo_email="",
            privacy_email=email,
            gdpr_portal_url=webform,
            postal_address=postal,
            preferred_method=preferred,  # type: ignore[arg-type]
        ),
        flags=Flags(
            portal_only=(preferred == "portal" and not email),
            email_accepted=bool(email),
            auto_send_possible=False,
        ),
        request_notes=RequestNotes(),
    )


def _parse_postal_address(address_str: str) -> PostalAddress:
    """Best-effort parse of a free-form multiline address string."""
    lines = [ln.strip() for ln in address_str.strip().splitlines() if ln.strip()]
    if not lines:
        return PostalAddress()
    if len(lines) == 1:
        return PostalAddress(line1=lines[0])
    if len(lines) == 2:
        return PostalAddress(line1=lines[0], city=lines[1])
    # 3+ lines: first line = street, second-to-last = city, last = country
    return PostalAddress(
        line1=lines[0],
        city=lines[-2],
        country=lines[-1],
    )
