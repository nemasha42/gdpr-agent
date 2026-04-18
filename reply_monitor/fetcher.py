"""Fetch Gmail replies for sent SAR records.

Strategy:
  1. If gmail_thread_id is present -> threads.get() to get all messages in thread
     -> skip messages where From matches the user's own address
  2. Supplementary GDPR-keyword search against the company's domain -- catches
     out-of-thread replies from ticket systems (Zendesk, Freshdesk, etc.) that
     reply from new threads instead of the original SAR thread.
  3. Fallback (legacy records without thread_id) -> two Gmail searches:
       a) from:{to_email_address}  after:{day_before_sent}
       b) from:{domain}            after:{day_before_sent}
  4. Portal platform sender search -- when portal_sender_domains is provided,
     searches for emails from third-party portal platforms (Ketch, OneTrust,
     TrustArc, Salesforce) that send replies from their own domain.
  5. Deduplicate against already-stored reply IDs
"""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from typing import Any

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Gmail API exception types -- conditional import so tests run without google libs
# ---------------------------------------------------------------------------
_API_ERRORS: tuple[type[BaseException], ...] = (OSError,)
try:
    from googleapiclient.errors import HttpError

    _API_ERRORS = (*_API_ERRORS, HttpError)
except ImportError:
    pass

# ---------------------------------------------------------------------------
# GDPR keyword search constants
# ---------------------------------------------------------------------------

# Gmail search OR-group (Gmail {term1 term2} = any match).
_GDPR_GMAIL_TERMS = (
    '{gdpr "subject access" "data subject" "data request" '
    '"personal data" "right to erasure" sub-processor '
    '"data protection" sar "your request" "your data"}'
)

# Local regex for post-fetch validation of GDPR relevance.
_RE_GDPR_RELEVANT = re.compile(
    r"(?i)"
    r"(?:gdpr|subject.access|data.subject|data.request|personal.data|"
    r"right.to.erasure|sub.?processor|data.protection|"
    r"privacy.request|your.request|your.data|"
    r"data.export|download.your|erasure|rectification|"
    r"data.portability|article.1[5-7]|article.20)"
)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def fetch_replies_for_sar(
    service: Any,
    sent_record: dict,
    existing_reply_ids: set[str] | None = None,
    user_email: str = "",
    verbose: bool = False,
    portal_sender_domains: list[str] | None = None,
) -> list[dict]:
    """Return new reply message dicts for a sent SAR record.

    Each returned dict has:
        id, from, subject, received_at (ISO), snippet, body,
        has_attachment (bool), parts (list of attachment part dicts)

    Args:
        service:               Authenticated Gmail readonly service
        sent_record:           One entry from sent_letters.json
        existing_reply_ids:    Set of gmail_message_ids already stored in state
        user_email:            The sender's email address (to filter outgoing msgs)
        portal_sender_domains: Extra domains to search for portal platform replies
                               (e.g. ["ketch.com", "m.ketch.com"] for Ketch portals)
    """
    thread_id = sent_record.get("gmail_thread_id", "")
    seen_ids: set[str] = set(existing_reply_ids) if existing_reply_ids else set()
    messages: list[dict] = []

    if thread_id:
        # Thread-based lookup is authoritative for in-thread replies.
        thread_msgs = _fetch_by_thread(
            service, thread_id, user_email, seen_ids, verbose
        )
        for m in thread_msgs:
            m["in_thread"] = True  # classifier uses this to suppress NON_GDPR
        messages.extend(thread_msgs)
        for m in thread_msgs:
            seen_ids.add(m["id"])

        # Supplementary GDPR-keyword search: catches out-of-thread replies
        # from ticket systems (Zendesk, Freshdesk) that create new threads.
        gdpr_msgs = _fetch_gdpr_from_domain(
            service, sent_record, user_email, seen_ids, verbose
        )
        messages.extend(gdpr_msgs)
        for m in gdpr_msgs:
            seen_ids.add(m["id"])
    else:
        # No thread_id: legacy record or portal/postal SAR -- full domain search.
        search_msgs = _fetch_by_search(
            service, sent_record, user_email, seen_ids, verbose
        )
        messages.extend(search_msgs)
        for m in search_msgs:
            seen_ids.add(m["id"])

    # Portal platform sender domains: catches replies from third-party portal
    # platforms (Ketch, OneTrust, TrustArc, Salesforce) that send from their
    # own domain rather than the company's domain.
    if portal_sender_domains:
        portal_msgs = _fetch_from_portal_senders(
            service, sent_record, portal_sender_domains, user_email, seen_ids, verbose
        )
        messages.extend(portal_msgs)

    return messages


