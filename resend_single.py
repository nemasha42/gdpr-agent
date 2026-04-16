"""Resend a SAR to a single company by domain, using the cached companies.json record.

Usage:
    python resend_single.py reflexivity.com
    python resend_single.py reflexivity.com --dry-run
    python resend_single.py reflexivity.com --gmail user@gmail.com
"""

import argparse
import json
import sys
from pathlib import Path

from contact_resolver.models import CompanyRecord
from letter_engine.composer import compose
from letter_engine.sender import preview_and_send

_DB_PATH = Path(__file__).parent / "data" / "companies.json"


def main() -> None:
    parser = argparse.ArgumentParser(description="Resend a SAR for a single domain")
    parser.add_argument(
        "domain", help="Domain key in companies.json (e.g. reflexivity.com)"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Preview only, do not send"
    )
    parser.add_argument(
        "--gmail", metavar="EMAIL", default="", help="Gmail send account"
    )
    args = parser.parse_args()

    raw = json.loads(_DB_PATH.read_text())
    companies = raw.get("companies", raw)

    if args.domain not in companies:
        print(f"ERROR: '{args.domain}' not found in companies.json.")
        print("Available domains:", ", ".join(sorted(companies.keys())))
        sys.exit(1)

    record = CompanyRecord.model_validate(companies[args.domain])
    print(f"Loaded record for {args.domain}:")
    print(f"  company_name  : {record.company_name}")
    print(f"  privacy_email : {record.contact.privacy_email}")
    print(f"  dpo_email     : {record.contact.dpo_email}")
    print()

    letter = compose(record)
    preview_and_send(letter, dry_run=args.dry_run, scan_email=args.gmail)


if __name__ == "__main__":
    main()
