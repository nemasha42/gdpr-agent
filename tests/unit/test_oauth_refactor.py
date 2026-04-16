"""Tests for tokens_dir parameter, caching, and OAuth logging."""

from pathlib import Path
from unittest.mock import patch, MagicMock
import json
import pytest


def test_get_gmail_service_uses_tokens_dir(tmp_path):
    from auth.gmail_oauth import get_gmail_service, _safe_email

    tokens_dir = tmp_path / "tokens"
    tokens_dir.mkdir()
    safe = _safe_email("alice@gmail.com")
    token_file = tokens_dir / f"{safe}_readonly.json"
    token_file.write_text(
        json.dumps(
            {
                "token": "fake-token",
                "refresh_token": "fake-refresh",
                "client_id": "fake-client",
                "client_secret": "fake-secret",
                "token_uri": "https://oauth2.googleapis.com/token",
                "scopes": ["https://www.googleapis.com/auth/gmail.readonly"],
            }
        )
    )

    with patch("auth.gmail_oauth.build") as mock_build, patch(
        "auth.gmail_oauth.Credentials"
    ) as mock_creds_cls:
        mock_creds = MagicMock()
        mock_creds.valid = True
        mock_creds.expired = False
        mock_creds.to_json.return_value = '{"token": "refreshed"}'
        mock_creds_cls.from_authorized_user_file.return_value = mock_creds

        service, email = get_gmail_service(
            email_hint="alice@gmail.com",
            tokens_dir=tokens_dir,
        )

        mock_creds_cls.from_authorized_user_file.assert_called_once()
        call_path = mock_creds_cls.from_authorized_user_file.call_args[0][0]
        assert str(tokens_dir) in call_path


def test_check_send_token_valid_uses_tokens_dir(tmp_path):
    from auth.gmail_oauth import check_send_token_valid, _safe_email

    tokens_dir = tmp_path / "tokens"
    tokens_dir.mkdir()
    safe = _safe_email("bob@gmail.com")
    token_file = tokens_dir / f"{safe}_send.json"
    token_file.write_text(
        json.dumps(
            {
                "token": "fake-token",
                "refresh_token": "fake-refresh",
                "client_id": "fake-client",
                "client_secret": "fake-secret",
                "token_uri": "https://oauth2.googleapis.com/token",
                "scopes": ["https://www.googleapis.com/auth/gmail.send"],
            }
        )
    )

    with patch("auth.gmail_oauth.Credentials") as mock_creds_cls:
        mock_creds = MagicMock()
        mock_creds.valid = True
        mock_creds.expired = False
        mock_creds_cls.from_authorized_user_file.return_value = mock_creds

        is_valid, err = check_send_token_valid("bob@gmail.com", tokens_dir=tokens_dir)

        assert is_valid is True
        assert err == ""
        call_path = mock_creds_cls.from_authorized_user_file.call_args[0][0]
        assert str(tokens_dir) in call_path


def test_check_send_token_missing_in_custom_dir(tmp_path):
    from auth.gmail_oauth import check_send_token_valid

    tokens_dir = tmp_path / "tokens"
    tokens_dir.mkdir()

    is_valid, err = check_send_token_valid("nobody@gmail.com", tokens_dir=tokens_dir)

    assert is_valid is False
    assert "No send token found" in err


def test_get_gmail_send_service_uses_tokens_dir(tmp_path):
    from auth.gmail_oauth import get_gmail_send_service, _safe_email

    tokens_dir = tmp_path / "tokens"
    tokens_dir.mkdir()
    safe = _safe_email("carol@gmail.com")
    token_file = tokens_dir / f"{safe}_send.json"
    token_file.write_text(
        json.dumps(
            {
                "token": "fake-token",
                "refresh_token": "fake-refresh",
                "client_id": "fake-client",
                "client_secret": "fake-secret",
                "token_uri": "https://oauth2.googleapis.com/token",
                "scopes": ["https://www.googleapis.com/auth/gmail.send"],
            }
        )
    )

    with patch("auth.gmail_oauth.build") as mock_build, patch(
        "auth.gmail_oauth.Credentials"
    ) as mock_creds_cls:
        mock_creds = MagicMock()
        mock_creds.valid = True
        mock_creds.expired = False
        mock_creds_cls.from_authorized_user_file.return_value = mock_creds

        service = get_gmail_send_service(
            "carol@gmail.com",
            tokens_dir=tokens_dir,
        )

        mock_creds_cls.from_authorized_user_file.assert_called_once()
        call_path = mock_creds_cls.from_authorized_user_file.call_args[0][0]
        assert str(tokens_dir) in call_path


