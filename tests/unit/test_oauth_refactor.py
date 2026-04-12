"""Tests for tokens_dir parameter on Gmail OAuth functions."""

from pathlib import Path
from unittest.mock import patch, MagicMock
import json


def test_get_gmail_service_uses_tokens_dir(tmp_path):
    from auth.gmail_oauth import get_gmail_service, _safe_email

    tokens_dir = tmp_path / "tokens"
    tokens_dir.mkdir()
    safe = _safe_email("alice@gmail.com")
    token_file = tokens_dir / f"{safe}_readonly.json"
    token_file.write_text(json.dumps({
        "token": "fake-token",
        "refresh_token": "fake-refresh",
        "client_id": "fake-client",
        "client_secret": "fake-secret",
        "token_uri": "https://oauth2.googleapis.com/token",
        "scopes": ["https://www.googleapis.com/auth/gmail.readonly"],
    }))

    with patch("auth.gmail_oauth.build") as mock_build, \
         patch("auth.gmail_oauth.Credentials") as mock_creds_cls:
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
    token_file.write_text(json.dumps({
        "token": "fake-token",
        "refresh_token": "fake-refresh",
        "client_id": "fake-client",
        "client_secret": "fake-secret",
        "token_uri": "https://oauth2.googleapis.com/token",
        "scopes": ["https://www.googleapis.com/auth/gmail.send"],
    }))

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
    token_file.write_text(json.dumps({
        "token": "fake-token",
        "refresh_token": "fake-refresh",
        "client_id": "fake-client",
        "client_secret": "fake-secret",
        "token_uri": "https://oauth2.googleapis.com/token",
        "scopes": ["https://www.googleapis.com/auth/gmail.send"],
    }))

    with patch("auth.gmail_oauth.build") as mock_build, \
         patch("auth.gmail_oauth.Credentials") as mock_creds_cls:
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
