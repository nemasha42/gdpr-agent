"""Preview a SAR letter, ask for human confirmation, then dispatch it."""

import base64
from email.mime.text import MIMEText
from pathlib import Path

from letter_engine import tracker
from letter_engine.models import SARLetter

_WIDTH = 62


def preview_and_send(letter: SARLetter, *, dry_run: bool = False, scan_email: str = "",
                     data_dir: Path | None = None, tokens_dir: Path | None = None) -> bool:
    """Print a formatted preview, ask Y/N, dispatch on approval.

    Args:
        letter:  The composed SARLetter to review and send.
        dry_run: If True, skip actual delivery even when user says yes.
                 Used in tests and test_phase4.py.

    Returns:
        True if the user approved the letter, False if skipped.
    """
    _print_preview(letter)

    try:
        answer = input("\nSend this letter? [y/N]: ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        print("\nSkipped.")
        return False

    if answer != "y":
        print("Skipped.")
        return False

    if dry_run:
        print("[DRY RUN] Confirmed — skipping actual delivery.")
        return True

    if letter.method == "email":
        msg_id, thread_id = _dispatch_email(letter, scan_email, tokens_dir=tokens_dir)
        letter.gmail_message_id = msg_id
        letter.gmail_thread_id = thread_id
    elif letter.method == "portal":
        print(f"\nPlease submit your SAR manually at:\n  {letter.portal_url}")
        print("\nCopy the letter body above to paste into the portal form.")
    else:  # postal
        print("\nPlease print and post the letter above.")

    tracker.record_sent(letter, data_dir=data_dir)
    return True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def send_letter(
    letter: SARLetter,
    scan_email: str,
    *,
    record: bool = True,
    data_dir: Path | None = None,
    tokens_dir: Path | None = None,
) -> tuple[bool, str, str]:
    """Send *letter* without an interactive Y/N prompt.

    Args:
        record: If False, skip writing to sent_letters.json. Pass False when
                sending subprocessor disclosure requests — those are recorded
                separately in subprocessor_requests.json by the caller.

    Returns:
        (success, message_id, thread_id).
        For portal/postal methods success is always True (user must act manually);
        message_id and thread_id are empty strings.
    """
    if letter.method == "email":
        msg_id, thread_id = _dispatch_email(letter, scan_email, tokens_dir=tokens_dir)
        letter.gmail_message_id = msg_id
        letter.gmail_thread_id = thread_id
        if msg_id and record:
            # Only record when Gmail API confirmed delivery — empty msg_id means the
            # API call failed and the email was never sent.
            tracker.record_sent(letter, data_dir=data_dir)
        return bool(msg_id), msg_id, thread_id

    # portal / postal — record as sent; user handles submission manually
    if record:
        tracker.record_sent(letter, data_dir=data_dir)
    return True, "", ""


def _print_preview(letter: SARLetter) -> None:
    print("\n" + "═" * _WIDTH)
    print(f"  SAR PREVIEW — {letter.company_name}")
    print("═" * _WIDTH)
    print(f"  Method : {letter.method.upper()}")
    if letter.method == "email":
        print(f"  To     : {letter.to_email}")
        print(f"  Subject: {letter.subject}")
    elif letter.method == "portal":
        print(f"  Portal : {letter.portal_url}")
    else:
        print("  Post to:")
        for line in letter.postal_address.splitlines():
            print(f"    {line}")
    print("─" * _WIDTH)
    print(letter.body)
    print("═" * _WIDTH)


def send_thread_reply(
    thread_id: str,
    to_addr: str,
    subject: str,
    body: str,
    scan_email: str,
    *,
    tokens_dir: Path | None = None,
) -> tuple[bool, str, str]:
    """Send a reply within an existing Gmail thread.

    Returns (success, message_id, thread_id).
    """
    try:
        from auth.gmail_oauth import get_gmail_send_service
        service = get_gmail_send_service(scan_email, tokens_dir=tokens_dir)
        msg = MIMEText(body, "plain", "utf-8")
        msg["to"] = to_addr
        msg["subject"] = subject if subject.startswith("Re:") else f"Re: {subject}"
        msg["In-Reply-To"] = thread_id
        msg["References"] = thread_id
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        result = service.users().messages().send(
            userId="me",
            body={"raw": raw, "threadId": thread_id},
        ).execute()
        return True, result.get("id", ""), result.get("threadId", "")
    except Exception as exc:
        print(f"[send_thread_reply] failed: {exc}")
        return False, "", ""


def _dispatch_email(letter: SARLetter, scan_email: str, *, tokens_dir: Path | None = None) -> tuple[str, str]:
    """Send via Gmail API; fall back to printing instructions on failure.

    Returns:
        (message_id, thread_id) on success, ("", "") on failure.
    """
    try:
        from auth.gmail_oauth import get_gmail_send_service
        service = get_gmail_send_service(scan_email, tokens_dir=tokens_dir)
        msg = MIMEText(letter.body, 'plain', 'utf-8')
        msg["to"] = letter.to_email
        msg["subject"] = letter.subject
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()
        result = service.users().messages().send(userId="me", body={"raw": raw}).execute()
        print(f"\nEmail sent to {letter.to_email}")
        return result.get("id", ""), result.get("threadId", "")
    except Exception as exc:
        print(f"\nCould not send via Gmail API ({exc}).")
        print(f"Please send manually to: {letter.to_email}")
        print("\nLetter body:\n")
        print(letter.body)
        return "", ""
