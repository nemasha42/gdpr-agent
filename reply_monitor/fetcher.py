"""Fetch Gmail replies for sent SAR records.

Strategy:
  1. If gmail_thread_id is present → threads.get() to get all messages in thread
     → skip messages where From matches the user's own address
  2. Fallback (legacy records without thread_id) → two Gmail searches:
       a) from:{to_email_address}  after:{day_before_sent}   (exact address, broadest catch)
       b) from:{domain}            after:{day_before_sent}   (whole domain, catches replies
                                                              from a different address)
     No subject filter — auto-acks, OOO replies, and data-download emails all have
     different subject lines and would be missed otherwise.
  3. Deduplicate against already-stored reply IDs
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def fetch_replies_for_sar(
    service: Any,
    sent_record: dict,
    existing_reply_ids: set[str] | None = None,
    user_email: str = "",
    verbose: bool = False,
) -> list[dict]:
    """Return new reply message dicts for a sent SAR record.

    Each returned dict has:
        id, from, subject, received_at (ISO), snippet, has_attachment (bool),
        parts (list of attachment part dicts for the attachment handler)

    Args:
        service:            Authenticated Gmail readonly service
        sent_record:        One entry from sent_letters.json
        existing_reply_ids: Set of gmail_message_ids already stored in state
        user_email:         The sender's email address (to filter outgoing msgs)
    """
    if existing_reply_ids is None:
        existing_reply_ids = set()

    thread_id = sent_record.get("gmail_thread_id", "")
    messages: list[dict] = []

    seen_ids: set[str] = set(existing_reply_ids)
    messages: list[dict] = []

    if thread_id:
        # Thread-based lookup is authoritative: only messages in our SAR's Gmail
        # thread are genuine replies. Do NOT also search by domain — that picks up
        # newsletters, marketing mail, and other unrelated emails from the same sender.
        thread_msgs = _fetch_by_thread(
            service, thread_id, user_email, seen_ids, verbose
        )
        messages.extend(thread_msgs)
    else:
        # No thread_id: legacy record or portal/postal SAR — fall back to domain search.
        search_msgs = _fetch_by_search(
            service, sent_record, user_email, seen_ids, verbose
        )
        messages.extend(search_msgs)

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
            .get(
                userId="me",
                id=thread_id,
                format="full",
            )
            .execute()
        )
    except Exception:
        return []

    results = []
    for i, msg in enumerate(thread.get("messages", [])):
        if i == 0:
            continue  # First message is the original letter we sent — always skip
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
# Search-based fallback
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
    sent_at = sent_record.get("sent_at", "")

    # Use one day before sent_at so same-day replies are never excluded.
    # Gmail's after: filter is exclusive of the specified date.
    date_filter = ""
    if sent_at:
        try:
            sent_date = datetime.fromisoformat(sent_at[:19]).date()
            before_sent = sent_date - timedelta(days=1)
            date_filter = f" after:{before_sent.isoformat().replace('-', '/')}"
        except ValueError:
            pass

    # Query (a): exact address we sent to (most targeted)
    # Query (b): whole domain (catches replies from privacy@ even when we sent to dpo@)
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
                    .get(
                        userId="me",
                        id=ref["id"],
                        format="full",
                    )
                    .execute()
                )
            except Exception:
                continue
            from_header = _get_header(msg, "From")
            if user_email and user_email.lower() in from_header.lower():
                continue
            parsed = _parse_message(msg)
            if parsed:
                results.append(parsed)

    return results


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
        except Exception:
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
        except Exception:
            return ""

    def _strip_html(text: str) -> str:
        # Remove style/script/head blocks entirely (content is not useful plain text)
        text = _re.sub(
            r"<(style|script|head)[^>]*>.*?</\1>", " ", text, flags=_re.I | _re.S
        )
        # Preserve href URLs before stripping tags — download links live in <a href="...">
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
    parts = []
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
    except Exception:
        return datetime.now(timezone.utc).isoformat(timespec="seconds")
