"""Orchestrates GDPR contact lookups: cache → dataowners → datarequests → scraper → LLM."""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Callable

import requests as http

from contact_resolver import llm_searcher, privacy_page_scraper
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
_DEFAULT_DATAOWNERS_PATH = _PROJECT_ROOT / "data" / "dataowners_overrides.json"

# GitHub API endpoint for the datarequests.org companies directory
_GITHUB_API_DIR_URL = (
    "https://api.github.com/repos/datenanfragen/data/contents/companies"
)

# Number of days before a record is considered stale and re-fetched
_STALENESS_DAYS: dict[str, int] = {
    "datarequests": 180,
    "dataowners_override": 180,
    "privacy_scrape": 90,
    "llm_search": 90,
    "user_manual": 365,  # kept for backward-compatibility with existing DB entries
}

# Cap on how many candidate files to fetch per lookup (avoid runaway network calls)
_MAX_CANDIDATE_FETCHES = 5


# ---------------------------------------------------------------------------
# Public class
# ---------------------------------------------------------------------------


class ContactResolver:
    """Resolve GDPR contact details for a company domain.

    Lookup chain (in order):
    1. Local ``data/companies.json`` cache — returned immediately if fresh
    2. ``data/dataowners_overrides.json`` — curated records for major services
    3. datarequests.org via GitHub API — broad open-source database
    4. Privacy page scraper — lightweight HTML scrape of known privacy URLs
    5. Anthropic Claude with web search — last resort, most expensive
    6. Return ``None`` and flag for manual entry

    Args:
        db_path: Path to the local companies JSON cache.
        dataowners_path: Path to the hand-curated dataowners overrides file.
        http_get: Injectable HTTP GET callable for network calls.
                  (signature: ``(url: str, **kwargs) → requests.Response``).
        privacy_scrape: Injectable privacy-page scraper callable for testing.
                        (signature: ``(domain, company_name, *, verbose) → CompanyRecord | None``).
        llm_search: Injectable LLM search callable for testing.
                    (signature: ``(company_name, domain) → CompanyRecord | None``).
    """

    def __init__(
        self,
        db_path: Path = _DEFAULT_DB_PATH,
        dataowners_path: Path = _DEFAULT_DATAOWNERS_PATH,
        http_get: Callable | None = None,
        privacy_scrape: Callable | None = None,
        llm_search: Callable | None = None,
    ) -> None:
        self._db_path = db_path
        self._dataowners_path = dataowners_path
        self._http_get = http_get or http.get
        self._privacy_scrape = privacy_scrape or privacy_page_scraper.scrape_privacy_page
        self._llm_search = llm_search or llm_searcher.search_company
        # Cached GitHub API directory listing for the current session
        self._dir_listing: list[dict] | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def resolve(
        self,
        domain: str,
        company_name: str,
        *,
        verbose: bool = False,
    ) -> CompanyRecord | None:
        """Return GDPR contact details for *domain*, or ``None`` if unresolvable.

        Side-effects: successful lookups are persisted to the local DB.

        Args:
            domain: Registrable domain, e.g. ``"spotify.com"``.
            company_name: Human-readable name, e.g. ``"Spotify"``.
            verbose: Print step-by-step progress to stdout.
        """
        db = self._load_db()

        # ── Step 1: Local cache ────────────────────────────────────────
        existing = db.companies.get(domain)
        if existing and not self._is_stale(existing):
            if verbose:
                print(
                    f"[CACHE HIT] {domain} — found, fresh"
                    f" (source: {existing.source})"
                )
            return existing
        if verbose:
            if existing:
                print(f"[CACHE MISS] {domain} — stale, re-fetching")
            else:
                print(f"[CACHE MISS] {domain} — not in local DB")

        # ── Step 2: dataowners overrides ──────────────────────────────
        record = self._search_dataowners(domain)
        if record:
            if verbose:
                print(f"[DATAOWNERS] {domain} — found")
            self._upsert(db, domain, record)
            return record
        if verbose:
            print(f"[DATAOWNERS] {domain} — not found")

        # ── Step 3: datarequests.org via GitHub ───────────────────────
        record = self._search_datarequests(domain, company_name)
        if record:
            if verbose:
                print(f"[DATAREQUESTS] {domain} — found as {record.company_name}")
            self._upsert(db, domain, record)
            return record
        if verbose:
            print(f"[DATAREQUESTS] {domain} — not found")

        # ── Step 4: Privacy page scraper ──────────────────────────────
        record = self._privacy_scrape(domain, company_name, verbose=verbose)
        if record:
            self._upsert(db, domain, record)
            return record

        # ── Step 5: LLM web search ────────────────────────────────────
        if verbose:
            print(f"[LLM] searching for {company_name}...", end=" ", flush=True)
        record = self._llm_search(company_name, domain)
        if record:
            if record.source_confidence == "low":
                if verbose:
                    print("confidence: low — skipping")
                return None
            if verbose:
                print(f"confidence: {record.source_confidence}")
            self._upsert(db, domain, record)
            return record
        if verbose:
            print("no result")

        # ── Step 6: Give up ───────────────────────────────────────────
        if verbose:
            print(f"[NOT FOUND] {domain} — flagged for manual entry")
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

    def save(self, domain: str, record: CompanyRecord) -> None:
        """Persist *record* under *domain* — for user_manual entries."""
        db = self._load_db()
        self._upsert(db, domain, record)

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
    # Step 2 — dataowners overrides
    # ------------------------------------------------------------------

    def _search_dataowners(self, domain: str) -> CompanyRecord | None:
        """Look up *domain* in the hand-curated dataowners overrides file."""
        if not self._dataowners_path.exists():
            return None
        try:
            data: dict = json.loads(self._dataowners_path.read_text())
            entry = data.get(domain)
            if not entry:
                return None
            return CompanyRecord.model_validate(entry)
        except Exception:
            return None

    # ------------------------------------------------------------------
    # Step 3 — datarequests.org
    # ------------------------------------------------------------------

    def _fetch_dir_listing(self) -> list[dict]:
        """Fetch (and in-memory cache) the GitHub API directory listing."""
        if self._dir_listing is not None:
            return self._dir_listing
        resp = self._http_get(_GITHUB_API_DIR_URL, timeout=15)
        resp.raise_for_status()
        self._dir_listing = resp.json()
        return self._dir_listing

    def _search_datarequests(
        self, domain: str, company_name: str
    ) -> CompanyRecord | None:
        """Search datarequests.org for *domain* via GitHub file listing."""
        try:
            file_listing = self._fetch_dir_listing()
        except Exception:
            return None

        candidates = _find_candidate_files(file_listing, domain, company_name)

        for file_info in candidates:
            download_url: str = file_info.get("download_url", "")
            if not download_url:
                continue
            try:
                resp = self._http_get(download_url, timeout=15)
                if not resp.ok:
                    continue
                entry: dict = resp.json()
                if domain in entry.get("runs", []):
                    return _map_datarequests_entry(entry)
            except Exception:
                continue

        return None


