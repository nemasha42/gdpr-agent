"""Phase 4 smoke test — compose SAR letters and preview them (dry run).

Run with:
    python test_phase4.py

For each test company you will see a formatted preview and be asked Y/N.
Answering 'y' confirms the letter but does NOT actually send it (dry_run=True).
"""

from contact_resolver.models import (
    CompanyRecord,
    Contact,
    Flags,
    PostalAddress,
    RequestNotes,
)
from letter_engine.composer import compose
from letter_engine.sender import preview_and_send

# ---------------------------------------------------------------------------
# Fake records covering all three delivery methods
# ---------------------------------------------------------------------------

_EMAIL_RECORD = CompanyRecord(
    company_name="Glassdoor",
    legal_entity_name="Glassdoor, Inc.",
    source="llm_search",
    source_confidence="high",
    last_verified="2026-03-16",
    contact=Contact(
        dpo_email="",
        privacy_email="privacy@glassdoor.com",
        gdpr_portal_url="",
        preferred_method="email",
    ),
    flags=Flags(email_accepted=True),
    request_notes=RequestNotes(),
)

_PORTAL_RECORD = CompanyRecord(
    company_name="Google",
    legal_entity_name="Google LLC",
    source="datarequests",
    source_confidence="high",
    last_verified="2026-03-16",
    contact=Contact(
        dpo_email="",
        privacy_email="",
        gdpr_portal_url="https://myaccount.google.com/data-and-privacy",
        preferred_method="portal",
    ),
    flags=Flags(portal_only=True, email_accepted=False),
    request_notes=RequestNotes(),
)

_POSTAL_RECORD = CompanyRecord(
    company_name="Acme Bank",
    legal_entity_name="Acme Bank plc",
    source="user_manual",
    source_confidence="high",
    last_verified="2026-03-16",
    contact=Contact(
        dpo_email="",
        privacy_email="",
        gdpr_portal_url="",
        postal_address=PostalAddress(
            line1="1 Finance Street",
            city="London",
            postcode="EC2V 8RF",
            country="United Kingdom",
        ),
        preferred_method="postal",
    ),
    flags=Flags(email_accepted=False),
    request_notes=RequestNotes(),
)

# ---------------------------------------------------------------------------
# Run
# ---------------------------------------------------------------------------

test_records = [_EMAIL_RECORD, _PORTAL_RECORD, _POSTAL_RECORD]

import sys
DRY_RUN = "--dry-run" in sys.argv

for record in test_records:
    letter = compose(record)
    result = preview_and_send(letter, dry_run=DRY_RUN)
    print(f"→ {'Approved' if result else 'Skipped'}\n")
