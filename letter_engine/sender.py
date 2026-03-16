"""Preview a SAR letter, ask for human confirmation, then dispatch it."""

import base64
from email.mime.text import MIMEText

from letter_engine import tracker
from letter_engine.models import SARLetter

_WIDTH = 62


def preview_and_send(letter: SARLetter, *, dry_run: bool = False, scan_email: str = "") -> bool:
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
        msg_id, thread_id = _dispatch_email(letter, scan_email)
        letter.gmail_message_id = msg_id
        letter.gmail_thread_id = thread_id
    elif letter.method == "portal":
        print(f"\nPlease submit your SAR manually at:\n  {letter.portal_url}")
        print("\nCopy the letter body above to paste into the portal form.")
    else:  # postal
        print("\nPlease print and post the letter above.")

    tracker.record_sent(letter)
    return True


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


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


def _dispatch_email(letter: SARLetter, scan_email: str) -> tuple[str, str]:
    """Send via Gmail API; fall back to printing instructions on failure.

    Returns:
        (message_id, thread_id) on success, ("", "") on failure.
    """
    try:
        from auth.gmail_oauth import get_gmail_send_service
        service = get_gmail_send_service(scan_email)
        msg = MIMEText(letter.body)
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
