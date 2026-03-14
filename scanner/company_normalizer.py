"""Convert raw email domains to clean company display names."""

# Ordered longest-first so that more-specific prefixes match before shorter ones
# (e.g. "no-reply." before a hypothetical "reply.")
_SUBDOMAIN_PREFIXES: tuple[str, ...] = (
    "notifications.",
    "no-reply.",
    "marketing.",
    "newsletter.",
    "noreply.",
    "accounts.",
    "security.",
    "support.",
    "updates.",
    "mailer.",
    "bounce.",
    "alerts.",
    "reply.",
    "email.",
    "hello.",
    "alert.",
    "info.",
    "mail.",
    "news.",
)

# Two-part TLDs that should not be treated as the company name segment
_TWO_PART_TLDS: frozenset[str] = frozenset(
    {
        "co.uk",
        "co.nz",
        "co.jp",
        "co.in",
        "co.za",
        "com.au",
        "com.br",
        "org.uk",
        "me.uk",
        "net.au",
    }
)

# Hard overrides — entire domain (after subdomain stripping) → display name
_KNOWN_EXCEPTIONS: dict[str, str] = {
    "t.co": "Twitter/X",
    "facebookmail.com": "Facebook",
    "glassdoor.com": "Glassdoor",
    "substack.com": "Substack",
    "amazonaws.com": "AWS",
    "googlemail.com": "Google",
    "googlegroups.com": "Google Groups",
    "paypal.com": "PayPal",
    "github.com": "GitHub",
    "linkedin.com": "LinkedIn",
}


def _strip_subdomains(domain: str) -> str:
    """Recursively strip known noise subdomain prefixes from *domain*."""
    for prefix in _SUBDOMAIN_PREFIXES:
        if domain.startswith(prefix):
            return _strip_subdomains(domain[len(prefix):])
    return domain


def _root_to_name(domain: str) -> str:
    """Derive a display name from a registrable domain.

    Examples:
        spotify.com    → Spotify
        amazon.co.uk   → Amazon
        github.com     → GitHub   (handled by exceptions before this is called)
    """
    parts = domain.split(".")
    # Check for known two-part TLDs (e.g. co.uk)
    if len(parts) >= 3 and ".".join(parts[-2:]) in _TWO_PART_TLDS:
        name_part = parts[-3]
    elif len(parts) >= 2:
        name_part = parts[-2]
    else:
        name_part = parts[0]
    return name_part.capitalize()


def normalize_domain(domain: str) -> str:
    """Return a clean company display name for *domain*.

    Args:
        domain: Raw email domain, e.g. ``"mail.spotify.com"``,
                ``"facebookmail.com"``, ``"amazon.co.uk"``.

    Returns:
        Human-readable company name, e.g. ``"Spotify"``, ``"Facebook"``,
        ``"Amazon"``.
    """
    domain = domain.lower().strip()

    if domain in _KNOWN_EXCEPTIONS:
        return _KNOWN_EXCEPTIONS[domain]

    cleaned = _strip_subdomains(domain)

    if cleaned in _KNOWN_EXCEPTIONS:
        return _KNOWN_EXCEPTIONS[cleaned]

    return _root_to_name(cleaned)
