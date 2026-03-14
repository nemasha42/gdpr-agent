"""Unit tests for contact_resolver/resolver.py."""

import json
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from contact_resolver.models import (
    CompanyRecord,
    CompaniesDB,
    Contact,
    DBMeta,
    Flags,
    PostalAddress,
    RequestNotes,
)
from contact_resolver.resolver import (
    ContactResolver,
    _map_datarequests_entry,
    _parse_postal_address,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

TODAY = date.today().isoformat()


def _fresh_record(source: str = "datarequests") -> CompanyRecord:
    """A non-stale record verified today."""
    return CompanyRecord(
        company_name="Spotify",
        source=source,  # type: ignore[arg-type]
        source_confidence="high",
        last_verified=TODAY,
        contact=Contact(privacy_email="privacy@spotify.com"),
    )


def _stale_record(source: str = "datarequests", days_old: int = 200) -> CompanyRecord:
    old_date = (date.today() - timedelta(days=days_old)).isoformat()
    return CompanyRecord(
        company_name="Spotify",
        source=source,  # type: ignore[arg-type]
        source_confidence="high",
        last_verified=old_date,
        contact=Contact(privacy_email="privacy@spotify.com"),
    )


def _make_resolver(
    tmp_path: Path,
    *,
    http_get: MagicMock | None = None,
    llm_search: MagicMock | None = None,
) -> ContactResolver:
    return ContactResolver(
        db_path=tmp_path / "companies.json",
        http_get=http_get or MagicMock(),
        llm_search=llm_search or MagicMock(return_value=None),
    )


def _seed_db(resolver: ContactResolver, domain: str, record: CompanyRecord) -> None:
    """Write a record directly into the resolver's database file."""
    db = CompaniesDB(companies={domain: record})
    resolver._db_path.write_text(db.model_dump_json(indent=2))


# ---------------------------------------------------------------------------
# _parse_postal_address
# ---------------------------------------------------------------------------


def test_parse_postal_empty_string() -> None:
    addr = _parse_postal_address("")
    assert addr == PostalAddress()


def test_parse_postal_single_line() -> None:
    addr = _parse_postal_address("123 Main St")
    assert addr.line1 == "123 Main St"
    assert addr.city == ""


def test_parse_postal_two_lines() -> None:
    addr = _parse_postal_address("123 Main St\nLondon")
    assert addr.line1 == "123 Main St"
    assert addr.city == "London"


def test_parse_postal_three_lines() -> None:
    addr = _parse_postal_address("123 Main St\nLondon\nUnited Kingdom")
    assert addr.line1 == "123 Main St"
    assert addr.city == "London"
    assert addr.country == "United Kingdom"


def test_parse_postal_four_lines_uses_last_two_for_city_country() -> None:
    addr = _parse_postal_address("Acme Corp\n1 Street\nBerlin\nGermany")
    assert addr.line1 == "Acme Corp"
    assert addr.city == "Berlin"
    assert addr.country == "Germany"


# ---------------------------------------------------------------------------
# _map_datarequests_entry
# ---------------------------------------------------------------------------

_DR_ENTRY: dict = {
    "name": "Spotify",
    "slug": "spotify",
    "runs": ["spotify.com", "spotify.de"],
    "email": "privacy@spotify.com",
    "webform": "",
    "suggested-transport": "email",
    "address": "Spotify AB\nBirger Jarlsgatan 61\nStockholm\nSweden",
}


def test_map_datarequests_email_transport() -> None:
    record = _map_datarequests_entry(_DR_ENTRY)
    assert record.source == "datarequests"
    assert record.source_confidence == "high"
    assert record.contact.privacy_email == "privacy@spotify.com"
    assert record.contact.preferred_method == "email"
    assert record.flags.email_accepted is True


def test_map_datarequests_webform_transport() -> None:
    entry = {**_DR_ENTRY, "suggested-transport": "webform", "email": "",
             "webform": "https://spotify.com/sar"}
    record = _map_datarequests_entry(entry)
    assert record.contact.preferred_method == "portal"
    assert record.contact.gdpr_portal_url == "https://spotify.com/sar"
    assert record.flags.portal_only is True
    assert record.flags.email_accepted is False


def test_map_datarequests_letter_transport() -> None:
    entry = {**_DR_ENTRY, "suggested-transport": "letter", "email": ""}
    record = _map_datarequests_entry(entry)
    assert record.contact.preferred_method == "postal"


def test_map_datarequests_address_parsed() -> None:
    record = _map_datarequests_entry(_DR_ENTRY)
    assert record.contact.postal_address.line1 == "Spotify AB"
    assert record.contact.postal_address.city == "Stockholm"
    assert record.contact.postal_address.country == "Sweden"


# ---------------------------------------------------------------------------
# ContactResolver — cache behaviour
# ---------------------------------------------------------------------------


def test_resolve_returns_fresh_cached_record(tmp_path: Path) -> None:
    resolver = _make_resolver(tmp_path)
    _seed_db(resolver, "spotify.com", _fresh_record())

    result = resolver.resolve("spotify.com", "Spotify")

    assert result is not None
    assert result.company_name == "Spotify"
    # HTTP and LLM should NOT have been called
    assert resolver._http_get.call_count == 0
    assert resolver._llm_search.call_count == 0


def test_resolve_skips_stale_cache_and_calls_datarequests(tmp_path: Path) -> None:
    mock_http = MagicMock()
    # Index returns one company
    index_resp = MagicMock()
    index_resp.raise_for_status = MagicMock()
    index_resp.json.return_value = [{**_DR_ENTRY, "runs": ["spotify.com"]}]
    # Individual company file fetch also returns the same entry
    company_resp = MagicMock()
    company_resp.ok = True
    company_resp.json.return_value = _DR_ENTRY
    mock_http.side_effect = [index_resp, company_resp]

    resolver = _make_resolver(tmp_path, http_get=mock_http)
    _seed_db(resolver, "spotify.com", _stale_record(source="datarequests", days_old=200))

    result = resolver.resolve("spotify.com", "Spotify")

    assert result is not None
    assert result.source == "datarequests"
    assert mock_http.call_count >= 1


def test_resolve_uses_llm_when_datarequests_misses(tmp_path: Path) -> None:
    mock_http = MagicMock()
    index_resp = MagicMock()
    index_resp.raise_for_status = MagicMock()
    index_resp.json.return_value = []  # empty index → no match
    mock_http.return_value = index_resp

    llm_record = _fresh_record(source="llm_search")
    mock_llm = MagicMock(return_value=llm_record)

    resolver = _make_resolver(tmp_path, http_get=mock_http, llm_search=mock_llm)

    result = resolver.resolve("unknown.com", "Unknown Co")

    assert result is not None
    assert result.source == "llm_search"
    mock_llm.assert_called_once_with("Unknown Co", "unknown.com")


def test_resolve_returns_none_when_all_sources_fail(tmp_path: Path) -> None:
    mock_http = MagicMock()
    index_resp = MagicMock()
    index_resp.raise_for_status = MagicMock()
    index_resp.json.return_value = []
    mock_http.return_value = index_resp

    resolver = _make_resolver(tmp_path)  # llm_search returns None by default

    result = resolver.resolve("nowhere.com", "Nowhere Inc")

    assert result is None


def test_resolve_llm_low_confidence_returns_none(tmp_path: Path) -> None:
    mock_http = MagicMock()
    index_resp = MagicMock()
    index_resp.raise_for_status = MagicMock()
    index_resp.json.return_value = []
    mock_http.return_value = index_resp

    low_confidence = CompanyRecord(
        company_name="Nowhere",
        source="llm_search",
        source_confidence="low",
        last_verified=TODAY,
    )
    mock_llm = MagicMock(return_value=low_confidence)
    resolver = _make_resolver(tmp_path, http_get=mock_http, llm_search=mock_llm)

    result = resolver.resolve("nowhere.com", "Nowhere")

    assert result is None


def test_resolve_persists_datarequests_result(tmp_path: Path) -> None:
    mock_http = MagicMock()
    index_resp = MagicMock()
    index_resp.raise_for_status = MagicMock()
    index_resp.json.return_value = [{**_DR_ENTRY, "runs": ["spotify.com"]}]
    company_resp = MagicMock()
    company_resp.ok = True
    company_resp.json.return_value = _DR_ENTRY
    mock_http.side_effect = [index_resp, company_resp]

    resolver = _make_resolver(tmp_path, http_get=mock_http)
    resolver.resolve("spotify.com", "Spotify")

    # Record must now be in the DB
    db = resolver._load_db()
    assert "spotify.com" in db.companies
    assert db.meta.total_companies == 1


# ---------------------------------------------------------------------------
# ContactResolver — staleness logic
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "source,days_old,expected_stale",
    [
        ("datarequests", 179, False),
        ("datarequests", 181, True),
        ("llm_search", 89, False),
        ("llm_search", 91, True),
        ("user_manual", 364, False),
        ("user_manual", 366, True),
    ],
)
def test_is_stale_per_source(
    tmp_path: Path,
    source: str,
    days_old: int,
    expected_stale: bool,
) -> None:
    resolver = _make_resolver(tmp_path)
    record = _stale_record(source=source, days_old=days_old)
    assert resolver._is_stale(record) is expected_stale


