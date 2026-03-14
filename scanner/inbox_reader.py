"""Fetch email metadata from Gmail — sender, subject, date only."""

from typing import Any

# Gmail API allows at most 500 results per messages.list call.
_LIST_PAGE_SIZE = 500


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
