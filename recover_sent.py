"""Recover sent_letters.json from Gmail Sent folder, then launch the dashboard.

Usage:
    python recover_sent.py --account traderm1620@gmail.com
    python recover_sent.py --account traderm1620@gmail.com --no-dashboard
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

from auth.gmail_oauth import get_gmail_service
from letter_engine.tracker import get_log

_TRACKER_PATH = Path(__file__).parent / "user_data" / "sent_letters.json"

# Search queries to find SAR emails in Gmail Sent folder
_QUERIES = [
    'subject:"Subject Access Request" in:sent',
    'subject:"SAR Request" in:sent',
    'subject:"Data Subject Access Request" in:sent',
    'subject:"Right of Access" in:sent',
    "to:privacy@ in:sent",
    "to:dpo@ in:sent",
    "to:gdpr@ in:sent",
    "to:dataprotection@ in:sent",
]


def _fetch_sent_sars(service, verbose: bool = False) -> list[dict]:
    """Search Gmail Sent for SAR-related emails and return message metadata."""
    seen_ids: set[str] = set()
    all_messages = []

    for query in _QUERIES:
        if verbose:
            print(f"  Searching: {query}")
        try:
            result = (
                service.users()
                .messages()
                .list(
                    userId="me",
                    q=query,
                    maxResults=100,
                )
                .execute()
            )
        except Exception as e:
            print(f"  Warning: query failed ({e}), skipping.")
            continue

        for m in result.get("messages", []):
            if m["id"] in seen_ids:
                continue
            seen_ids.add(m["id"])
            all_messages.append(m)

    return all_messages


def _get_message_detail(service, message_id: str) -> dict | None:
    """Fetch full metadata for a single message."""
    try:
        msg = (
            service.users()
            .messages()
            .get(
                userId="me",
                id=message_id,
                format="metadata",
                metadataHeaders=["To", "Subject", "Date", "From"],
            )
            .execute()
        )
        return msg
    except Exception as e:
        print(f"  Warning: could not fetch message {message_id}: {e}")
        return None


def _parse_date(date_str: str) -> str:
    """Convert RFC2822 date string to ISO format."""
    try:
        dt = parsedate_to_datetime(date_str)
        return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")
    except Exception:
        return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S")


def _is_sar_subject(subject: str) -> bool:
    """Check if subject looks like an SAR email."""
    subject_lower = subject.lower()
    keywords = [
        "subject access request",
        "sar request",
        "data subject access",
        "right of access",
        "gdpr",
        "dsar",
        "personal data",
    ]
    return any(k in subject_lower for k in keywords)


def _is_sar_recipient(to_addr: str) -> bool:
    """Check if recipient looks like a privacy/DPO address."""
    to_lower = to_addr.lower()
    keywords = [
        "privacy",
        "dpo",
        "gdpr",
        "dataprotection",
        "data-protection",
        "legal",
        "compliance",
        "sar",
    ]
    return any(k in to_lower for k in keywords)


def main() -> None:
    args = _parse_args()

    if not Path("credentials.json").exists():
        print("credentials.json not found. Run python setup.py first.")
        sys.exit(1)

    print(f"Connecting to Gmail as {args.account or '(auto)'}...")
    service, email = get_gmail_service(email_hint=args.account)
    print(f"Connected: {email}\n")

    # Load existing tracker to avoid duplicates
    # Entries with empty gmail_message_id are useless — treat as untracked
    existing = get_log()
    existing_valid = [r for r in existing if r.get("gmail_thread_id")]
    existing_ids = {r["gmail_message_id"] for r in existing_valid}
    dropped = len(existing) - len(existing_valid)
    if dropped:
        print(
            f"Dropping {dropped} existing entries with missing thread IDs (will re-recover from Gmail)."
        )
    print(f"Existing tracked SARs (with thread IDs): {len(existing_valid)}")

    # Search Sent folder
    print("Searching Gmail Sent folder for SAR emails...")
    messages = _fetch_sent_sars(service, verbose=args.verbose)
    print(f"Found {len(messages)} candidate message(s) to inspect.\n")

    recovered = []
    skipped_dup = 0
    skipped_non_sar = 0

    for m in messages:
        if m["id"] in existing_ids:
            skipped_dup += 1
            continue

        detail = _get_message_detail(service, m["id"])
        if not detail:
            continue

        headers = {h["name"]: h["value"] for h in detail["payload"]["headers"]}
        subject = headers.get("Subject", "")
        to_addr = headers.get("To", "")
        date_str = headers.get("Date", "")

        # Filter: must look like an SAR email
        if not (_is_sar_subject(subject) or _is_sar_recipient(to_addr)):
            skipped_non_sar += 1
            if args.verbose:
                print(f"  Skip (not SAR): {subject[:60]} → {to_addr[:40]}")
            continue

        entry = {
            "sent_at": _parse_date(date_str),
            "company_name": _guess_company(to_addr),
            "method": "email",
            "to_email": to_addr,
            "subject": subject,
            "gmail_message_id": m["id"],
            "gmail_thread_id": detail["threadId"],
        }
        recovered.append(entry)
        print(
            f"  Recovered: {entry['company_name']:<30} {entry['sent_at']}  →  {to_addr}"
        )

    if not recovered and not existing:
        print("\nNo SAR emails found in Sent folder.")
        print("If you used a different subject line, check Gmail and create")
        print("user_data/sent_letters.json manually (see README for format).")
        sys.exit(0)

    if recovered:
        # Merge with existing (valid only) and save
        merged = existing_valid + recovered
        _TRACKER_PATH.parent.mkdir(parents=True, exist_ok=True)
        _TRACKER_PATH.write_text(json.dumps(merged, indent=2))
        print(f"\nSaved {len(merged)} total SARs to user_data/sent_letters.json")
        print(
            f"  (+{len(recovered)} recovered, {skipped_dup} already tracked, {skipped_non_sar} skipped non-SAR)"
        )
    else:
        print(f"\nAll {len(existing_valid)} SARs already tracked — nothing new to add.")

    if args.no_dashboard:
        print("\nDone. Run 'python monitor.py' to refresh states,")
        print("or 'python dashboard/app.py' to open the web UI.")
        return

    # Run monitor once to populate reply state
    print("\nRunning monitor to fetch reply states...")
    subprocess.run([sys.executable, "monitor.py", "--account", email], check=False)

    # Launch dashboard
    print("\n" + "=" * 50)
    print("  Dashboard ready → http://localhost:5001")
    print("  Press Ctrl+C to stop.")
    print("=" * 50 + "\n")
    subprocess.run([sys.executable, "dashboard/app.py"])


def _guess_company(to_addr: str) -> str:
    """Guess company name from email address (best effort)."""
    try:
        domain = to_addr.split("@")[-1].split(">")[0].strip()
        # Strip TLD(s)
        parts = domain.split(".")
        if len(parts) >= 2:
            # Handle co.uk, com.au etc.
            if parts[-2] in ("co", "com", "org", "net") and len(parts[-1]) == 2:
                name = parts[-3] if len(parts) >= 3 else parts[-2]
            else:
                name = parts[-2]
            return name.capitalize()
        return domain
    except Exception:
        return to_addr


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Recover sent_letters.json from Gmail and launch dashboard"
    )
    parser.add_argument(
        "--account",
        metavar="EMAIL",
        default=None,
        help="Gmail account (e.g. traderm1620@gmail.com)",
    )
    parser.add_argument(
        "--no-dashboard",
        action="store_true",
        help="Skip launching the dashboard after recovery",
    )
    parser.add_argument(
        "--verbose", action="store_true", help="Show each message being inspected"
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