def test_is_stale_empty_last_verified(tmp_path: Path) -> None:
    resolver = _make_resolver(tmp_path)
    record = CompanyRecord(
        company_name="X",
        source="llm_search",
        source_confidence="high",
        last_verified="",
    )
    assert resolver._is_stale(record) is True


# ---------------------------------------------------------------------------
# ContactResolver — datarequests domain matching
# ---------------------------------------------------------------------------


def test_datarequests_matches_by_domain_in_runs(tmp_path: Path) -> None:
    index = [
        {**_DR_ENTRY, "runs": ["spotify.com", "spotify.de"]},
        {
            "name": "Netflix",
            "slug": "netflix",
            "runs": ["netflix.com"],
            "email": "privacy@netflix.com",
            "webform": "",
            "suggested-transport": "email",
            "address": "",
        },
    ]
    mock_http = MagicMock()
    index_resp = MagicMock()
    index_resp.raise_for_status = MagicMock()
    index_resp.json.return_value = index
    company_resp = MagicMock()
    company_resp.ok = True
    company_resp.json.return_value = _DR_ENTRY
    mock_http.side_effect = [index_resp, company_resp]

    resolver = _make_resolver(tmp_path, http_get=mock_http)
    result = resolver.resolve("spotify.com", "Spotify")

    assert result is not None
    assert result.company_name == "Spotify"


