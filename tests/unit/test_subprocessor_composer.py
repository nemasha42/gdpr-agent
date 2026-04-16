"""Unit tests for compose_subprocessor_request() in letter_engine/composer.py."""

from datetime import date, timedelta


from contact_resolver.models import CompanyRecord, Contact, PostalAddress
from letter_engine.composer import compose_subprocessor_request


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_record(
    company_name: str = "Acme Corp",
    privacy_email: str = "privacy@acme.com",
    dpo_email: str = "",
    preferred_method: str = "email",
    postal_address: PostalAddress | None = None,
) -> CompanyRecord:
    return CompanyRecord(
        company_name=company_name,
        source="llm_search",
        source_confidence="medium",
        last_verified=date.today().isoformat(),
        contact=Contact(
            privacy_email=privacy_email,
            dpo_email=dpo_email,
            preferred_method=preferred_method,
            postal_address=postal_address or PostalAddress(),
        ),
    )


_USER_IDENTITY = {
    "user_full_name": "Jane Smith",
    "user_email": "jane@example.com",
    "user_address_line1": "1 Test Street",
    "user_address_city": "London",
    "user_address_postcode": "EC1A 1AA",
    "user_address_country": "United Kingdom",
    "gdpr_framework": "UK GDPR",
}


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


def test_compose_returns_sar_letter_with_correct_subject():
    letter = compose_subprocessor_request(_make_record(), user_identity=_USER_IDENTITY)
    assert letter is not None
    assert "Subprocessor Disclosure Request" in letter.subject
    assert "Jane Smith" in letter.subject


def test_compose_uses_privacy_email():
    letter = compose_subprocessor_request(
        _make_record(privacy_email="privacy@acme.com"), user_identity=_USER_IDENTITY
    )
    assert letter.to_email == "privacy@acme.com"


def test_compose_falls_back_to_dpo_email():
    letter = compose_subprocessor_request(
        _make_record(privacy_email="", dpo_email="dpo@acme.com"),
        user_identity=_USER_IDENTITY,
    )
    assert letter.to_email == "dpo@acme.com"


def test_compose_no_contact_returns_none():
    result = compose_subprocessor_request(
        _make_record(privacy_email="", dpo_email="", preferred_method="email"),
        user_identity=_USER_IDENTITY,
    )
    assert result is None


def test_compose_postal_method_uses_postal_template():
    record = _make_record(
        privacy_email="",
        dpo_email="",
        preferred_method="postal",
        postal_address=PostalAddress(
            line1="123 Corp Lane", city="Berlin", postcode="10115", country="Germany"
        ),
    )
    letter = compose_subprocessor_request(record, user_identity=_USER_IDENTITY)
    assert letter is not None
    assert letter.method == "postal"
    # Postal template includes user address header
    assert "1 Test Street" in letter.body


def test_deadline_is_30_days_from_today():
    letter = compose_subprocessor_request(_make_record(), user_identity=_USER_IDENTITY)
    expected = (date.today() + timedelta(days=30)).strftime("%d %B %Y")
    assert expected in letter.body


def test_body_contains_four_disclosure_categories():
    letter = compose_subprocessor_request(_make_record(), user_identity=_USER_IDENTITY)
    body = letter.body
    assert "DATA PROCESSORS AND SUB-PROCESSORS" in body
    assert "ARTIFICIAL INTELLIGENCE AND MACHINE LEARNING" in body
    assert "DATA BROKERS" in body
    assert "ADVERTISING" in body


def test_body_cites_cjeu_case():
    letter = compose_subprocessor_request(_make_record(), user_identity=_USER_IDENTITY)
    assert "C-154/21" in letter.body


def test_body_cites_edpb_opinion():
    letter = compose_subprocessor_request(_make_record(), user_identity=_USER_IDENTITY)
    assert "EDPB Opinion 22/2024" in letter.body


def test_body_contains_company_name():
    letter = compose_subprocessor_request(
        _make_record(company_name="Figma Inc"), user_identity=_USER_IDENTITY
    )
    assert "Figma Inc" in letter.body


def test_body_contains_user_email():
    letter = compose_subprocessor_request(_make_record(), user_identity=_USER_IDENTITY)
    assert "jane@example.com" in letter.body