# ---------------------------------------------------------------------------
# datarequests.org helpers
# ---------------------------------------------------------------------------


def _find_candidate_files(
    file_listing: list[dict],
    domain: str,
    company_name: str,
) -> list[dict]:
    """Return files from *file_listing* that might belong to *domain*/*company_name*.

    Matches by checking whether the file's slug (filename without ``.json``)
    contains the domain's second-level label or any word from the company name.
    """
    domain_root = domain.split(".")[0].lower()
    # Split company name into words with ≥3 chars to avoid noise
    company_words = [
        w for w in re.split(r"[^a-z0-9]+", company_name.lower()) if len(w) >= 3
    ]

    matches: list[dict] = []
    for file_info in file_listing:
        name: str = file_info.get("name", "")
        if not name.endswith(".json"):
            continue
        slug = name[:-5].lower()
        if domain_root in slug or any(word in slug for word in company_words):
            matches.append(file_info)

    return matches[:_MAX_CANDIDATE_FETCHES]


def _map_datarequests_entry(entry: dict) -> CompanyRecord:
    """Convert a datarequests.org company JSON to a :class:`CompanyRecord`."""
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
    # 3+ lines: first = street, second-to-last = city, last = country
    return PostalAddress(
        line1=lines[0],
        city=lines[-2],
        country=lines[-1],
    )
