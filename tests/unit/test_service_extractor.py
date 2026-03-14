"""Unit tests for scanner/service_extractor.py."""

import json
from pathlib import Path

import pytest

from scanner.service_extractor import extract_services, _extract_domain, _classify

_FIXTURES = Path(__file__).parent.parent / "fixtures" / "sample_emails.json"


def _load_fixtures() -> list[dict[str, str]]:
    return json.loads(_FIXTURES.read_text())


# ---------------------------------------------------------------------------
# _extract_domain
# ---------------------------------------------------------------------------


def test_extract_domain_plain_address() -> None:
    assert _extract_domain("noreply@netflix.com") == "netflix.com"


def test_extract_domain_display_name_format() -> None:
    assert _extract_domain('"Netflix" <no-reply@netflix.com>') == "netflix.com"


def test_extract_domain_no_at_symbol() -> None:
    assert _extract_domain("not-an-email") is None


def test_extract_domain_subdomain_preserved() -> None:
    """Domain extraction keeps subdomains intact; normalization strips them."""
    assert _extract_domain("user@mail.spotify.com") == "mail.spotify.com"


def test_extract_domain_uppercased_is_lowercased() -> None:
    assert _extract_domain("User@SPOTIFY.COM") == "spotify.com"


# ---------------------------------------------------------------------------
# _classify
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "subject,expected_confidence",
    [
        ("Welcome to Acme!", "HIGH"),
        ("Please verify your email address", "HIGH"),
        ("Confirm your account", "HIGH"),
        ("Thanks for signing up", "HIGH"),
        ("Activate your account now", "HIGH"),
        ("Your order has shipped", "MEDIUM"),
        ("Your account statement", "MEDIUM"),
        ("New sign in detected", "MEDIUM"),
        ("Please login to continue", "MEDIUM"),
        ("Your monthly newsletter", "LOW"),
        ("Flash sale: 50% off everything", "LOW"),
    ],
)
def test_classify_confidence(subject: str, expected_confidence: str) -> None:
    confidence, _ = _classify(subject)
    assert confidence == expected_confidence


def test_classify_high_returns_matching_phrase() -> None:
    _, signal = _classify("Welcome to our platform!")
    assert signal == "welcome to"


def test_classify_medium_returns_matching_phrase() -> None:
    _, signal = _classify("Track your order here")
    assert signal == "your order"


def test_classify_low_returns_transactional() -> None:
    _, signal = _classify("Your receipt for March")
    assert signal == "transactional"


# ---------------------------------------------------------------------------
# extract_services — using fixtures
# ---------------------------------------------------------------------------


def test_extract_services_returns_one_per_domain() -> None:
    """Each unique domain appears exactly once in the output."""
    fixtures = _load_fixtures()
    results = extract_services(fixtures)
    domains = [r["domain"] for r in results]
    assert len(domains) == len(set(domains))


def test_extract_services_all_fixture_domains_present() -> None:
    fixtures = _load_fixtures()
    results = extract_services(fixtures)
    returned_domains = {r["domain"] for r in results}

    expected_domains = {
        "netflix.com",
        "spotify.com",
        "amazon.co.uk",
        "linkedin.com",
        "twitter.com",
        "deliveroo.co.uk",
        "monzo.com",
        "wise.com",
        "github.com",
        "apple.com",
    }
    assert expected_domains == returned_domains


def test_extract_services_company_names_normalised() -> None:
    fixtures = _load_fixtures()
    results = extract_services(fixtures)
    by_domain = {r["domain"]: r for r in results}

    assert by_domain["spotify.com"]["company_name_raw"] == "Spotify"
    assert by_domain["amazon.co.uk"]["company_name_raw"] == "Amazon"
    assert by_domain["github.com"]["company_name_raw"] == "GitHub"


def test_extract_services_medium_confidence_detected() -> None:
    """amazon.co.uk, twitter.com, deliveroo.co.uk have MEDIUM signals."""
    fixtures = _load_fixtures()
    results = extract_services(fixtures)
    by_domain = {r["domain"]: r for r in results}

    assert by_domain["amazon.co.uk"]["confidence"] == "MEDIUM"
    assert by_domain["amazon.co.uk"]["signal_type"] == "your order"

    assert by_domain["twitter.com"]["confidence"] == "MEDIUM"
    assert by_domain["twitter.com"]["signal_type"] == "sign-in"

    assert by_domain["deliveroo.co.uk"]["confidence"] == "MEDIUM"
    assert by_domain["deliveroo.co.uk"]["signal_type"] == "your order"