def test_default_tokens_dir_unchanged():
    """Verify that omitting tokens_dir still uses the module-level default."""
    from auth.gmail_oauth import _TOKENS_DIR, get_gmail_service
    import inspect

    sig = inspect.signature(get_gmail_service)
    default = sig.parameters["tokens_dir"].default
    assert default == _TOKENS_DIR


# ---------------------------------------------------------------------------
# Service cache tests
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _clear_cache():
    """Clear the service cache before and after each test."""
    from auth.gmail_oauth import clear_service_cache

    clear_service_cache()
    yield
    clear_service_cache()


def test_cache_hit_skips_disk_load(tmp_path):
    """Second call with same email_hint returns cached service without loading creds."""
    from auth.gmail_oauth import get_gmail_service, _safe_email

    tokens_dir = tmp_path / "tokens"
    tokens_dir.mkdir()
    safe = _safe_email("alice@gmail.com")
    token_file = tokens_dir / f"{safe}_readonly.json"
    token_file.write_text(
        json.dumps(
            {
                "token": "fake-token",
                "refresh_token": "fake-refresh",
                "client_id": "fake-client",
                "client_secret": "fake-secret",
                "token_uri": "https://oauth2.googleapis.com/token",
                "scopes": ["https://www.googleapis.com/auth/gmail.readonly"],
            }
        )
    )

    with patch("auth.gmail_oauth.build") as mock_build, patch(
        "auth.gmail_oauth.Credentials"
    ) as mock_creds_cls:
        mock_creds = MagicMock()
        mock_creds.valid = True
        mock_creds.expired = False
        mock_creds.to_json.return_value = '{"token": "refreshed"}'
        mock_creds_cls.from_authorized_user_file.return_value = mock_creds

        # First call — populates cache
        svc1, email1 = get_gmail_service(
            email_hint="alice@gmail.com", tokens_dir=tokens_dir
        )

        # Second call — should hit cache
        svc2, email2 = get_gmail_service(
            email_hint="alice@gmail.com", tokens_dir=tokens_dir
        )

        assert svc1 is svc2
        assert email1 == email2
        # Credentials loaded only once (first call), not twice
        mock_creds_cls.from_authorized_user_file.assert_called_once()


def test_cache_miss_different_email(tmp_path):
    """Different email_hints produce separate cache entries."""
    from auth.gmail_oauth import get_gmail_service, _safe_email

    tokens_dir = tmp_path / "tokens"
    tokens_dir.mkdir()
    for addr in ("alice@gmail.com", "bob@gmail.com"):
        safe = _safe_email(addr)
        token_file = tokens_dir / f"{safe}_readonly.json"
        token_file.write_text(
            json.dumps(
                {
                    "token": "fake-token",
                    "refresh_token": "fake-refresh",
                    "client_id": "fake-client",
                    "client_secret": "fake-secret",
                    "token_uri": "https://oauth2.googleapis.com/token",
                    "scopes": ["https://www.googleapis.com/auth/gmail.readonly"],
                }
            )
        )

    with patch("auth.gmail_oauth.build") as mock_build, patch(
        "auth.gmail_oauth.Credentials"
    ) as mock_creds_cls:
        mock_creds = MagicMock()
        mock_creds.valid = True
        mock_creds.expired = False
        mock_creds.to_json.return_value = '{"token": "refreshed"}'
        mock_creds_cls.from_authorized_user_file.return_value = mock_creds

        svc1, _ = get_gmail_service(email_hint="alice@gmail.com", tokens_dir=tokens_dir)
        svc2, _ = get_gmail_service(email_hint="bob@gmail.com", tokens_dir=tokens_dir)

        # build() called twice — different cache keys
        assert mock_build.call_count == 2


def test_clear_service_cache():
    """clear_service_cache() empties the cache dict."""
    from auth.gmail_oauth import _service_cache, _cache_put, clear_service_cache

    _cache_put("a@b.com", "readonly", Path("/tmp"), MagicMock(), "a@b.com")
    assert len(_service_cache) == 1
    clear_service_cache()
    assert len(_service_cache) == 0