# ---------------------------------------------------------------------------
# Thread-based lookup
# ---------------------------------------------------------------------------


def _fetch_by_thread(
    service: Any,
    thread_id: str,
    user_email: str,
    existing_ids: set[str],
    verbose: bool = False,
) -> list[dict]:
    try:
        thread = (
            service.users()
            .threads()
            .get(userId="me", id=thread_id, format="full")
            .execute()
        )
    except _API_ERRORS as exc:
        logger.warning("Thread fetch failed for %s: %s", thread_id, exc)
        return []

    results = []
    for i, msg in enumerate(thread.get("messages", [])):
        if i == 0:
            continue  # First message is the original letter we sent
        if msg["id"] in existing_ids:
            continue
        from_header = _get_header(msg, "From")
        from_self = bool(user_email and user_email.lower() in from_header.lower())
        parsed = _parse_message(msg)
        if parsed:
            if from_self:
                parsed["from_self"] = True
            results.append(parsed)
    return results


# ---------------------------------------------------------------------------
# GDPR keyword search (supplementary -- runs after thread lookup)
# ---------------------------------------------------------------------------


def _fetch_gdpr_from_domain(
    service: Any,
    sent_record: dict,
    user_email: str,
    existing_ids: set[str],
    verbose: bool = False,
) -> list[dict]:
    """Search for GDPR-related emails from the company's domain.

    Catches out-of-thread replies from ticket systems that create new
    threads/tickets instead of replying in the original SAR thread.
    Results are filtered through _is_gdpr_relevant() to prevent false positives.
    """
    to_email = sent_record.get("to_email", "")
    if not to_email or "@" not in to_email:
        return []

    domain = to_email.split("@")[-1]
    date_filter = _date_filter(sent_record.get("sent_at", ""))
    query = f"from:{domain}{date_filter} {_GDPR_GMAIL_TERMS}"

    if verbose:
        print(f"    [gdpr-search] {query}")

    refs = _paginated_search(service, query, max_results=50)
    results: list[dict] = []

    for ref in refs:
        if ref["id"] in existing_ids:
            continue
        existing_ids.add(ref["id"])
        try:
            msg = (
                service.users()
                .messages()
                .get(userId="me", id=ref["id"], format="full")
                .execute()
            )
        except _API_ERRORS as exc:
            logger.debug("Failed to fetch message %s: %s", ref["id"], exc)
            continue

        from_header = _get_header(msg, "From")
        if user_email and user_email.lower() in from_header.lower():
            continue

        parsed = _parse_message(msg)
        if parsed and _is_gdpr_relevant(parsed):
            results.append(parsed)

    return results


def _is_gdpr_relevant(msg: dict) -> bool:
    """Check if a message's content is GDPR-relevant using local regex."""
    text = " ".join(
        [
            msg.get("subject", ""),
            msg.get("snippet", ""),
            msg.get("body", "")[:2000],
        ]
    )
    return bool(_RE_GDPR_RELEVANT.search(text))


# ---------------------------------------------------------------------------
# Portal platform sender search
# ---------------------------------------------------------------------------


def _fetch_from_portal_senders(
    service: Any,
    sent_record: dict,
    portal_domains: list[str],
    user_email: str,
    existing_ids: set[str],
    verbose: bool = False,
) -> list[dict]:
    """Search for emails from portal platform sender domains.

    When a company uses a third-party portal platform (Ketch, OneTrust, etc.),
    replies come from the platform's domain (e.g. m.ketch.com) rather than
    the company's domain.  This function searches each portal domain for
    GDPR-related emails sent after the SAR date.
    """
    date_filter = _date_filter(sent_record.get("sent_at", ""))
    results: list[dict] = []

    for portal_domain in portal_domains:
        query = f"from:{portal_domain}{date_filter} {_GDPR_GMAIL_TERMS}"

        if verbose:
            print(f"    [portal-search] {query}")

        refs = _paginated_search(service, query, max_results=50)

        for ref in refs:
            if ref["id"] in existing_ids:
                continue
            existing_ids.add(ref["id"])
            try:
                msg = (
                    service.users()
                    .messages()
                    .get(userId="me", id=ref["id"], format="full")
                    .execute()
                )
            except _API_ERRORS as exc:
                logger.debug("Failed to fetch message %s: %s", ref["id"], exc)
                continue

            from_header = _get_header(msg, "From")
            if user_email and user_email.lower() in from_header.lower():
                continue

            parsed = _parse_message(msg)
            if parsed:
                # Portal platform emails are inherently GDPR-relevant — they
                # only exist because a SAR was filed via that platform.  Still
                # run the relevance check to filter marketing from the same
                # platform domain, but also accept emails whose subject
                # contains the company name as a strong signal.
                company_name = sent_record.get("company_name", "")
                subject = parsed.get("subject", "")
                company_match = company_name and company_name.lower() in subject.lower()
                if company_match or _is_gdpr_relevant(parsed):
                    parsed["from_portal_platform"] = True
                    results.append(parsed)

    return results


