"""Unit tests for letter_engine: composer, tracker, sender."""

import json
from pathlib import Path
from unittest.mock import patch

import pytest

from contact_resolver.models import (
    CompanyRecord,
    Contact,
    Flags,
    PostalAddress,
    RequestNotes,
)
from letter_engine.composer import compose
from letter_engine.models import SARLetter
from letter_engine import tracker


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

def _make_record(
    *,
    company_name: str = "Acme Corp",
    dpo_email: str = "dpo@acme.com",
    privacy_email: str = "",
    portal_url: str = "",
    preferred_method: str = "email",
    postal: PostalAddress | None = None,
) -> CompanyRecord:
    return CompanyRecord(
        company_name=company_name,
        source="llm_search",
        source_confidence="high",
        last_verified="2026-03-16",
        contact=Contact(
            dpo_email=dpo_email,
            privacy_email=privacy_email,
            gdpr_portal_url=portal_url,
            postal_address=postal or PostalAddress(),
            preferred_method=preferred_method,  # type: ignore[arg-type]
        ),
        flags=Flags(),
        request_notes=RequestNotes(),
    )


_MOCK_SETTINGS = {
    "user_full_name": "Jane Doe",
    "user_email": "jane@example.com",
    "user_address_line1": "10 Test Road",
    "user_address_city": "London",
    "user_address_postcode": "SW1A 1AA",
    "user_address_country": "United Kingdom",
    "gdpr_framework": "UK GDPR",
}


# ---------------------------------------------------------------------------
# composer
# ---------------------------------------------------------------------------

class TestCompose:
    def test_email_method_sets_correct_fields(self) -> None:
        record = _make_record(dpo_email="dpo@acme.com", preferred_method="email")
        with patch("letter_engine.composer.settings", **_MOCK_SETTINGS):
            letter = compose(record)

        assert letter.method == "email"
        assert letter.to_email == "dpo@acme.com"
        assert letter.subject == "Subject Access Request — Jane Doe"
        assert "UK GDPR" in letter.body
        assert "Jane Doe" in letter.body
        assert "jane@example.com" in letter.body

    def test_email_prefers_dpo_over_privacy(self) -> None:
        record = _make_record(dpo_email="dpo@acme.com", privacy_email="privacy@acme.com")
        with patch("letter_engine.composer.settings", **_MOCK_SETTINGS):
            letter = compose(record)
        assert letter.to_email == "dpo@acme.com"

    def test_email_falls_back_to_privacy_email(self) -> None:
        record = _make_record(dpo_email="", privacy_email="privacy@acme.com")
        with patch("letter_engine.composer.settings", **_MOCK_SETTINGS):
            letter = compose(record)
        assert letter.to_email == "privacy@acme.com"

    def test_portal_method(self) -> None:
        record = _make_record(
            portal_url="https://privacy.acme.com/sar",
            preferred_method="portal",
        )
        with patch("letter_engine.composer.settings", **_MOCK_SETTINGS):
            letter = compose(record)

        assert letter.method == "portal"
        assert letter.portal_url == "https://privacy.acme.com/sar"
        assert "UK GDPR" in letter.body

    def test_postal_method_uses_postal_template(self) -> None:
        postal = PostalAddress(
            line1="1 Corp Street", city="Manchester", postcode="M1 1AA", country="UK"
        )
        record = _make_record(preferred_method="postal", postal=postal)
        with patch("letter_engine.composer.settings", **_MOCK_SETTINGS):
            letter = compose(record)

        assert letter.method == "postal"
        assert "10 Test Road" in letter.body  # user address block
        assert "1 Corp Street" in letter.body  # company address block

    def test_postal_address_formatted(self) -> None:
        postal = PostalAddress(line1="5 Lane", city="Bristol", postcode="BS1", country="UK")
        record = _make_record(preferred_method="postal", postal=postal)
        with patch("letter_engine.composer.settings", **_MOCK_SETTINGS):
            letter = compose(record)
        assert "5 Lane" in letter.postal_address
        assert "Bristol" in letter.postal_address

    def test_missing_postal_address_shows_placeholder(self) -> None:
        record = _make_record(preferred_method="postal")
        with patch("letter_engine.composer.settings", **_MOCK_SETTINGS):
            letter = compose(record)
        assert "(address not available)" in letter.postal_address


