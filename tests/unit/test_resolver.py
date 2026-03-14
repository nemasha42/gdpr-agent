"""Unit tests for contact_resolver/resolver.py."""

import json
from datetime import date, timedelta
from pathlib import Path
from unittest.mock import MagicMock

import pytest

from contact_resolver.models import (
    CompaniesDB,
    CompanyRecord,
    Contact,
    PostalAddress,
    RequestNotes,
)
from contact_resolver.resolver import (
    ContactResolver,
    _find_candidate_files,
    _map_datarequests_entry,
    _parse_postal_address,
)

# ---------------------------------------------------------------------------
# Constants & shared fixtures
# ---------------------------------------------------------------------------

TODAY = date.today().isoformat()

# A realistic datarequests.org company entry (used across many tests)
_DR_ENTRY: dict = {
    "name": "Spotify",
    "slug": "spotify",
    "runs": ["spotify.com", "spotify.de"],
    "email": "privacy@spotify.com",
    "webform": "",
    "suggested-transport": "email",
    "address": "Spotify AB\nBirger Jarlsgatan 61\nStockholm\nSweden",
}

# A dataowners entry for testing the overrides lookup
_DATAOWNERS_SPOTIFY: dict = {
    "spotify.com": {
        "company_name": "Spotify",
        "legal_entity_name": "Spotify AB",
        "source": "dataowners_override",
        "source_confidence": "high",
        "last_verified": TODAY,
        "contact": {
            "dpo_email": "",
            "privacy_email": "privacy@spotify.com",
            "gdpr_portal_url": "https://www.spotify.com/account/privacy/",
            "postal_address": {
                "line1": "Regeringsgatan 19",
                "city": "Stockholm",
                "postcode": "SE-111 53",
                "country": "Sweden",
            },
            "preferred_method": "email",
        },
        "flags": {"portal_only": False, "email_accepted": True, "auto_send_possible": False},
        "request_notes": {
            "special_instructions": "",
            "identity_verification_required": False,
            "known_response_time_days": 30,
        },
    }
}


# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


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
    privacy_scrape: MagicMock | None = None,
    dataowners: dict | None = None,
) -> ContactResolver:
    """Build a ContactResolver with an isolated tmp DB and controllable deps."""
    dataowners_path = tmp_path / "dataowners_overrides.json"
    dataowners_path.write_text(json.dumps(dataowners or {}))
    return ContactResolver(
        db_path=tmp_path / "companies.json",
        dataowners_path=dataowners_path,
        http_get=http_get or MagicMock(),
        llm_search=llm_search or MagicMock(return_value=None),
        privacy_scrape=privacy_scrape or MagicMock(return_value=None),
    )


def _seed_db(resolver: ContactResolver, domain: str, record: CompanyRecord) -> None:
    db = CompaniesDB(companies={domain: record})
    resolver._db_path.write_text(db.model_dump_json(indent=2))


def _make_dir_listing(*filenames: str) -> list[dict]:
    """Simulate a GitHub API directory listing."""
    return [
        {
            "name": fname,
            "download_url": f"https://raw.githubusercontent.com/datenanfragen/data/master/companies/{fname}",
        }
        for fname in filenames
    ]


def _mock_http_datarequests(
    filenames: list[str],
    company_entry: dict,
) -> MagicMock:
    """Build a mock http_get that returns a dir listing then a company file."""
    dir_resp = MagicMock()
    dir_resp.raise_for_status = MagicMock()
    dir_resp.json.return_value = _make_dir_listing(*filenames)

    company_resp = MagicMock()
    company_resp.ok = True
    company_resp.json.return_value = company_entry

    mock = MagicMock()
    mock.side_effect = [dir_resp, company_resp]
    return mock


# ---------------------------------------------------------------------------
# _parse_postal_address
# ---------------------------------------------------------------------------


def test_parse_postal_empty_string() -> None:
    assert _parse_postal_address("") == PostalAddress()


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


def test_parse_postal_four_lines_last_two_are_city_country() -> None:
    addr = _parse_postal_address("Acme Corp\n1 Street\nBerlin\nGermany")
    assert addr.line1 == "Acme Corp"
    assert addr.city == "Berlin"
    assert addr.country == "Germany"


# ---------------------------------------------------------------------------
# _map_datarequests_entry
# ---------------------------------------------------------------------------


def test_map_datarequests_email_transport() -> None:
    record = _map_datarequests_entry(_DR_ENTRY)
    assert record.source == "datarequests"
    assert record.source_confidence == "high"
    assert record.contact.privacy_email == "privacy@spotify.com"
    assert record.contact.preferred_method == "email"
    assert record.flags.email_accepted is True


