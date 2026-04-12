"""Tests for dashboard.user_model — User class, persistence, invite tokens."""

import json
from pathlib import Path

import pytest

from dashboard.user_model import (
    User,
    _safe_email,
    _safe_email_to_address,
    delete_user,
    generate_invite_token,
    load_user,
    load_users,
    save_user,
    user_data_dir,
    validate_invite_token,
)


def test_safe_email():
    assert _safe_email("alice@example.com") == "alice_at_example_com"
    assert _safe_email("bob.jones@sub.domain.co.uk") == "bob_jones_at_sub_domain_co_uk"


def test_safe_email_roundtrip():
    safe = _safe_email("alice@example.com")
    assert _safe_email_to_address(safe) == "alice@example.com"


def test_safe_email_to_address_no_at():
    # Best-effort: if no _at_ separator, return input unchanged
    assert _safe_email_to_address("noemailhere") == "noemailhere"


def test_user_data_dir(tmp_path):
    d = user_data_dir("alice@example.com", root=tmp_path)
    assert d == (tmp_path / "alice_at_example_com").resolve()
    assert d.is_relative_to(tmp_path.resolve())


def test_user_data_dir_traversal_rejected(tmp_path):
    # _safe_email replaces "." with "_", so ".." becomes "__" (not traversal).
    # Patch _safe_email to simulate a bypass and verify the resolve() guard catches it.
    from unittest.mock import patch

    with patch("dashboard.user_model._safe_email", return_value="../../etc/passwd"):
        with pytest.raises(ValueError, match="Path traversal"):
            user_data_dir("anything", root=tmp_path)


def test_load_users_empty(tmp_path):
    users_path = tmp_path / "users.json"
    assert load_users(path=users_path) == {}


def test_save_and_load_user(tmp_path):
    users_path = tmp_path / "users.json"
    user = User(email="alice@example.com", name="Alice", role="admin")
    save_user(user, path=users_path)

    data = load_users(path=users_path)
    assert "alice@example.com" in data
    assert data["alice@example.com"]["name"] == "Alice"
    assert data["alice@example.com"]["role"] == "admin"
    assert "created_at" in data["alice@example.com"]


def test_save_user_updates_existing(tmp_path):
    users_path = tmp_path / "users.json"
    user = User(email="alice@example.com", name="Alice", role="user")
    save_user(user, path=users_path)

    # Update name and role
    user2 = User(email="alice@example.com", name="Alice Smith", role="admin")
    save_user(user2, path=users_path)

    data = load_users(path=users_path)
    assert data["alice@example.com"]["name"] == "Alice Smith"
    assert data["alice@example.com"]["role"] == "admin"


def test_load_user_by_email(tmp_path):
    users_path = tmp_path / "users.json"
    user = User(email="bob@test.org", name="Bob", role="user")
    save_user(user, path=users_path)

    loaded = load_user("bob@test.org", path=users_path)
    assert loaded is not None
    assert loaded.email == "bob@test.org"
    assert loaded.name == "Bob"
    assert loaded.role == "user"
    assert loaded.is_admin is False


def test_load_user_missing(tmp_path):
    users_path = tmp_path / "users.json"
    assert load_user("nobody@nowhere.com", path=users_path) is None


def test_delete_user(tmp_path):
    users_path = tmp_path / "users.json"
    user = User(email="alice@example.com", name="Alice")
    save_user(user, path=users_path)

    assert delete_user("alice@example.com", path=users_path) is True
    assert load_user("alice@example.com", path=users_path) is None


def test_delete_user_missing(tmp_path):
    users_path = tmp_path / "users.json"
    assert delete_user("nobody@nowhere.com", path=users_path) is False


def test_generate_invite_token():
    token = generate_invite_token("alice@example.com", secret_key="test-secret")
    assert isinstance(token, str)
    assert len(token) > 10

    # Token should validate with the same key
    email = validate_invite_token(token, secret_key="test-secret")
    assert email == "alice@example.com"


def test_validate_invite_token_bad_signature():
    result = validate_invite_token("not-a-real-token", secret_key="test-secret")
    assert result is None


def test_validate_invite_token_wrong_key():
    token = generate_invite_token("alice@example.com", secret_key="key-one")
    result = validate_invite_token(token, secret_key="key-two")
    assert result is None


def test_user_is_admin():
    admin = User(email="a@b.com", name="Admin", role="admin")
    assert admin.is_admin is True

    regular = User(email="c@d.com", name="Regular", role="user")
    assert regular.is_admin is False


def test_user_get_id():
    user = User(email="alice@example.com", name="Alice")
    assert user.get_id() == "alice@example.com"
