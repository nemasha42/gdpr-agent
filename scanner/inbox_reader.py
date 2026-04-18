"""Fetch email metadata from Gmail — sender, subject, date only."""

from typing import Any, Callable

# Gmail API allows at most 500 results per messages.list call.
_LIST_PAGE_SIZE = 500


def get_inbox_total(service: Any) -> int:
    """Return the total number of messages in the inbox from Gmail profile.

    Uses users.getProfile which is a single fast API call.
    Returns 0 on any error so callers can treat it as 'unknown'.
    """
    try:
        profile = service.users().getProfile(userId="me").execute()
        return int(profile.get("messagesTotal", 0))
    except Exception as exc:
        print(f"[inbox_reader] get_inbox_total failed: {exc}")
        return 0


def fetch_emails(
    service: Any,
    max_results: int = 500,
) -> list[dict[str, str]]:
    """Fetch email metadata from the authenticated user's Gmail inbox.

    Paginates automatically until max_results are collected or the inbox
    is exhausted. No email bodies are fetched — only headers.

    Args:
        service: Authenticated Gmail API service object.
        max_results: Maximum number of emails to return (default 500).

    Returns:
        List of dicts, each with keys:
            message_id, sender, subject, date
    """
    emails: list[dict[str, str]] = []
    page_token: str | None = None

    while len(emails) < max_results:
        remaining = max_results - len(emails)

        list_response: dict = (
            service.users()
            .messages()
            .list(
                userId="me",
                maxResults=min(_LIST_PAGE_SIZE, remaining),
                pageToken=page_token,
                fields="messages(id),nextPageToken",
            )
            .execute()
        )

        raw = list_response.get("messages", [])
        if not raw:
            break

        for msg in raw:
            if len(emails) >= max_results:
                break
            detail: dict = (
                service.users()
                .messages()
                .get(
                    userId="me",
                    id=msg["id"],
                    format="metadata",
                    metadataHeaders=["From", "Subject", "Date"],
                )
                .execute()
            )
            headers = {
                h["name"]: h["value"]
                for h in detail.get("payload", {}).get("headers", [])
            }
            emails.append(
                {
                    "message_id": detail["id"],
                    "sender": headers.get("From", ""),
                    "subject": headers.get("Subject", ""),
                    "date": headers.get("Date", ""),
                }
            )

        page_token = list_response.get("nextPageToken")
        if not page_token:
            break

    return emails


def fetch_new_emails(
    service: Any,
    known_ids: set[str],
    max_results: int = 500,
    progress_callback: Callable[[int], None] | None = None,
) -> list[dict[str, str]]:
    """Fetch only emails whose message ID is not in *known_ids*.

    Pages through the inbox (newest-first) and stops as soon as an entire
    page of IDs is already known — meaning we have reached the already-scanned
    frontier and there is nothing older to fetch.

    Args:
        service:           Authenticated Gmail API service object.
        known_ids:         Set of already-processed Gmail message IDs.
        max_results:       Cap on the number of new emails to return.
        progress_callback: Called with the running count of new emails after
                           each page, for UI progress updates.

    Returns:
        List of dicts with keys: message_id, sender, subject, date.
        Only contains messages whose ID was not in *known_ids*.
    """
    new_emails: list[dict[str, str]] = []
    page_token: str | None = None

    while len(new_emails) < max_results:
        remaining = max_results - len(new_emails)

        list_response: dict = (
            service.users()
            .messages()
            .list(
                userId="me",
                maxResults=min(_LIST_PAGE_SIZE, remaining + len(known_ids)),
                pageToken=page_token,
                fields="messages(id),nextPageToken",
            )
            .execute()
        )

        raw = list_response.get("messages", [])
        if not raw:
            break

        page_new_ids = [msg["id"] for msg in raw if msg["id"] not in known_ids]

        # Early-stop: if this entire page was already known, we've hit the frontier
        if not page_new_ids:
            break

        for msg_id in page_new_ids:
            if len(new_emails) >= max_results:
                break
            detail: dict = (
                service.users()
                .messages()
                .get(
                    userId="me",
                    id=msg_id,
                    format="metadata",
                    metadataHeaders=["From", "Subject", "Date"],
                )
                .execute()
            )
            headers = {
                h["name"]: h["value"]
                for h in detail.get("payload", {}).get("headers", [])
            }
            new_emails.append({
                "message_id": detail["id"],
                "sender": headers.get("From", ""),
                "subject": headers.get("Subject", ""),
                "date": headers.get("Date", ""),
            })
            if progress_callback:
                progress_callback(len(new_emails))

        page_token = list_response.get("nextPageToken")
        if not page_token:
            break

    return new_emails