# ---------------------------------------------------------------------------
# Search-based fallback (for records without thread_id)
# ---------------------------------------------------------------------------


def _fetch_by_search(
    service: Any,
    sent_record: dict,
    user_email: str,
    existing_ids: set[str],
    verbose: bool = False,
) -> list[dict]:
    to_email = sent_record.get("to_email", "")
    if not to_email or "@" not in to_email:
        return []

    domain = to_email.split("@")[-1]
    date_filter = _date_filter(sent_record.get("sent_at", ""))

    queries = []
    if to_email:
        queries.append(f"from:{to_email}{date_filter}")
    queries.append(f"from:{domain}{date_filter}")

    seen_ids: set[str] = set(existing_ids)
    results: list[dict] = []

    for query in queries:
        if verbose:
            print(f"    [search] {query}")
        refs = _paginated_search(service, query)
        for ref in refs:
            if ref["id"] in seen_ids:
                continue
            seen_ids.add(ref["id"])
            try:
                msg = (
                    service.users()
                    .messages()
                    .get(userId="me", id=ref["id"], format="full")
                    .execute()
                )
            except _API_ERRORS as exc:
                logger.debug("Failed to fetch message %s: %s", ref["id"], exc)
                continue
            from_header = _get_header(msg, "From")
            if user_email and user_email.lower() in from_header.lower():
                continue
            parsed = _parse_message(msg)
            if parsed:
                results.append(parsed)

    return results


def _date_filter(sent_at: str) -> str:
    """Build Gmail after: date filter string from an ISO datetime."""
    if not sent_at:
        return ""
    try:
        sent_date = datetime.fromisoformat(sent_at[:19]).date()
        before_sent = sent_date - timedelta(days=1)
        return f" after:{before_sent.isoformat().replace('-', '/')}"
    except ValueError:
        return ""


def _paginated_search(service: Any, query: str, max_results: int = 200) -> list[dict]:
    """Run a Gmail search query and return all message refs across pages."""
    refs: list[dict] = []
    page_token = None
    while True:
        kwargs: dict = {
            "userId": "me",
            "q": query,
            "maxResults": min(100, max_results - len(refs)),
        }
        if page_token:
            kwargs["pageToken"] = page_token
        try:
            resp = service.users().messages().list(**kwargs).execute()
        except _API_ERRORS as exc:
            logger.warning("Gmail search failed for query %r: %s", query, exc)
            break
        refs.extend(resp.get("messages", []))
        page_token = resp.get("nextPageToken")
        if not page_token or len(refs) >= max_results:
            break
    return refs


# ---------------------------------------------------------------------------
# Message parsing helpers
# ---------------------------------------------------------------------------


def _parse_message(msg: dict) -> dict | None:
    """Extract a flat dict from a full Gmail message resource."""
    try:
        msg_id = msg["id"]
        from_addr = _get_header(msg, "From")
        subject = _get_header(msg, "Subject")
        date_str = _get_header(msg, "Date")
        received_at = _parse_date(date_str)
        snippet = msg.get("snippet", "")
        payload = msg.get("payload", {})
        attachment_parts = _find_attachment_parts(payload)
        body = _extract_body(payload)

        return {
            "id": msg_id,
            "from": from_addr,
            "subject": subject,
            "received_at": received_at,
            "snippet": snippet,
            "body": body,
            "has_attachment": bool(attachment_parts),
            "parts": attachment_parts,
        }
    except (KeyError, TypeError):
        return None