def test_map_datarequests_webform_transport() -> None:
    entry = {
        **_DR_ENTRY,
        "suggested-transport": "webform",
        "email": "",
        "webform": "https://spotify.com/sar",
    }
    record = _map_datarequests_entry(entry)
    assert record.contact.preferred_method == "portal"
    assert record.contact.gdpr_portal_url == "https://spotify.com/sar"
    assert record.flags.portal_only is True
    assert record.flags.email_accepted is False


def test_map_datarequests_letter_transport() -> None:
    entry = {**_DR_ENTRY, "suggested-transport": "letter", "email": ""}
    assert _map_datarequests_entry(entry).contact.preferred_method == "postal"


def test_map_datarequests_address_parsed() -> None:
    record = _map_datarequests_entry(_DR_ENTRY)
    assert record.contact.postal_address.line1 == "Spotify AB"
    assert record.contact.postal_address.city == "Stockholm"
    assert record.contact.postal_address.country == "Sweden"


# ---------------------------------------------------------------------------
# _find_candidate_files
# ---------------------------------------------------------------------------


def test_find_candidate_files_matches_domain_root() -> None:
    listing = _make_dir_listing("spotify.json", "amazon.json", "apple.json")
    results = _find_candidate_files(listing, "spotify.com", "Spotify AB")
    assert len(results) == 1
    assert results[0]["name"] == "spotify.json"


def test_find_candidate_files_matches_company_word() -> None:
    listing = _make_dir_listing("spotify-ab.json", "amazon.json")
    results = _find_candidate_files(listing, "spotify.com", "Spotify")
    assert any(r["name"] == "spotify-ab.json" for r in results)


def test_find_candidate_files_no_match_returns_empty() -> None:
    listing = _make_dir_listing("amazon.json", "apple.json")
    results = _find_candidate_files(listing, "stripe.com", "Stripe")
    assert results == []


def test_find_candidate_files_skips_non_json() -> None:
    listing = _make_dir_listing("README.md", "spotify.json")
    results = _find_candidate_files(listing, "spotify.com", "Spotify")
    assert all(r["name"].endswith(".json") for r in results)


def test_find_candidate_files_caps_at_max() -> None:
    # 10 files all matching "test"
    listing = _make_dir_listing(*[f"test-{i}.json" for i in range(10)])
    results = _find_candidate_files(listing, "test.com", "Test Co")
    assert len(results) <= 5


# ---------------------------------------------------------------------------
# ContactResolver — cache behaviour
# ---------------------------------------------------------------------------


def test_resolve_returns_fresh_cached_record(tmp_path: Path) -> None:
    resolver = _make_resolver(tmp_path)
    _seed_db(resolver, "spotify.com", _fresh_record())

    result = resolver.resolve("spotify.com", "Spotify")

    assert result is not None
    assert result.company_name == "Spotify"
    assert resolver._http_get.call_count == 0
    assert resolver._llm_search.call_count == 0


def test_resolve_skips_stale_cache_and_continues(tmp_path: Path) -> None:
    """Stale cache → falls through to next steps (dataowners → datarequests)."""
    mock_http = _mock_http_datarequests(["spotify.json"], _DR_ENTRY)
    resolver = _make_resolver(tmp_path, http_get=mock_http)
    _seed_db(resolver, "spotify.com", _stale_record(source="datarequests", days_old=200))

    result = resolver.resolve("spotify.com", "Spotify")

    assert result is not None
    assert result.source == "datarequests"


# ---------------------------------------------------------------------------
# ContactResolver — dataowners step
# ---------------------------------------------------------------------------


def test_resolve_finds_domain_in_dataowners(tmp_path: Path) -> None:
    resolver = _make_resolver(tmp_path, dataowners=_DATAOWNERS_SPOTIFY)

    result = resolver.resolve("spotify.com", "Spotify")

    assert result is not None
    assert result.company_name == "Spotify"
    assert result.source == "dataowners_override"
    # datarequests and LLM should be skipped
    assert resolver._http_get.call_count == 0
    assert resolver._llm_search.call_count == 0


def test_resolve_dataowners_result_persisted_to_db(tmp_path: Path) -> None:
    resolver = _make_resolver(tmp_path, dataowners=_DATAOWNERS_SPOTIFY)
    resolver.resolve("spotify.com", "Spotify")

    db = resolver._load_db()
    assert "spotify.com" in db.companies
    assert db.companies["spotify.com"].source == "dataowners_override"


def test_resolve_dataowners_miss_falls_through_to_datarequests(tmp_path: Path) -> None:
    mock_http = _mock_http_datarequests(["spotify.json"], _DR_ENTRY)
    # dataowners has a different domain
    resolver = _make_resolver(
        tmp_path,
        http_get=mock_http,
        dataowners={"other.com": _DATAOWNERS_SPOTIFY["spotify.com"]},
    )

    result = resolver.resolve("spotify.com", "Spotify")

    assert result is not None
    assert result.source == "datarequests"