def test_extract_services_low_confidence_default() -> None:
    by_domain = {r["domain"]: r for r in extract_services(_load_fixtures())}
    assert by_domain["spotify.com"]["confidence"] == "LOW"
    assert by_domain["monzo.com"]["confidence"] == "LOW"


def test_extract_services_sorted_high_before_low() -> None:
    """HIGH-confidence domains appear before LOW-confidence ones in output."""
    emails = _load_fixtures() + [
        {
            "message_id": "msg_high",
            "sender": "hello@newservice.com",
            "subject": "Welcome to NewService!",
            "date": "Mon, 10 Mar 2025 09:00:00 +0000",
        }
    ]
    results = extract_services(emails)
    confidences = [r["confidence"] for r in results]
    rank = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}
    ranked = [rank[c] for c in confidences]
    assert ranked == sorted(ranked, reverse=True)


# ---------------------------------------------------------------------------
# Deduplication behaviour
# ---------------------------------------------------------------------------


def test_deduplication_merges_same_domain() -> None:
    emails = [
        {
            "message_id": "a",
            "sender": "receipts@spotify.com",
            "subject": "Your receipt",
            "date": "Mon, 10 Mar 2025 09:00:00 +0000",
        },
        {
            "message_id": "b",
            "sender": "hello@spotify.com",
            "subject": "Welcome to Spotify",
            "date": "Tue, 11 Mar 2025 10:00:00 +0000",
        },
    ]
    results = extract_services(emails)
    assert len(results) == 1
    assert results[0]["domain"] == "spotify.com"


def test_deduplication_upgrades_confidence() -> None:
    """Second email from same domain with higher confidence upgrades the record."""
    emails = [
        {
            "message_id": "a",
            "sender": "receipts@spotify.com",
            "subject": "Your receipt",
            "date": "Mon, 10 Mar 2025 09:00:00 +0000",
        },
        {
            "message_id": "b",
            "sender": "hello@spotify.com",
            "subject": "Welcome to Spotify!",
            "date": "Tue, 11 Mar 2025 10:00:00 +0000",
        },
    ]
    results = extract_services(emails)
    assert results[0]["confidence"] == "HIGH"
    assert results[0]["signal_type"] == "welcome to"


def test_deduplication_does_not_downgrade_confidence() -> None:
    """A LOW email arriving after a HIGH one must not downgrade confidence."""
    emails = [
        {
            "message_id": "a",
            "sender": "hello@stripe.com",
            "subject": "Welcome to Stripe",
            "date": "Mon, 10 Mar 2025 09:00:00 +0000",
        },
        {
            "message_id": "b",
            "sender": "receipts@stripe.com",
            "subject": "Your monthly invoice",
            "date": "Tue, 11 Mar 2025 10:00:00 +0000",
        },
    ]
    results = extract_services(emails)
    assert results[0]["confidence"] == "HIGH"


def test_deduplication_tracks_date_range() -> None:
    emails = [
        {
            "message_id": "a",
            "sender": "noreply@stripe.com",
            "subject": "Your invoice",
            "date": "Mon, 10 Mar 2025 09:00:00 +0000",
        },
        {
            "message_id": "b",
            "sender": "noreply@stripe.com",
            "subject": "Your invoice",
            "date": "Fri, 14 Mar 2025 18:00:00 +0000",
        },
    ]
    results = extract_services(emails)
    assert results[0]["first_seen"] == "2025-03-10"
    assert results[0]["last_seen"] == "2025-03-14"


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


def test_sender_without_at_symbol_is_skipped() -> None:
    emails = [
        {"message_id": "x", "sender": "not-an-email", "subject": "Hi", "date": "Mon, 10 Mar 2025 09:00:00 +0000"},
        {"message_id": "y", "sender": "real@service.com", "subject": "Hi", "date": "Mon, 10 Mar 2025 09:00:00 +0000"},
    ]
    results = extract_services(emails)
    assert len(results) == 1
    assert results[0]["domain"] == "service.com"


def test_empty_email_list_returns_empty() -> None:
    assert extract_services([]) == []


def test_required_fields_present_in_output() -> None:
    emails = [
        {
            "message_id": "z",
            "sender": "hi@example.com",
            "subject": "Welcome!",
            "date": "Mon, 10 Mar 2025 09:00:00 +0000",
        }
    ]
    result = extract_services(emails)[0]
    for field in ("domain", "company_name_raw", "confidence", "signal_type", "first_seen", "last_seen"):
        assert field in result, f"Missing field: {field}"
