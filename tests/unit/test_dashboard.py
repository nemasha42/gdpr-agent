"""Unit tests for dashboard/app.py Flask routes."""

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

# Ensure project root on path
_ROOT = Path(__file__).parent.parent.parent
sys.path.insert(0, str(_ROOT))


@pytest.fixture
def client(tmp_path):
    """Flask test client with isolated user_data paths."""
    # Write minimal state file
    state_data = {
        "test_at_example_com": {
            "spotify.com": {
                "domain": "spotify.com",
                "company_name": "Spotify",
                "sar_sent_at": "2026-02-01T10:00:00Z",
                "to_email": "privacy@spotify.com",
                "subject": "Subject Access Request",
                "gmail_thread_id": "thread123",
                "deadline": "2026-03-03",
                "replies": [],
                "last_checked": None,
            }
        }
    }
    state_path = tmp_path / "reply_state.json"
    state_path.write_text(json.dumps(state_data))

    cost_data = [
        {
            "timestamp": "2026-02-01T10:00:00",
            "source": "contact_resolver",
            "company_name": "Spotify",
            "model": "claude-haiku-4-5-20251001",
            "input_tokens": 500,
            "output_tokens": 200,
            "cost_usd": 0.0012,
            "found": True,
        }
    ]
    cost_path = tmp_path / "cost_log.json"
    cost_path.write_text(json.dumps(cost_data))

    import dashboard.app as app_module

    app_module._STATE_PATH = state_path
    app_module._USER_DATA = tmp_path

    with patch("dashboard.app.get_log", return_value=[]):
        with patch("contact_resolver.cost_tracker._COST_LOG_PATH", cost_path):
            app_module.app.config["TESTING"] = True
            with app_module.app.test_client() as c:
                yield c


# ---------------------------------------------------------------------------
# Route: GET /
# ---------------------------------------------------------------------------


def test_dashboard_root_returns_200(client):
    resp = client.get("/?account=test@example.com")
    assert resp.status_code == 200


def test_dashboard_root_no_account_returns_200(client):
    resp = client.get("/")
    assert resp.status_code == 200


# ---------------------------------------------------------------------------
# Route: GET /refresh
# ---------------------------------------------------------------------------


def test_refresh_with_no_account_redirects(client):
    resp = client.get("/refresh")
    assert resp.status_code in (302, 200)


def test_refresh_calls_monitor_and_redirects(client):
    with patch("dashboard.app._run_monitor_for_account") as mock_monitor:
        with patch("dashboard.app._reextract_missing_links") as mock_reextract:
            mock_monitor.return_value = None
            mock_reextract.return_value = 0
            resp = client.get("/refresh?account=test@example.com")
            assert resp.status_code == 302
            mock_monitor.assert_called_once_with("test@example.com")


def test_refresh_monitor_exception_does_not_crash(client):
    """Monitor errors should be caught; route still redirects."""
    with patch("dashboard.app._run_monitor_for_account", side_effect=RuntimeError("boom")):
        with patch("dashboard.app._reextract_missing_links", return_value=0):
            resp = client.get("/refresh?account=test@example.com")
            assert resp.status_code == 302


# ---------------------------------------------------------------------------
# Route: GET /company/<domain>
# ---------------------------------------------------------------------------


def test_company_detail_known_domain(client):
    resp = client.get("/company/spotify.com?account=test@example.com")
    assert resp.status_code == 200


def test_company_detail_unknown_domain(client):
    resp = client.get("/company/unknown.com?account=test@example.com")
    assert resp.status_code == 404
