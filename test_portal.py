"""Manual test script for portal submission.

Usage:
    python test_portal.py --list-portals              # show portal companies
    python test_portal.py --domain glassdoor.com --dry-run   # analyze form only
    python test_portal.py --domain glassdoor.com              # full submission
"""

import argparse
import json
import sys
from pathlib import Path

from contact_resolver.resolver import ContactResolver
from letter_engine.composer import compose
from portal_submitter.platform_hints import detect_platform


def main() -> None:
    args = _parse_args()

    if args.list_portals:
        _list_portals()
        return

    if not args.domain:
        print("Provide --domain or --list-portals")
        sys.exit(1)

    resolver = ContactResolver()
    record = resolver.resolve(args.domain, args.domain, verbose=True)
    if not record:
        print(f"Could not resolve {args.domain}")
        sys.exit(1)

    print(f"\nCompany: {record.company_name}")
    print(f"Method: {record.contact.preferred_method}")
    print(f"Portal URL: {record.contact.gdpr_portal_url}")
    print(f"Platform: {detect_platform(record.contact.gdpr_portal_url)}")

    if record.contact.preferred_method != "portal":
        print(f"\nNote: {args.domain} uses method={record.contact.preferred_method}, not portal.")
        if not args.force:
            print("Use --force to test anyway.")
            return

    letter = compose(record)

    if args.dry_run:
        from portal_submitter import submit_portal
        result = submit_portal(letter, scan_email=args.gmail or "", dry_run=True)
        print(f"\nDry run result: {result}")
        return

    from portal_submitter import submit_portal
    print(f"\nSubmitting to {record.contact.gdpr_portal_url}...")
    result = submit_portal(letter, scan_email=args.gmail or "")
    print(f"\nResult:")
    print(f"  Success: {result.success}")
    print(f"  Status: {result.portal_status}")
    print(f"  Confirmation: {result.confirmation_ref}")
    print(f"  Screenshot: {result.screenshot_path}")
    if result.error:
        print(f"  Error: {result.error}")
    if result.needs_manual:
        print(f"  Manual submission required at: {letter.portal_url}")


def _list_portals() -> None:
    db_path = Path(__file__).parent / "data" / "companies.json"
    if not db_path.exists():
        print("data/companies.json not found.")
        return
    db = json.loads(db_path.read_text())
    companies = db.get("companies", {})

    portal_companies = [
        (domain, rec)
        for domain, rec in companies.items()
        if rec.get("contact", {}).get("preferred_method") == "portal"
        or rec.get("contact", {}).get("gdpr_portal_url")
    ]

    if not portal_companies:
        print("No portal companies found.")
        return

    print(f"Portal companies ({len(portal_companies)}):\n")
    for domain, rec in sorted(portal_companies):
        contact = rec.get("contact", {})
        method = contact.get("preferred_method", "?")
        url = contact.get("gdpr_portal_url", "")
        platform = detect_platform(url) if url else "?"
        print(f"  {domain:<30} method={method:<8} platform={platform:<15} {url}")


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Test portal submission")
    parser.add_argument("--domain", help="Domain to test")
    parser.add_argument("--dry-run", action="store_true", help="Analyze form only")
    parser.add_argument("--list-portals", action="store_true", help="List portal companies")
    parser.add_argument("--gmail", help="Gmail account for OTP monitoring")
    parser.add_argument("--force", action="store_true", help="Force portal test even if method != portal")
    return parser.parse_args()


if __name__ == "__main__":
    main()