def test_datarequests_no_match_returns_none_from_step(tmp_path: Path) -> None:
    mock_http = MagicMock()
    index_resp = MagicMock()
    index_resp.raise_for_status = MagicMock()
    index_resp.json.return_value = [
        {**_DR_ENTRY, "runs": ["spotify.de"]}  # different domain
    ]
    mock_http.return_value = index_resp

    resolver = _make_resolver(tmp_path)
    # _search_datarequests should return None for "spotify.com"
    result = resolver._search_datarequests("spotify.com", "Spotify")
    assert result is None


def test_datarequests_http_error_does_not_crash(tmp_path: Path) -> None:
    mock_http = MagicMock(side_effect=Exception("Connection refused"))
    resolver = _make_resolver(tmp_path, http_get=mock_http)

    result = resolver._search_datarequests("spotify.com", "Spotify")
    assert result is None


# ---------------------------------------------------------------------------
# ContactResolver — save (user_manual path)
# ---------------------------------------------------------------------------


def test_save_persists_user_manual_record(tmp_path: Path) -> None:
    resolver = _make_resolver(tmp_path)
    record = CompanyRecord(
        company_name="My Bank",
        source="user_manual",
        source_confidence="high",
        last_verified=TODAY,
        contact=Contact(dpo_email="dpo@mybank.com"),
    )
    resolver.save("mybank.com", record)

    db = resolver._load_db()
    assert "mybank.com" in db.companies
    assert db.companies["mybank.com"].source == "user_manual"


# ---------------------------------------------------------------------------
# ContactResolver — index in-memory cache
# ---------------------------------------------------------------------------


def test_datarequests_index_fetched_only_once_per_session(tmp_path: Path) -> None:
    mock_http = MagicMock()
    index_resp = MagicMock()
    index_resp.raise_for_status = MagicMock()
    index_resp.json.return_value = []
    mock_http.return_value = index_resp

    resolver = _make_resolver(tmp_path, http_get=mock_http)
    resolver._search_datarequests("a.com", "A")
    resolver._search_datarequests("b.com", "B")

    # Index URL should only be fetched once despite two lookups
    index_calls = [
        c for c in mock_http.call_args_list
        if "companies/_index.json" in str(c)
    ]
    assert len(index_calls) == 1
