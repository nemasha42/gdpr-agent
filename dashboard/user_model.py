"""User model, registry, and data directory helpers for multiuser support."""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path

from flask_login import UserMixin
from itsdangerous import URLSafeTimedSerializer, BadSignature

_PROJECT_ROOT = Path(__file__).parent.parent
_USER_DATA_ROOT = _PROJECT_ROOT / "user_data"
_USERS_PATH = _USER_DATA_ROOT / "users.json"


def _safe_email(email: str) -> str:
    return email.replace("@", "_at_").replace(".", "_")


def _safe_email_to_address(safe: str) -> str:
    parts = safe.split("_at_")
    if len(parts) != 2:
        return safe
    local = parts[0].replace("_", ".")
    domain = parts[1].replace("_", ".")
    return f"{local}@{domain}"


def user_data_dir(email: str, *, root: Path = _USER_DATA_ROOT) -> Path:
    safe = _safe_email(email)
    path = (root / safe).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError(f"Path traversal attempt: {email}")
    return path


class User(UserMixin):
    def __init__(self, email: str, name: str, role: str = "user",
                 *, data_root: Path = _USER_DATA_ROOT):
        self.email = email
        self.name = name
        self.role = role
        self.data_dir = user_data_dir(email, root=data_root)

    def get_id(self) -> str:
        return self.email

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def _read_users_file(path: Path = _USERS_PATH) -> dict:
    if not path.exists():
        return {}
    with open(path) as f:
        return json.load(f)


def _write_users_file(data: dict, path: Path = _USERS_PATH) -> None:
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def load_users(*, path: Path = _USERS_PATH) -> dict:
    return _read_users_file(path)


def load_user(email: str, *, path: Path = _USERS_PATH,
              data_root: Path = _USER_DATA_ROOT) -> User | None:
    data = _read_users_file(path)
    if email not in data:
        return None
    rec = data[email]
    return User(email=email, name=rec["name"], role=rec.get("role", "user"),
                data_root=data_root)


def save_user(user: User, *, path: Path = _USERS_PATH) -> None:
    data = _read_users_file(path)
    if user.email in data:
        data[user.email]["name"] = user.name
        data[user.email]["role"] = user.role
    else:
        data[user.email] = {
            "name": user.name,
            "role": user.role,
            "created_at": datetime.now(timezone.utc).isoformat(),
            "invite_token": None,
        }
    _write_users_file(data, path)


def delete_user(email: str, *, path: Path = _USERS_PATH) -> bool:
    data = _read_users_file(path)
    if email not in data:
        return False
    del data[email]
    _write_users_file(data, path)
    return True


_INVITE_SALT = "multiuser-invite"


def generate_invite_token(email: str, *, secret_key: str) -> str:
    s = URLSafeTimedSerializer(secret_key)
    return s.dumps(email, salt=_INVITE_SALT)


def validate_invite_token(token: str, *, secret_key: str) -> str | None:
    s = URLSafeTimedSerializer(secret_key)
    try:
        return s.loads(token, salt=_INVITE_SALT)
    except BadSignature:
        return None