def _extract_body(payload: dict) -> str:
    """Extract full plain-text body from a Gmail message payload.

    Handles:
    - text/plain parts (base64url-decoded)
    - multipart/alternative: prefers text/plain over text/html
    - multipart/*: concatenates text children
    - text/html fallback: strips tags and decodes HTML entities
    """
    import base64
    import html as _html
    import re as _re

    def _decode(part: dict) -> str:
        data = part.get("body", {}).get("data", "")
        if not data:
            return ""
        try:
            return base64.urlsafe_b64decode(data + "==").decode(
                "utf-8", errors="replace"
            )
        except ValueError as exc:
            logger.debug("Base64 decode failed: %s", exc)
            return ""

    def _strip_html(text: str) -> str:
        # Remove style/script/head blocks entirely (content is not useful plain text)
        text = _re.sub(
            r"<(style|script|head)[^>]*>.*?</\1>", " ", text, flags=_re.I | _re.S
        )
        # Preserve href URLs before stripping tags -- download links live in <a href="...">
        # Insert the URL as plain text next to the anchor text so regexes can find it.
        text = _re.sub(
            r'<a\s[^>]*href=["\']([^"\']+)["\'][^>]*>',
            lambda m: f" {m.group(1)} ",
            text,
            flags=_re.I,
        )
        text = _re.sub(r"<[^>]+>", " ", text)

        def _clean(t: str) -> str:
            # 1. Strip zero-width / invisible Unicode chars used as email padding
            t = _re.sub(
                r"[\u00ad\u034f\u115f\u1160\u17b4\u17b5"
                r"\u180b-\u180e\u200b-\u200f\u202a-\u202e"
                r"\u2060-\u2064\u2066-\u206f\u3164\ufeff\uffa0]+",
                "",
                t,
            )
            # 2. Normalize each line (strip leading/trailing whitespace)
            lines = [line.strip() for line in t.splitlines()]
            # 3. Deduplicate consecutive identical lines (catches duplicate URL lines)
            deduped: list[str] = []
            prev = object()
            for line in lines:
                if line != prev:
                    deduped.append(line)
                    prev = line
            # 4. Collapse 3+ consecutive blank lines to a single blank line
            result = _re.sub(r"\n{3,}", "\n\n", "\n".join(deduped))
            return result.strip()

        return _clean(_html.unescape(text))

    def _collect(part: dict) -> str:
        mime = part.get("mimeType", "")
        sub_parts = part.get("parts", [])

        if mime == "text/plain":
            return _decode(part)

        if mime == "text/html":
            return _strip_html(_decode(part))

        if mime == "multipart/alternative":
            # Prefer text/plain; fall back to html
            for sub in sub_parts:
                if sub.get("mimeType") == "text/plain":
                    text = _decode(sub)
                    if text:
                        return text
            for sub in sub_parts:
                if sub.get("mimeType") == "text/html":
                    return _strip_html(_decode(sub))
            return ""

        if mime.startswith("multipart/"):
            return "\n".join(_collect(sub) for sub in sub_parts)

        return ""

    return _collect(payload)


def _get_header(msg: dict, name: str) -> str:
    """Extract a header value from a Gmail message resource."""
    headers = msg.get("payload", {}).get("headers", [])
    for h in headers:
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


def _find_attachment_parts(payload: dict) -> list[dict]:
    """Recursively find MIME parts that are attachments."""
    parts: list[dict] = []
    _collect_attachment_parts(payload, parts)
    return parts


def _collect_attachment_parts(part: dict, out: list[dict]) -> None:
    filename = part.get("filename", "")
    body = part.get("body", {})
    if filename and body.get("attachmentId"):
        out.append(
            {
                "filename": filename,
                "mimeType": part.get("mimeType", ""),
                "attachmentId": body["attachmentId"],
                "size": body.get("size", 0),
            }
        )
    for sub in part.get("parts", []):
        _collect_attachment_parts(sub, out)


def _parse_date(date_str: str) -> str:
    """Parse RFC 2822 date string to ISO 8601 UTC string."""
    if not date_str:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")
    try:
        from email.utils import parsedate_to_datetime

        dt = parsedate_to_datetime(date_str)
        return (
            dt.astimezone(timezone.utc)
            .isoformat(timespec="seconds")
            .replace("+00:00", "Z")
        )
    except (ValueError, TypeError, OverflowError) as exc:
        logger.debug("Date parse failed for %r: %s", date_str, exc)
        return datetime.now(timezone.utc).isoformat(timespec="seconds")
