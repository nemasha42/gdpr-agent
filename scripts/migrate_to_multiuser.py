"""One-time migration from single-user to multiuser directory layout.

Usage:
    python scripts/migrate_to_multiuser.py --email YOUR_EMAIL --name "Your Name"
"""

from __future__ import annotations

import json
import shutil
from datetime import datetime, timezone
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parent.parent
_DEFAULT_USER_DATA = _PROJECT_ROOT / "user_data"


def _safe_email(email: str) -> str:
    return email.replace("@", "_at_").replace(".", "_")


def migrate(
    *,
    user_data_root: Path = _DEFAULT_USER_DATA,
    admin_email: str,
    admin_name: str,
) -> None:
    """Migrate existing single-user data to multiuser layout."""
    safe = _safe_email(admin_email)
    user_dir = user_data_root / safe
    user_dir.mkdir(exist_ok=True)

    # 1. Move tokens/
    old_tokens = user_data_root / "tokens"
    new_tokens = user_dir / "tokens"
    if old_tokens.is_dir():
        if new_tokens.exists():
            shutil.rmtree(new_tokens)
        shutil.move(str(old_tokens), str(new_tokens))

    # 2. Move JSON state files
    for fname in [
        "sent_letters.json",
        "subprocessor_requests.json",
        "subprocessor_reply_state.json",
        "scan_state.json",
    ]:
        old = user_data_root / fname
        if old.exists():
            shutil.move(str(old), str(user_dir / fname))

    # 3. Extract user's account from reply_state.json
    old_state = user_data_root / "reply_state.json"
    if old_state.exists():
        data = json.loads(old_state.read_text())
        (user_dir / "reply_state.json").write_text(json.dumps(data, indent=2))
        old_state.unlink()

    # 4. Move received/
    old_received = user_data_root / "received"
    new_received = user_dir / "received"
    if old_received.is_dir():
        if new_received.exists():
            shutil.rmtree(new_received)
        shutil.move(str(old_received), str(new_received))

    # 5. Create users.json
    users_path = user_data_root / "users.json"
    users = {}
    if users_path.exists():
        users = json.loads(users_path.read_text())
    users[admin_email] = {
        "name": admin_name,
        "role": "admin",
        "created_at": datetime.now(timezone.utc).isoformat(),
        "invite_token": None,
    }
    users_path.write_text(json.dumps(users, indent=2))

    print(f"Migration complete. User data moved to {user_dir}")
    print(f"Admin user created: {admin_email}")


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="Migrate to multiuser layout")
    parser.add_argument(
        "--email", required=True, help="Your Gmail address (becomes admin)"
    )
    parser.add_argument("--name", required=True, help="Your full name")
    args = parser.parse_args()

    migrate(admin_email=args.email, admin_name=args.name)