# ---------------------------------------------------------------------------
# ContactResolver — datarequests step
# ---------------------------------------------------------------------------


def test_resolve_datarequests_hit(tmp_path: Path) -> None:
    mock_http = _mock_http_datarequests(["spotify.json"], _DR_ENTRY)
    resolver = _make_resolver(tmp_path, http_get=mock_http)

    result = resolver.resolve("spotify.com", "Spotify")

    assert result is not None
    assert result.source == "datarequests"
    assert result.contact.privacy_email == "privacy@spotify.com"


def test_resolve_datarequests_domain_must_be_in_runs(tmp_path: Path) -> None:
    """Company file fetched but domain not in 'runs' → no match."""
    entry_wrong_domain = {**_DR_ENTRY, "runs": ["spotify.de"]}
    dir_resp = MagicMock()
    dir_resp.raise_for_status = MagicMock()
    dir_resp.json.return_value = _make_dir_listing("spotify.json")
    company_resp = MagicMock()
    company_resp.ok = True
    company_resp.json.return_value = entry_wrong_domain
    mock_http = MagicMock(side_effect=[dir_resp, company_resp])

    resolver = _make_resolver(tmp_path, http_get=mock_http)
    result = resolver._search_datarequests("spotify.com", "Spotify")

    assert result is None


def test_resolve_datarequests_persists_result(tmp_path: Path) -> None:
    mock_http = _mock_http_datarequests(["spotify.json"], _DR_ENTRY)
    resolver = _make_resolver(tmp_path, http_get=mock_http)
    resolver.resolve("spotify.com", "Spotify")

    db = resolver._load_db()
    assert "spotify.com" in db.companies
    assert db.meta.total_companies == 1


def test_resolve_datarequests_http_error_does_not_crash(tmp_path: Path) -> None:
    mock_http = MagicMock(side_effect=Exception("Connection refused"))
    resolver = _make_resolver(tmp_path, http_get=mock_http)

    result = resolver._search_datarequests("spotify.com", "Spotify")
    assert result is None


def test_dir_listing_fetched_only_once_per_session(tmp_path: Path) -> None:
    """GitHub API should only be called once across multiple lookups."""
    dir_resp = MagicMock()
    dir_resp.raise_for_status = MagicMock()
    dir_resp.json.return_value = []  # empty listing → no matches
    mock_http = MagicMock(return_value=dir_resp)

    resolver = _make_resolver(tmp_path, http_get=mock_http)
    resolver._search_datarequests("a.com", "A")
    resolver._search_datarequests("b.com", "B")

    # Only one HTTP call total (the directory listing); never re-fetched
    assert mock_http.call_count == 1


# ---------------------------------------------------------------------------
# ContactResolver — LLM step
# ---------------------------------------------------------------------------


def test_resolve_uses_llm_when_all_else_misses(tmp_path: Path) -> None:
    dir_resp = MagicMock()
    dir_resp.raise_for_status = MagicMock()
    dir_resp.json.return_value = []
    mock_http = MagicMock(return_value=dir_resp)

    llm_record = _fresh_record(source="llm_search")
    mock_llm = MagicMock(return_value=llm_record)

    resolver = _make_resolver(tmp_path, http_get=mock_http, llm_search=mock_llm)
    result = resolver.resolve("unknown.com", "Unknown Co")

    assert result is not None
    assert result.source == "llm_search"
    mock_llm.assert_called_once_with("Unknown Co", "unknown.com")


def test_resolve_llm_low_confidence_returns_none(tmp_path: Path) -> None:
    dir_resp = MagicMock()
    dir_resp.raise_for_status = MagicMock()
    dir_resp.json.return_value = []
    mock_http = MagicMock(return_value=dir_resp)

    low = CompanyRecord(
        company_name="X",
        source="llm_search",
        source_confidence="low",
        last_verified=TODAY,
    )
    resolver = _make_resolver(tmp_path, http_get=mock_http, llm_search=MagicMock(return_value=low))

    assert resolver.resolve("x.com", "X") is None


def test_resolve_returns_none_when_all_sources_fail(tmp_path: Path) -> None:
    dir_resp = MagicMock()
    dir_resp.raise_for_status = MagicMock()
    dir_resp.json.return_value = []
    mock_http = MagicMock(return_value=dir_resp)

    resolver = _make_resolver(tmp_path, http_get=mock_http)  # llm returns None

    assert resolver.resolve("nowhere.com", "Nowhere") is None


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
        ("dataowners_override", 179, False),
        ("dataowners_override", 181, True),
        ("user_manual", 364, False),
        ("user_manual", 366, True),
    ],
)
def test_is_stale_per_source(
    tmp_path: Path, source: str, days_old: int, expected_stale: bool
) -> None:
    resolver = _make_resolver(tmp_path)
    record = _stale_record(source=source, days_old=days_old)
    assert resolver._is_stale(record) is expected_stale


