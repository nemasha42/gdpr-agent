"""Unit tests for run.py orchestration logic."""

from datetime import date
from unittest.mock import MagicMock, patch


from contact_resolver.models import (
    CompanyRecord,
    Contact,
    Flags,
    RequestNotes,
)
from contact_resolver import cost_tracker


TODAY = date.today().isoformat()

_RECORD = CompanyRecord(
    company_name="Spotify",
    source="dataowners_override",
    source_confidence="high",
    last_verified=TODAY,
    contact=Contact(privacy_email="privacy@spotify.com", preferred_method="email"),
    flags=Flags(email_accepted=True),
    request_notes=RequestNotes(),
)

_SERVICES = [
    {"domain": "spotify.com", "company_name_raw": "Spotify", "confidence": "HIGH"},
    {
        "domain": "unknown.example",
        "company_name_raw": "Unknown Co",
        "confidence": "LOW",
    },
]


# ---------------------------------------------------------------------------
# Orchestration: skip if no record, count sent vs skipped
# ---------------------------------------------------------------------------


def test_run_skips_company_when_resolver_returns_none(capsys):
    """Companies where resolve() returns None should be skipped, not cause an error."""
    import run

    mock_resolver = MagicMock()
    mock_resolver.resolve.return_value = None

    with patch("run.ContactResolver", return_value=mock_resolver):
        with patch("run.fetch_emails", return_value=[]):
            with patch("run.extract_services", return_value=_SERVICES[:1]):
                with patch(
                    "run.get_gmail_service",
                    return_value=(MagicMock(), "test@gmail.com"),
                ):
                    with patch("run.preview_and_send", return_value=False):
                        with patch("run.cost_tracker.print_cost_summary"):
                            with patch("run.cost_tracker.set_llm_limit"):
                                import sys

                                with patch.object(sys, "argv", ["run.py", "--dry-run"]):
                                    run.main()

    captured = capsys.readouterr()
    assert "not found" in captured.out


def test_run_reports_sent_and_skipped_counts(capsys):
    """After sending, run.py should print the sent/skipped summary."""
    import run

    mock_resolver = MagicMock()
    mock_resolver.resolve.side_effect = [_RECORD, None]

    mock_letter = MagicMock()
    mock_letter.company_name = "Spotify"
    mock_letter.method = "email"

    with patch("run.ContactResolver", return_value=mock_resolver):
        with patch("run.fetch_emails", return_value=[]):
            with patch("run.extract_services", return_value=_SERVICES):
                with patch(
                    "run.get_gmail_service",
                    return_value=(MagicMock(), "test@gmail.com"),
                ):
                    with patch("run.compose", return_value=mock_letter):
                        with patch("run.preview_and_send", return_value=True):
                            with patch("run.cost_tracker.print_cost_summary"):
                                import sys

                                with patch.object(sys, "argv", ["run.py", "--dry-run"]):
                                    run.main()

    captured = capsys.readouterr()
    assert "Sent:" in captured.out


def test_run_max_llm_calls_flag_sets_limit():
    """--max-llm-calls N should call cost_tracker.set_llm_limit(N)."""
    import run

    mock_resolver = MagicMock()
    mock_resolver.resolve.return_value = None

    with patch("run.ContactResolver", return_value=mock_resolver):
        with patch("run.fetch_emails", return_value=[]):
            with patch("run.extract_services", return_value=[]):
                with patch(
                    "run.get_gmail_service",
                    return_value=(MagicMock(), "test@gmail.com"),
                ):
                    with patch("run.cost_tracker.print_cost_summary"):
                        with patch("run.cost_tracker.set_llm_limit") as mock_limit:
                            import sys

                            with patch.object(
                                sys,
                                "argv",
                                ["run.py", "--dry-run", "--max-llm-calls", "5"],
                            ):
                                run.main()

    mock_limit.assert_called_once_with(5)


def test_run_no_services_exits_cleanly(capsys):
    """When no services are found, run.py should print a message and return."""
    import run

    with patch("run.fetch_emails", return_value=[]):
        with patch("run.extract_services", return_value=[]):
            with patch(
                "run.get_gmail_service", return_value=(MagicMock(), "test@gmail.com")
            ):
                import sys

                with patch.object(sys, "argv", ["run.py", "--dry-run"]):
                    run.main()

    captured = capsys.readouterr()
    assert "No services found" in captured.out


# ---------------------------------------------------------------------------
# --max-llm-calls: resolver respects limit
# ---------------------------------------------------------------------------


def test_resolver_skips_llm_when_limit_reached(tmp_path):
    """When LLM limit is reached, resolver should not fire LLM and return None."""
    from contact_resolver.resolver import ContactResolver

    cost_tracker.reset()
    cost_tracker.set_llm_limit(0)  # 0 calls allowed

    llm_called = False

    def fake_llm(name, domain):
        nonlocal llm_called
        llm_called = True
        return _RECORD

    resolver = ContactResolver(
        db_path=tmp_path / "companies.json",
        dataowners_path=tmp_path / "overrides.json",
        http_get=MagicMock(side_effect=Exception("no network")),
        privacy_scrape=MagicMock(return_value=None),
        llm_search=fake_llm,
    )

    record = resolver.resolve("neverheardof.example", "Unknown")
    assert record is None
    assert not llm_called
    cost_tracker.reset()
