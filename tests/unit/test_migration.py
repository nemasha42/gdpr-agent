"""Tests for single-user to multiuser data migration."""

import json
from pathlib import Path


def _setup_legacy_data(root: Path):
    """Create a legacy single-user data layout."""
    (root / "tokens").mkdir(parents=True)
    (root / "tokens" / "nemasha_at_gmail_com_readonly.json").write_text("{}")
    (root / "tokens" / "nemasha_at_gmail_com_send.json").write_text("{}")

    (root / "reply_state.json").write_text(
        json.dumps(
            {
                "nemasha_at_gmail_com": {
                    "spotify.com": {"domain": "spotify.com", "company_name": "Spotify"}
                }
            }
        )
    )

    (root / "sent_letters.json").write_text(
        json.dumps([{"company_name": "Spotify", "to_email": "privacy@spotify.com"}])
    )

    (root / "subprocessor_requests.json").write_text("[]")
    (root / "subprocessor_reply_state.json").write_text("{}")
    (root / "scan_state.json").write_text("{}")

    received = root / "received" / "spotify.com"
    received.mkdir(parents=True)
    (received / "data.json").write_text("{}")


def test_migration_creates_user_dir(tmp_path):
    from scripts.migrate_to_multiuser import migrate

    _setup_legacy_data(tmp_path)
    migrate(
        user_data_root=tmp_path,
        admin_email="nemasha@gmail.com",
        admin_name="Nemasha",
    )

    user_dir = tmp_path / "nemasha_at_gmail_com"
    assert user_dir.is_dir()
    assert (user_dir / "tokens").is_dir()
    assert (user_dir / "sent_letters.json").exists()
    assert (user_dir / "reply_state.json").exists()
    assert (user_dir / "received" / "spotify.com" / "data.json").exists()


def test_migration_creates_users_json(tmp_path):
    from scripts.migrate_to_multiuser import migrate

    _setup_legacy_data(tmp_path)
    migrate(
        user_data_root=tmp_path,
        admin_email="nemasha@gmail.com",
        admin_name="Nemasha",
    )

    users = json.loads((tmp_path / "users.json").read_text())
    assert "nemasha@gmail.com" in users
    assert users["nemasha@gmail.com"]["role"] == "admin"


def test_migration_removes_old_files(tmp_path):
    from scripts.migrate_to_multiuser import migrate

    _setup_legacy_data(tmp_path)
    migrate(
        user_data_root=tmp_path,
        admin_email="nemasha@gmail.com",
        admin_name="Nemasha",
    )

    assert not (tmp_path / "tokens").exists()
    assert not (tmp_path / "reply_state.json").exists()
    assert not (tmp_path / "sent_letters.json").exists()
    assert not (tmp_path / "received").exists()


def test_migration_preserves_reply_state_content(tmp_path):
    from scripts.migrate_to_multiuser import migrate

    _setup_legacy_data(tmp_path)
    migrate(
        user_data_root=tmp_path,
        admin_email="nemasha@gmail.com",
        admin_name="Nemasha",
    )

    user_dir = tmp_path / "nemasha_at_gmail_com"
    data = json.loads((user_dir / "reply_state.json").read_text())
    assert "nemasha_at_gmail_com" in data
    assert "spotify.com" in data["nemasha_at_gmail_com"]
