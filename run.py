"""Full GDPR agent pipeline.

Steps:
  1. Connect to Gmail via OAuth
  2. Scan inbox for service / welcome emails
  3. For each discovered company, resolve the GDPR contact
  4. Compose a SAR letter per company
  5. Preview each letter and ask Y/N before sending

Usage:
    python run.py                  # full run, real sending
    python run.py --dry-run        # preview only, nothing sent
    python run.py --gmail EMAIL    # choose which Gmail account to scan
    python run.py --max-emails 200 # limit inbox scan (default 500)
    python run.py --min-confidence MEDIUM  # skip LOW-confidence services

First time? Run  python setup.py  to create your credentials.json.
"""

import argparse
import sys
from pathlib import Path

from auth.gmail_oauth import get_gmail_service
from contact_resolver import cost_tracker
from contact_resolver.resolver import ContactResolver
from letter_engine.composer import compose
from letter_engine.sender import preview_and_send
from scanner.inbox_reader import fetch_emails
from scanner.service_extractor import extract_services


def main() -> None:
    args = _parse_args()

    # ── Preflight: credentials check ─────────────────────────────────────────
    if not (Path(__file__).parent / "credentials.json").exists():
        print("credentials.json not found.")
        print("Run  python setup.py  to create your Google Cloud credentials.")
        sys.exit(1)

    # Apply LLM call cap early so it's active even if the run exits early
    if args.max_llm_calls is not None:
        cost_tracker.set_llm_limit(args.max_llm_calls)
        print(f"LLM call cap: {args.max_llm_calls}\n")

    # ── Step 1: Gmail connection ─────────────────────────────────────────────
    print("Connecting to Gmail...")
    from dashboard.user_model import user_data_dir, load_user

    service, email = get_gmail_service(email_hint=args.gmail)
    data_dir = user_data_dir(email)
    data_dir.mkdir(parents=True, exist_ok=True)
    tokens_dir = data_dir / "tokens"
    print(f"Scanning: {email}\n")

    # Build user_identity for letter composition
    user = load_user(email)
    if user:
        from config.settings import settings

        user_identity = {
            "user_full_name": user.name,
            "user_email": email,
            "user_address_line1": "",
            "user_address_city": "",
            "user_address_postcode": "",
            "user_address_country": "",
            "gdpr_framework": settings.gdpr_framework,
        }
    else:
        user_identity = None  # composer falls back to settings

    # ── Step 2: Scan inbox ───────────────────────────────────────────────────
    print(f"Scanning inbox (up to {args.max_emails} emails)...")
    emails = fetch_emails(service, max_results=args.max_emails)
    print(f"Fetched {len(emails)} emails.\n")

    services = extract_services(emails)
    _confidence_rank = {"HIGH": 3, "MEDIUM": 2, "LOW": 1}
    min_rank = _confidence_rank.get(args.min_confidence, 1)
    services = [s for s in services if _confidence_rank[s["confidence"]] >= min_rank]

    if not services:
        print("No services found matching the confidence threshold. Done.")
        return

    print(f"Found {len(services)} unique services:\n")
    for s in services:
        print(f"  [{s['confidence']:<6}] {s['company_name_raw']:<30} ({s['domain']})")
    print()

    # ── Step 3 & 4: Resolve contacts + compose letters ───────────────────────
    resolver = ContactResolver()
    letters = []
    not_found = []

    for s in services:
        domain = s["domain"]
        name = s["company_name_raw"]
        print(f"Resolving {name} ({domain})...", end=" ", flush=True)
        record = resolver.resolve(domain, name, verbose=False)
        if record:
            print(f"found ({record.source}, {record.contact.preferred_method})")
            letters.append(compose(record, user_identity=user_identity))
        else:
            print("not found — skipping")
            not_found.append(name)

    print(f"\nComposed {len(letters)} letter(s).")
    if not_found:
        print(f"Could not resolve: {', '.join(not_found)}\n")

    if not letters:
        cost_tracker.print_cost_summary()
        return

    # ── Step 5: Preview and send ─────────────────────────────────────────────
    if args.portal_only:
        letters = [l for l in letters if l.method == "portal"]
        if not letters:
            print("No portal companies found.")
            cost_tracker.print_cost_summary()
            return
        print(f"Portal-only mode: {len(letters)} letter(s).\n")

    sent = skipped = 0
    for letter in letters:
        result = preview_and_send(
            letter,
            dry_run=args.dry_run,
            scan_email=email,
            data_dir=data_dir,
            tokens_dir=tokens_dir,
        )
        if result:
            sent += 1
        else:
            skipped += 1

    print(f"\nDone. Sent: {sent}  Skipped: {skipped}")
    cost_tracker.print_cost_summary()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="GDPR SAR agent")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview letters without sending",
    )
    parser.add_argument(
        "--max-emails",
        type=int,
        default=500,
        metavar="N",
        help="Max emails to scan (default 500)",
    )
    parser.add_argument(
        "--min-confidence",
        choices=["HIGH", "MEDIUM", "LOW"],
        default="LOW",
        help="Minimum service detection confidence (default LOW)",
    )
    parser.add_argument(
        "--gmail",
        metavar="EMAIL",
        default=None,
        help="Gmail account to scan (e.g. user@gmail.com)",
    )
    parser.add_argument(
        "--max-llm-calls",
        type=int,
        default=None,
        metavar="N",
        help="Cap LLM API calls this run (0 = block all LLM, omit for unlimited)",
    )
    parser.add_argument(
        "--portal-only",
        action="store_true",
        help="Only process portal-method companies",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
