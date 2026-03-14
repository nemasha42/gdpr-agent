"""Unit tests for scanner/company_normalizer.py."""

import pytest

from scanner.company_normalizer import normalize_domain


# ---------------------------------------------------------------------------
# Basic domain → name
# ---------------------------------------------------------------------------


def test_simple_com_domain() -> None:
    assert normalize_domain("spotify.com") == "Spotify"


def test_simple_com_domain_capitalised() -> None:
    assert normalize_domain("netflix.com") == "Netflix"


def test_input_is_case_insensitive() -> None:
    assert normalize_domain("SPOTIFY.COM") == "Spotify"


# ---------------------------------------------------------------------------
# co.uk and other two-part TLDs
# ---------------------------------------------------------------------------


def test_co_uk_tld() -> None:
    assert normalize_domain("amazon.co.uk") == "Amazon"


def test_co_uk_tld_deliveroo() -> None:
    assert normalize_domain("deliveroo.co.uk") == "Deliveroo"


def test_com_au_tld() -> None:
    assert normalize_domain("woolworths.com.au") == "Woolworths"


# ---------------------------------------------------------------------------
# Subdomain prefix stripping
# ---------------------------------------------------------------------------


def test_strip_mail_prefix() -> None:
    assert normalize_domain("mail.spotify.com") == "Spotify"


def test_strip_email_prefix() -> None:
    assert normalize_domain("email.netflix.com") == "Netflix"


def test_strip_noreply_prefix() -> None:
    assert normalize_domain("noreply.medium.com") == "Medium"


def test_strip_no_reply_hyphen_prefix() -> None:
    assert normalize_domain("no-reply.acme.com") == "Acme"


def test_strip_accounts_prefix() -> None:
    assert normalize_domain("accounts.google.com") == "Google"


def test_strip_support_prefix() -> None:
    assert normalize_domain("support.stripe.com") == "Stripe"


def test_strip_notifications_prefix() -> None:
    assert normalize_domain("notifications.slack.com") == "Slack"


def test_strip_marketing_prefix() -> None:
    assert normalize_domain("marketing.hubspot.com") == "Hubspot"


def test_multi_level_subdomain_stripping() -> None:
    """noreply.accounts.google.com → accounts.google.com → google.com → Google."""
    assert normalize_domain("noreply.accounts.google.com") == "Google"


# ---------------------------------------------------------------------------
# Known exceptions (hardcoded overrides)
# ---------------------------------------------------------------------------


def test_known_exception_t_co() -> None:
    assert normalize_domain("t.co") == "Twitter/X"


def test_known_exception_facebookmail() -> None:
    assert normalize_domain("facebookmail.com") == "Facebook"


def test_known_exception_glassdoor() -> None:
    assert normalize_domain("glassdoor.com") == "Glassdoor"


def test_known_exception_substack() -> None:
    assert normalize_domain("substack.com") == "Substack"


def test_known_exception_github() -> None:
    assert normalize_domain("github.com") == "GitHub"


def test_known_exception_linkedin() -> None:
    assert normalize_domain("linkedin.com") == "LinkedIn"


def test_known_exception_after_stripping() -> None:
    """mail.github.com → github.com → hits exception → GitHub."""
    assert normalize_domain("mail.github.com") == "GitHub"