def test_skip_get_profile_when_email_hint_and_cached_creds(tmp_path):
    """When email_hint is given and creds load from disk, _get_account_email is skipped."""
    from auth.gmail_oauth import get_gmail_service, _safe_email

    tokens_dir = tmp_path / "tokens"
    tokens_dir.mkdir()
    safe = _safe_email("user@gmail.com")
    token_file = tokens_dir / f"{safe}_readonly.json"
    token_file.write_text(
        json.dumps(
            {
                "token": "fake-token",
                "refresh_token": "fake-refresh",
                "client_id": "fake-client",
                "client_secret": "fake-secret",
                "token_uri": "https://oauth2.googleapis.com/token",
                "scopes": ["https://www.googleapis.com/auth/gmail.readonly"],
            }
        )
    )

    with patch("auth.gmail_oauth.build") as mock_build, patch(
        "auth.gmail_oauth.Credentials"
    ) as mock_creds_cls, patch("auth.gmail_oauth._get_account_email") as mock_get_email:
        mock_creds = MagicMock()
        mock_creds.valid = True
        mock_creds.expired = False
        mock_creds.to_json.return_value = '{"token": "refreshed"}'
        mock_creds_cls.from_authorized_user_file.return_value = mock_creds

        service, email = get_gmail_service(
            email_hint="user@gmail.com", tokens_dir=tokens_dir
        )

        # _get_account_email should NOT be called — we trust the email_hint
        mock_get_email.assert_not_called()
        assert email == "user@gmail.com"


# ---------------------------------------------------------------------------
# OAuth call logger tests
# ---------------------------------------------------------------------------


def test_oauth_log_written(tmp_path):
    """OAuth calls produce a log entry in the log file."""
    from auth.gmail_oauth import (
        get_gmail_service,
        _safe_email,
    )
    import auth.gmail_oauth as oauth_mod

    tokens_dir = tmp_path / "tokens"
    tokens_dir.mkdir()
    safe = _safe_email("logger@gmail.com")
    token_file = tokens_dir / f"{safe}_readonly.json"
    token_file.write_text(
        json.dumps(
            {
                "token": "fake-token",
                "refresh_token": "fake-refresh",
                "client_id": "fake-client",
                "client_secret": "fake-secret",
                "token_uri": "https://oauth2.googleapis.com/token",
                "scopes": ["https://www.googleapis.com/auth/gmail.readonly"],
            }
        )
    )

    log_file = tmp_path / "oauth_calls.log"

    with patch("auth.gmail_oauth.build") as mock_build, patch(
        "auth.gmail_oauth.Credentials"
    ) as mock_creds_cls, patch.object(oauth_mod, "_LOG_PATH", log_file), patch.object(
        oauth_mod, "_counter_loaded", False
    ), patch.object(oauth_mod, "_call_counter", 0):
        mock_creds = MagicMock()
        mock_creds.valid = True
        mock_creds.expired = False
        mock_creds.to_json.return_value = '{"token": "refreshed"}'
        mock_creds_cls.from_authorized_user_file.return_value = mock_creds

        get_gmail_service(email_hint="logger@gmail.com", tokens_dir=tokens_dir)

    assert log_file.exists()
    lines = log_file.read_text().strip().split("\n")
    assert len(lines) >= 1
    parts = lines[0].split("\t")
    assert parts[0] == "1"  # counter
    assert parts[2] == "get_gmail_service"  # function
    assert "logger@gmail.com" in parts[4]  # user


def test_send_cache_hit(tmp_path):
    """get_gmail_send_service returns cached service on second call."""
    from auth.gmail_oauth import get_gmail_send_service, _safe_email

    tokens_dir = tmp_path / "tokens"
    tokens_dir.mkdir()
    safe = _safe_email("sender@gmail.com")
    token_file = tokens_dir / f"{safe}_send.json"
    token_file.write_text(
        json.dumps(
            {
                "token": "fake-token",
                "refresh_token": "fake-refresh",
                "client_id": "fake-client",
                "client_secret": "fake-secret",
                "token_uri": "https://oauth2.googleapis.com/token",
                "scopes": ["https://www.googleapis.com/auth/gmail.send"],
            }
        )
    )

    with patch("auth.gmail_oauth.build") as mock_build, patch(
        "auth.gmail_oauth.Credentials"
    ) as mock_creds_cls:
        mock_creds = MagicMock()
        mock_creds.valid = True
        mock_creds.expired = False
        mock_creds_cls.from_authorized_user_file.return_value = mock_creds

        svc1 = get_gmail_send_service("sender@gmail.com", tokens_dir=tokens_dir)
        svc2 = get_gmail_send_service("sender@gmail.com", tokens_dir=tokens_dir)

        assert svc1 is svc2
        mock_build.assert_called_once()
