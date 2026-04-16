"""Monitor Gmail for OTP/verification emails after portal form submission."""

import re
import time
from typing import Callable

# Patterns for confirmation/verification URLs
_CONFIRM_URL_RE = re.compile(
    r"https?://[\w./-]+" r"(?:confirm|verify|validate|activate)" r"[\w./?&=%-]*",
    re.IGNORECASE,
)

# 6-digit OTP code
_OTP_CODE_RE = re.compile(r"\b(\d{6})\b")

_DEFAULT_TIMEOUT = 120  # 2 minutes
_DEFAULT_POLL_INTERVAL = 10  # seconds


def extract_otp_from_message(body: str) -> dict | None:
    """Extract a confirmation URL or OTP code from an email body.

    Returns:
        {"type": "url", "value": "https://..."} or
        {"type": "code", "value": "123456"} or
        None if nothing found.
    """
    # URLs take priority over codes
    url_match = _CONFIRM_URL_RE.search(body)
    if url_match:
        return {"type": "url", "value": url_match.group()}

    code_match = _OTP_CODE_RE.search(body)
    if code_match:
        return {"type": "code", "value": code_match.group(1)}

    return None


def wait_for_otp(
    scan_email: str,
    sender_hints: list[str],
    *,
    fetch_recent: Callable | None = None,
    timeout: float = _DEFAULT_TIMEOUT,
    poll_interval: float = _DEFAULT_POLL_INTERVAL,
) -> dict | None:
    """Poll Gmail for a verification email and extract OTP/confirmation link.

    Args:
        scan_email: Gmail account to poll.
        sender_hints: Email addresses or domain fragments to match against sender.
        fetch_recent: Injectable callable(scan_email, sender_hints, since_minutes) -> list[dict].
                      Each dict has "from", "body", "date". If None, uses Gmail API.
        timeout: Max seconds to wait.
        poll_interval: Seconds between polls.

    Returns:
        {"type": "url"|"code", "value": str} or None on timeout.
    """
    if fetch_recent is None:
        fetch_recent = _gmail_fetch_recent

    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        messages = fetch_recent(scan_email, sender_hints, since_minutes=5)
        for msg in messages:
            result = extract_otp_from_message(msg.get("body", ""))
            if result:
                return result
        time.sleep(poll_interval)

    return None


def _gmail_fetch_recent(
    scan_email: str,
    sender_hints: list[str],
    since_minutes: int = 5,
) -> list[dict]:
    """Fetch recent Gmail messages matching sender hints. Uses existing OAuth."""
    try:
        from auth.gmail_oauth import get_gmail_service
        from datetime import datetime, timedelta, timezone

        service, _ = get_gmail_service(email_hint=scan_email)

        from_clauses = " OR ".join(f"from:{s}" for s in sender_hints)
        cutoff = datetime.now(timezone.utc) - timedelta(minutes=since_minutes)
        query = f"({from_clauses}) after:{int(cutoff.timestamp())}"

        resp = (
            service.users()
            .messages()
            .list(userId="me", q=query, maxResults=5)
            .execute()
        )
        messages = resp.get("messages", [])

        results = []
        for msg_ref in messages:
            msg = (
                service.users()
                .messages()
                .get(userId="me", id=msg_ref["id"], format="full")
                .execute()
            )

            headers = {
                h["name"].lower(): h["value"]
                for h in msg.get("payload", {}).get("headers", [])
            }
            body = _extract_body(msg.get("payload", {}))

            results.append(
                {
                    "from": headers.get("from", ""),
                    "body": body,
                    "date": headers.get("date", ""),
                }
            )

        return results
    except Exception:
        return []


def _extract_body(payload: dict) -> str:
    """Extract plain text body from Gmail message payload."""
    import base64

    if payload.get("mimeType") == "text/plain" and payload.get("body", {}).get("data"):
        return base64.urlsafe_b64decode(payload["body"]["data"]).decode(
            "utf-8", errors="replace"
        )

    for part in payload.get("parts", []):
        text = _extract_body(part)
        if text:
            return text

    return ""
