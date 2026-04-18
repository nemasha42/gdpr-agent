"""Extract registered-service signals from raw email metadata.

No LLM — pure regex and heuristics only.
"""

from email.utils import parseaddr, parsedate_to_datetime

from scanner.company_normalizer import canonical_domain, normalize_domain

# ---------------------------------------------------------------------------
# Signal definitions — checked in order, first match wins per email
# ---------------------------------------------------------------------------

_HIGH_SIGNALS: tuple[str, ...] = (
    "thanks for signing up",
    "confirm your account",
    "verify your email",
    "verify your account",
    "activate your account",
    "welcome to",
    "activate",
    "welcome",
    "verify",
)

_MEDIUM_SIGNALS: tuple[str, ...] = (
    "your account",
    "your order",
    "sign-in",
    "sign in",
    "login",
)

_CONFIDENCE_RANK: dict[str, int] = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _extract_domain(sender: str) -> str | None:
    """Extract the domain portion from a raw sender field.

    Handles both plain addresses (``user@example.com``) and RFC 5322
    display-name forms (``"Name" <user@example.com>``).
    """
    _, addr = parseaddr(sender)
    if "@" not in addr:
        return None
    return addr.split("@", 1)[1].lower().strip()


def _classify(subject: str) -> tuple[str, str]:
    """Return ``(confidence, signal_type)`` for *subject*.

    Confidence levels:
        - ``HIGH``   — clear sign-up / account-creation email
        - ``MEDIUM`` — transactional email tied to an existing account
        - ``LOW``    — any other email (receipts, newsletters, misc.)
    """
    lower = subject.lower()
    for phrase in _HIGH_SIGNALS:
        if phrase in lower:
            return "HIGH", phrase
    for phrase in _MEDIUM_SIGNALS:
        if phrase in lower:
            return "MEDIUM", phrase
    return "LOW", "transactional"


def _parse_date_iso(date_str: str) -> str:
    """Parse an RFC 2822 date string to an ISO-8601 date (``YYYY-MM-DD``).

    Returns *date_str* unchanged if parsing fails.
    """
    try:
        return parsedate_to_datetime(date_str).date().isoformat()
    except Exception as exc:
        print(f"[service_extractor] date parse failed: {exc}")
        return date_str


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def extract_services(
    emails: list[dict[str, str]],
) -> list[dict[str, str]]:
    """Identify and deduplicate services from email metadata.

    Args:
        emails: Output of :func:`scanner.inbox_reader.fetch_emails` —
                list of dicts with keys ``sender``, ``subject``, ``date``,
                ``message_id``.

    Returns:
        Deduplicated list of dicts, one per domain:

        .. code-block:: python

            {
                "domain":           "spotify.com",
                "company_name_raw": "Spotify",
                "confidence":       "MEDIUM",
                "signal_type":      "your account",
                "first_seen":       "2025-01-01",
                "last_seen":        "2025-03-14",
            }

        Sorted by confidence descending (HIGH → MEDIUM → LOW), then
        domain alphabetically.
    """
    # domain → accumulated record
    seen: dict[str, dict[str, str]] = {}

    for email in emails:
        domain = _extract_domain(email.get("sender", ""))
        if not domain:
            continue

        confidence, signal_type = _classify(email.get("subject", ""))
        date_iso = _parse_date_iso(email.get("date", ""))

        canon = canonical_domain(domain)

        if canon not in seen:
            seen[canon] = {
                "domain": canon,
                "company_name_raw": normalize_domain(domain),
                "confidence": confidence,
                "signal_type": signal_type,
                "first_seen": date_iso,
                "last_seen": date_iso,
            }
        else:
            record = seen[canon]
            # Upgrade confidence if this email is stronger evidence
            if _CONFIDENCE_RANK[confidence] > _CONFIDENCE_RANK[record["confidence"]]:
                record["confidence"] = confidence
                record["signal_type"] = signal_type
            # Expand the observed date window
            if date_iso and date_iso < record["first_seen"]:
                record["first_seen"] = date_iso
            if date_iso and date_iso > record["last_seen"]:
                record["last_seen"] = date_iso

    results = list(seen.values())
    results.sort(key=lambda r: (-_CONFIDENCE_RANK[r["confidence"]], r["domain"]))
    return results