def test_is_stale_empty_last_verified(tmp_path: Path) -> None:
    resolver = _make_resolver(tmp_path)
    record = CompanyRecord(
        company_name="X", source="llm_search", source_confidence="high", last_verified=""
    )
    assert resolver._is_stale(record) is True


# ---------------------------------------------------------------------------
# ContactResolver — privacy page scraper step
# ---------------------------------------------------------------------------


def test_resolve_privacy_page_hit(tmp_path: Path) -> None:
    """Privacy scraper returns a record → resolver returns it and skips LLM."""
    dir_resp = MagicMock()
    dir_resp.raise_for_status = MagicMock()
    dir_resp.json.return_value = []  # datarequests: no match
    mock_http = MagicMock(return_value=dir_resp)

    scrape_record = CompanyRecord(
        company_name="Acme",
        source="privacy_scrape",
        source_confidence="medium",
        last_verified=TODAY,
        contact=Contact(privacy_email="privacy@acme.com"),
    )
    mock_scrape = MagicMock(return_value=scrape_record)
    mock_llm = MagicMock(return_value=None)

    resolver = _make_resolver(
        tmp_path, http_get=mock_http, privacy_scrape=mock_scrape, llm_search=mock_llm
    )
    result = resolver.resolve("acme.com", "Acme")

    assert result is not None
    assert result.source == "privacy_scrape"
    assert result.contact.privacy_email == "privacy@acme.com"
    mock_scrape.assert_called_once_with("acme.com", "Acme", verbose=False)
    mock_llm.assert_not_called()


def test_resolve_privacy_page_miss_falls_through_to_llm(tmp_path: Path) -> None:
    """Privacy scraper returns None → resolver continues to LLM."""
    dir_resp = MagicMock()
    dir_resp.raise_for_status = MagicMock()
    dir_resp.json.return_value = []
    mock_http = MagicMock(return_value=dir_resp)

    llm_record = _fresh_record(source="llm_search")
    mock_llm = MagicMock(return_value=llm_record)

    resolver = _make_resolver(
        tmp_path,
        http_get=mock_http,
        privacy_scrape=MagicMock(return_value=None),
        llm_search=mock_llm,
    )
    result = resolver.resolve("unknown.com", "Unknown")

    assert result is not None
    assert result.source == "llm_search"
    mock_llm.assert_called_once()


def test_resolve_privacy_page_result_persisted(tmp_path: Path) -> None:
    dir_resp = MagicMock()
    dir_resp.raise_for_status = MagicMock()
    dir_resp.json.return_value = []
    mock_http = MagicMock(return_value=dir_resp)

    scrape_record = CompanyRecord(
        company_name="Acme",
        source="privacy_scrape",
        source_confidence="medium",
        last_verified=TODAY,
        contact=Contact(privacy_email="privacy@acme.com"),
    )
    resolver = _make_resolver(
        tmp_path, http_get=mock_http, privacy_scrape=MagicMock(return_value=scrape_record)
    )
    resolver.resolve("acme.com", "Acme")

    db = resolver._load_db()
    assert "acme.com" in db.companies
    assert db.companies["acme.com"].source == "privacy_scrape"


# ---------------------------------------------------------------------------
# ContactResolver — verbose mode
# ---------------------------------------------------------------------------


def test_verbose_cache_hit(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    resolver = _make_resolver(tmp_path)
    _seed_db(resolver, "spotify.com", _fresh_record())

    resolver.resolve("spotify.com", "Spotify", verbose=True)

    out = capsys.readouterr().out
    assert "[CACHE HIT]" in out
    assert "spotify.com" in out


def test_verbose_cache_miss_and_dataowners_found(
    tmp_path: Path, capsys: pytest.CaptureFixture
) -> None:
    resolver = _make_resolver(tmp_path, dataowners=_DATAOWNERS_SPOTIFY)

    resolver.resolve("spotify.com", "Spotify", verbose=True)

    out = capsys.readouterr().out
    assert "[CACHE MISS]" in out
    assert "[DATAOWNERS]" in out
    assert "found" in out


def test_verbose_not_found(tmp_path: Path, capsys: pytest.CaptureFixture) -> None:
    dir_resp = MagicMock()
    dir_resp.raise_for_status = MagicMock()
    dir_resp.json.return_value = []
    mock_http = MagicMock(return_value=dir_resp)

    resolver = _make_resolver(tmp_path, http_get=mock_http)
    resolver.resolve("nowhere.com", "Nowhere", verbose=True)

    out = capsys.readouterr().out
    assert "[NOT FOUND]" in out


# ---------------------------------------------------------------------------
# ContactResolver — save (user_manual)
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
