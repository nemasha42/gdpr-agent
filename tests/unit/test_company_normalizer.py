"""Unit tests for scanner/company_normalizer.py."""

from scanner.company_normalizer import canonical_domain, normalize_domain


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


def test_known_exception_alternate_mail_domain() -> None:
    assert normalize_domain("facebookmail.com") == "Facebook"


def test_known_exception_preserves_casing() -> None:
    assert normalize_domain("glassdoor.com") == "Glassdoor"


def test_known_exception_mixed_case() -> None:
    assert normalize_domain("github.com") == "GitHub"


def test_known_exception_after_stripping() -> None:
    """mail.github.com → github.com → hits exception → GitHub."""
    assert normalize_domain("mail.github.com") == "GitHub"


# ---------------------------------------------------------------------------
# canonical_domain
# ---------------------------------------------------------------------------


def test_canonical_domain_strips_subdomain() -> None:
    assert canonical_domain("accounts.google.com") == "google.com"


def test_canonical_domain_maps_alias() -> None:
    assert canonical_domain("youtube.com") == "google.com"


def test_canonical_domain_maps_ibkr() -> None:
    assert canonical_domain("ibkr.com") == "interactivebrokers.com"


def test_canonical_domain_passthrough() -> None:
    assert canonical_domain("spotify.com") == "spotify.com"


def test_canonical_domain_strips_then_maps() -> None:
    """communications.paypal.com → strip → paypal.com → already canonical."""
    assert canonical_domain("communications.paypal.com") == "paypal.com"


# ---------------------------------------------------------------------------
# normalize_domain with company groups
# ---------------------------------------------------------------------------


def test_alias_resolves_to_parent_group() -> None:
    assert normalize_domain("youtube.com") == "Google"


def test_normalize_ibkr_returns_interactive_brokers() -> None:
    assert normalize_domain("ibkr.com") == "Interactive Brokers"