# ---------------------------------------------------------------------------
# tracker
# ---------------------------------------------------------------------------

class TestTracker:
    def test_empty_log_when_no_file(self, tmp_path: Path) -> None:
        assert tracker.get_log(path=tmp_path / "missing.json") == []

    def test_record_and_retrieve(self, tmp_path: Path) -> None:
        path = tmp_path / "sent.json"
        letter = SARLetter(
            company_name="Acme",
            method="email",
            to_email="dpo@acme.com",
            subject="SAR",
            body="Dear ...",
            portal_url="",
            postal_address="",
        )
        tracker.record_sent(letter, path=path)
        log = tracker.get_log(path=path)

        assert len(log) == 1
        assert log[0]["company_name"] == "Acme"
        assert log[0]["method"] == "email"
        assert log[0]["to_email"] == "dpo@acme.com"
        assert "sent_at" in log[0]

    def test_multiple_records_appended(self, tmp_path: Path) -> None:
        path = tmp_path / "sent.json"
        for name in ("Alpha", "Beta", "Gamma"):
            letter = SARLetter(
                company_name=name, method="email", to_email="x@x.com",
                subject="SAR", body="...", portal_url="", postal_address="",
            )
            tracker.record_sent(letter, path=path)

        log = tracker.get_log(path=path)
        assert [e["company_name"] for e in log] == ["Alpha", "Beta", "Gamma"]

    def test_corrupted_file_returns_empty(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.json"
        path.write_text("not json at all")
        assert tracker.get_log(path=path) == []


# ---------------------------------------------------------------------------
# sender
# ---------------------------------------------------------------------------

class TestSender:
    def _make_letter(self, method: str = "email") -> SARLetter:
        return SARLetter(
            company_name="Acme",
            method=method,  # type: ignore[arg-type]
            to_email="dpo@acme.com" if method == "email" else "",
            subject="Subject Access Request",
            body="Dear DPO, ...",
            portal_url="https://acme.com/sar" if method == "portal" else "",
            postal_address="1 Street\nLondon" if method == "postal" else "",
        )

    def test_returns_false_on_no(self, capsys: pytest.CaptureFixture) -> None:
        from letter_engine.sender import preview_and_send
        with patch("builtins.input", return_value="n"):
            result = preview_and_send(self._make_letter())
        assert result is False

    def test_returns_false_on_empty_input(self) -> None:
        from letter_engine.sender import preview_and_send
        with patch("builtins.input", return_value=""):
            result = preview_and_send(self._make_letter())
        assert result is False

    def test_dry_run_returns_true_without_sending(self) -> None:
        from letter_engine.sender import preview_and_send
        with patch("builtins.input", return_value="y"):
            result = preview_and_send(self._make_letter(), dry_run=True)
        assert result is True

    def test_eof_returns_false(self) -> None:
        from letter_engine.sender import preview_and_send
        with patch("builtins.input", side_effect=EOFError):
            result = preview_and_send(self._make_letter())
        assert result is False

    def test_portal_dry_run(self) -> None:
        from letter_engine.sender import preview_and_send
        with patch("builtins.input", return_value="y"):
            result = preview_and_send(self._make_letter("portal"), dry_run=True)
        assert result is True

    def test_postal_dry_run(self) -> None:
        from letter_engine.sender import preview_and_send
        with patch("builtins.input", return_value="y"):
            result = preview_and_send(self._make_letter("postal"), dry_run=True)
        assert result is True
