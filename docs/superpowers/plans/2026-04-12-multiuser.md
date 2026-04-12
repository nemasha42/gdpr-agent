# Multiuser Support Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add invite-only multiuser support with directory-per-user isolation, shared OAuth app, progressive scan, and multi-mailbox tab UI.

**Architecture:** Each user gets an isolated directory under `user_data/<safe_email>/` containing their tokens, sent letters, reply state, and downloaded data. Auth uses Flask-Login with magic-link invites (admin-generated, `itsdangerous`). Gmail OAuth uses the shared GCP app with a new web-based flow (`google_auth_oauthlib.flow.Flow`). The existing CLI flow (`InstalledAppFlow`) is preserved for `run.py` and `monitor.py`.

**Tech Stack:** Flask-Login, itsdangerous (already a Flask dep), google-auth-oauthlib (already installed), HTMX SSE extension (CDN, no install).

**Spec:** `docs/superpowers/specs/2026-04-12-multiuser-design.md`

---

## File Structure

### New Files

| File | Responsibility |
|------|---------------|
| `dashboard/user_model.py` | `User` class (UserMixin), `users.json` CRUD, `user_data_dir()` path helper, invite token generation/validation |
| `dashboard/auth_routes.py` | Flask Blueprint: `/join/<token>`, `/login`, `/logout`, `/auth/callback`, `/auth/add-mailbox` |
| `dashboard/admin_routes.py` | Flask Blueprint: `/admin/invite`, `/admin/users` |
| `dashboard/sse.py` | SSE helper: `MessageAnnouncer`, `format_sse()` for progressive scan streaming |
| `dashboard/templates/login.html` | Login page with "Sign in with Google" button |
| `dashboard/templates/onboarding.html` | Name entry + "Connect Gmail" button |
| `dashboard/templates/scan.html` | Progressive scan page with SSE progress + company cards |
| `dashboard/templates/admin_invite.html` | Admin invite form |
| `dashboard/templates/admin_users.html` | Admin user list |
| `scripts/migrate_to_multiuser.py` | One-time migration of existing single-user data |
| `tests/unit/test_user_model.py` | Tests for User, users.json CRUD, path resolution, invite tokens |
| `tests/unit/test_auth_routes.py` | Tests for invite flow, OAuth callback, login/logout |
| `tests/unit/test_sse.py` | Tests for SSE announcer |
| `tests/unit/test_migration.py` | Tests for data migration script |

### Modified Files

| File | What Changes |
|------|-------------|
| `requirements.txt` | Add `flask-login>=0.6` |
| `auth/gmail_oauth.py` | Add `tokens_dir` parameter to `get_gmail_service()` and `get_gmail_send_service()` (default preserves current behavior) |
| `config/settings.py` | Remove `USER_*` fields; add `FLASK_SECRET_KEY` |
| `letter_engine/tracker.py` | Add `data_dir` parameter to `record_sent()`, `get_log()`, `record_subprocessor_request()` |
| `letter_engine/composer.py` | Accept `user_identity: dict` parameter instead of reading from `settings` singleton |
| `letter_engine/sender.py` | Pass `data_dir` through to tracker; pass `tokens_dir` to OAuth |
| `reply_monitor/state_manager.py` | Add `data_dir` parameter to `load_state()`, `save_state()` (derive file path from data_dir instead of module-level constant) |
| `dashboard/app.py` | Init Flask-Login, register blueprints, add `@app.before_request` for `g.user`, refactor all routes to use `g.user.data_dir`, add mailbox tab bar logic |
| `dashboard/scan_state.py` | Add `data_dir` parameter to `load_scan_state()`, `save_scan_state()` |
| `dashboard/templates/base.html` | Add mailbox tab bar, login/logout nav items |
| `run.py` | Resolve `data_dir` from user email, pass through pipeline |
| `monitor.py` | Resolve `data_dir` from user email |

---

## Task 1: User Model & Data Directory Helper

**Files:**
- Create: `dashboard/user_model.py`
- Create: `tests/unit/test_user_model.py`

This task builds the foundation: the `User` class, `users.json` persistence, `user_data_dir()` path helper, and invite token generation/validation.

- [ ] **Step 1: Write test for `_safe_email` and `user_data_dir`**

```python
# tests/unit/test_user_model.py
import pytest
from pathlib import Path


def test_safe_email():
    from dashboard.user_model import _safe_email
    assert _safe_email("alice@gmail.com") == "alice_at_gmail_com"
    assert _safe_email("bob.jones@company.co.uk") == "bob_jones_at_company_co_uk"


def test_user_data_dir(tmp_path):
    from dashboard.user_model import user_data_dir
    d = user_data_dir("alice@gmail.com", root=tmp_path)
    assert d == tmp_path / "alice_at_gmail_com"
    assert d.is_relative_to(tmp_path)


def test_user_data_dir_traversal_rejected(tmp_path):
    from dashboard.user_model import user_data_dir
    with pytest.raises(ValueError, match="Path traversal"):
        user_data_dir("../../etc/passwd", root=tmp_path)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_user_model.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'dashboard.user_model'`

- [ ] **Step 3: Implement `_safe_email` and `user_data_dir`**

```python
# dashboard/user_model.py
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
    """Encode an email address as a filesystem-safe string."""
    return email.replace("@", "_at_").replace(".", "_")


def _safe_email_to_address(safe: str) -> str:
    """Reverse _safe_email — best-effort, assumes single @ in original."""
    parts = safe.split("_at_")
    if len(parts) != 2:
        return safe
    local = parts[0].replace("_", ".")
    domain = parts[1].replace("_", ".")
    return f"{local}@{domain}"


def user_data_dir(email: str, *, root: Path = _USER_DATA_ROOT) -> Path:
    """Return the per-user data directory. Raises ValueError on traversal."""
    safe = _safe_email(email)
    path = (root / safe).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError(f"Path traversal attempt: {email}")
    return path
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/unit/test_user_model.py::test_safe_email tests/unit/test_user_model.py::test_user_data_dir tests/unit/test_user_model.py::test_user_data_dir_traversal_rejected -v`
Expected: 3 PASSED

- [ ] **Step 5: Write tests for User class and users.json CRUD**

```python
# tests/unit/test_user_model.py (append to existing file)

def test_load_users_empty(tmp_path):
    from dashboard.user_model import load_users
    users = load_users(path=tmp_path / "users.json")
    assert users == {}


def test_save_and_load_user(tmp_path):
    from dashboard.user_model import save_user, load_users, User
    path = tmp_path / "users.json"
    save_user(
        User(email="alice@gmail.com", name="Alice", role="admin"),
        path=path,
    )
    users = load_users(path=path)
    assert "alice@gmail.com" in users
    assert users["alice@gmail.com"]["name"] == "Alice"
    assert users["alice@gmail.com"]["role"] == "admin"


def test_load_user_by_email(tmp_path):
    from dashboard.user_model import save_user, load_user, User
    path = tmp_path / "users.json"
    save_user(User(email="alice@gmail.com", name="Alice", role="admin"), path=path)
    user = load_user("alice@gmail.com", path=path)
    assert user is not None
    assert user.name == "Alice"
    assert user.get_id() == "alice@gmail.com"


def test_load_user_missing(tmp_path):
    from dashboard.user_model import load_user
    user = load_user("nobody@example.com", path=tmp_path / "users.json")
    assert user is None
```

- [ ] **Step 6: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/test_user_model.py -v`
Expected: 4 new tests FAIL — `ImportError: cannot import name 'load_users' from 'dashboard.user_model'`

- [ ] **Step 7: Implement User class and users.json CRUD**

```python
# dashboard/user_model.py (append after user_data_dir)

class User(UserMixin):
    """Flask-Login compatible user. Identity = email address."""

    def __init__(self, email: str, name: str, role: str = "user"):
        self.email = email
        self.name = name
        self.role = role
        self.data_dir = user_data_dir(email)

    def get_id(self) -> str:
        return self.email

    @property
    def is_admin(self) -> bool:
        return self.role == "admin"


def _read_users_file(path: Path = _USERS_PATH) -> dict:
    """Read users.json, returning empty dict if missing."""
    if not path.exists():
        return {}
    with open(path) as f:
        return json.load(f)


def _write_users_file(data: dict, path: Path = _USERS_PATH) -> None:
    """Atomically write users.json."""
    tmp = path.with_suffix(".tmp")
    with open(tmp, "w") as f:
        json.dump(data, f, indent=2)
    os.replace(tmp, path)


def load_users(*, path: Path = _USERS_PATH) -> dict:
    """Return raw users dict from users.json."""
    return _read_users_file(path)


def load_user(email: str, *, path: Path = _USERS_PATH) -> User | None:
    """Load a single user by email. Returns None if not found."""
    data = _read_users_file(path)
    if email not in data:
        return None
    rec = data[email]
    return User(email=email, name=rec["name"], role=rec.get("role", "user"))


def save_user(user: User, *, path: Path = _USERS_PATH) -> None:
    """Create or update a user in users.json."""
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
    """Remove a user from users.json. Returns True if found and removed."""
    data = _read_users_file(path)
    if email not in data:
        return False
    del data[email]
    _write_users_file(data, path)
    return True
```

- [ ] **Step 8: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_user_model.py -v`
Expected: 7 PASSED

- [ ] **Step 9: Write tests for invite token generation and validation**

```python
# tests/unit/test_user_model.py (append)

def test_generate_invite_token():
    from dashboard.user_model import generate_invite_token, validate_invite_token
    token = generate_invite_token("friend@gmail.com", secret_key="test-secret")
    email = validate_invite_token(token, secret_key="test-secret")
    assert email == "friend@gmail.com"


def test_validate_invite_token_bad_signature():
    from dashboard.user_model import validate_invite_token
    result = validate_invite_token("tampered-token", secret_key="test-secret")
    assert result is None


def test_validate_invite_token_wrong_key():
    from dashboard.user_model import generate_invite_token, validate_invite_token
    token = generate_invite_token("friend@gmail.com", secret_key="key-A")
    result = validate_invite_token(token, secret_key="key-B")
    assert result is None
```

- [ ] **Step 10: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/test_user_model.py -k invite -v`
Expected: FAIL — `ImportError: cannot import name 'generate_invite_token'`

- [ ] **Step 11: Implement invite token functions**

```python
# dashboard/user_model.py (append)

_INVITE_SALT = "multiuser-invite"


def generate_invite_token(email: str, *, secret_key: str) -> str:
    """Generate a signed invite token encoding the email."""
    s = URLSafeTimedSerializer(secret_key)
    return s.dumps(email, salt=_INVITE_SALT)


def validate_invite_token(token: str, *, secret_key: str) -> str | None:
    """Validate an invite token. Returns email or None if invalid."""
    s = URLSafeTimedSerializer(secret_key)
    try:
        return s.loads(token, salt=_INVITE_SALT)
    except BadSignature:
        return None
```

- [ ] **Step 12: Run all tests**

Run: `.venv/bin/pytest tests/unit/test_user_model.py -v`
Expected: 10 PASSED

- [ ] **Step 13: Commit**

```bash
git add dashboard/user_model.py tests/unit/test_user_model.py
git commit -m "feat: add User model, users.json CRUD, invite tokens, data_dir helper"
```

---

## Task 2: Add flask-login Dependency & Config Changes

**Files:**
- Modify: `requirements.txt`
- Modify: `config/settings.py` (lines 12-42)

- [ ] **Step 1: Add flask-login to requirements.txt**

Add `flask-login>=0.6` to `requirements.txt`.

- [ ] **Step 2: Install the new dependency**

Run: `.venv/bin/pip install flask-login>=0.6`

- [ ] **Step 3: Remove USER_* fields from settings.py**

In `config/settings.py`, remove the per-user fields from the `Settings` class (lines 18-24). These will move to per-user storage. Keep `GOOGLE_CLIENT_ID`, `GOOGLE_CLIENT_SECRET`, `ANTHROPIC_API_KEY`, and `GDPR_FRAMEWORK`. Add `FLASK_SECRET_KEY` with auto-generation fallback.

```python
# config/settings.py
from __future__ import annotations

from pathlib import Path
from pydantic import BaseModel
from dotenv import load_dotenv
import os

_PROJECT_ROOT = Path(__file__).parent.parent
load_dotenv(_PROJECT_ROOT / ".env")


class Settings(BaseModel, frozen=True):
    google_client_id: str = ""
    google_client_secret: str = ""
    anthropic_api_key: str = ""
    gdpr_framework: str = "UK GDPR"
    flask_secret_key: str = ""


def get_settings() -> Settings:
    return Settings(
        google_client_id=os.getenv("GOOGLE_CLIENT_ID", ""),
        google_client_secret=os.getenv("GOOGLE_CLIENT_SECRET", ""),
        anthropic_api_key=os.getenv("ANTHROPIC_API_KEY", ""),
        gdpr_framework=os.getenv("GDPR_FRAMEWORK", "UK GDPR"),
        flask_secret_key=os.getenv("FLASK_SECRET_KEY", ""),
    )


settings = get_settings()
```

**Note:** `USER_FULL_NAME`, `USER_EMAIL`, and `USER_ADDRESS_*` are removed. These will be stored per-user in `users.json` (name) and potentially a future profile settings file (address). For now, address fields are deferred per spec — SAR letters will use email-only method.

- [ ] **Step 4: Run existing tests to check for breakage**

Run: `.venv/bin/pytest tests/unit/ -q`

Fix any tests that import `settings.user_full_name` etc. — they need to be updated to pass user identity explicitly. Note which tests break; those modules are fixed in later tasks.

- [ ] **Step 5: Commit**

```bash
git add requirements.txt config/settings.py
git commit -m "feat: add flask-login dep, remove per-user fields from global settings"
```

---

## Task 3: Data Path Refactor — State Manager

**Files:**
- Modify: `reply_monitor/state_manager.py` (lines 11-12, 53-91)
- Modify: `tests/unit/test_state_manager.py` (if exists — update fixtures)

The state manager is the most critical module to refactor. Currently it uses module-level `_STATE_PATH` constants. We need to derive paths from a `data_dir` parameter.

- [ ] **Step 1: Write test for `load_state` with explicit data_dir**

```python
# tests/unit/test_state_refactor.py
import json
from pathlib import Path


def test_load_state_from_data_dir(tmp_path):
    """load_state() should read from data_dir / reply_state.json."""
    from reply_monitor.state_manager import load_state, save_state

    state_file = tmp_path / "reply_state.json"
    state_file.write_text("{}")

    states = load_state("alice@gmail.com", data_dir=tmp_path)
    assert states == {}


def test_save_state_to_data_dir(tmp_path):
    """save_state() should write to data_dir / reply_state.json."""
    from reply_monitor.state_manager import save_state, load_state

    save_state("alice@gmail.com", {"example.com": {}}, data_dir=tmp_path)

    state_file = tmp_path / "reply_state.json"
    assert state_file.exists()
    data = json.loads(state_file.read_text())
    safe_key = "alice_at_gmail_com"
    assert safe_key in data
    assert "example.com" in data[safe_key]
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_state_refactor.py -v`
Expected: FAIL — `TypeError: load_state() got an unexpected keyword argument 'data_dir'`

- [ ] **Step 3: Add `data_dir` parameter to `load_state` and `save_state`**

In `reply_monitor/state_manager.py`, modify `load_state` (line 53) and `save_state` (line 72):

```python
# Change the signatures from:
def load_state(account_email: str, *, path: Path = _STATE_PATH) -> dict[str, CompanyState]:

# To:
def load_state(account_email: str, *, path: Path | None = None, data_dir: Path | None = None) -> dict[str, CompanyState]:
    """Load reply state for an account.

    Args:
        path: Explicit file path (legacy, for backward compat).
        data_dir: Per-user data directory. File is data_dir/reply_state.json.
        If neither given, falls back to module-level _STATE_PATH.
    """
    if path is None:
        path = (data_dir / "reply_state.json") if data_dir else _STATE_PATH
    # ... rest of function unchanged
```

Apply the same pattern to `save_state`:

```python
def save_state(account_email: str, states: dict, *, path: Path | None = None, data_dir: Path | None = None) -> None:
    if path is None:
        path = (data_dir / "reply_state.json") if data_dir else _STATE_PATH
    # ... rest unchanged
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `.venv/bin/pytest tests/unit/test_state_refactor.py tests/unit/test_state_manager.py -v`
Expected: ALL PASS (existing tests use `path=` which still works)

- [ ] **Step 5: Commit**

```bash
git add reply_monitor/state_manager.py tests/unit/test_state_refactor.py
git commit -m "feat: add data_dir parameter to state_manager load/save"
```

---

## Task 4: Data Path Refactor — Tracker

**Files:**
- Modify: `letter_engine/tracker.py` (lines 9-10, 13-58)

- [ ] **Step 1: Write test for tracker with data_dir**

```python
# tests/unit/test_tracker_refactor.py
import json
from pathlib import Path
from unittest.mock import MagicMock


def _make_letter():
    letter = MagicMock()
    letter.company_name = "Spotify"
    letter.method = "email"
    letter.to_email = "privacy@spotify.com"
    letter.subject = "Subject Access Request"
    letter.body = "Dear Spotify..."
    letter.gmail_message_id = "msg123"
    letter.gmail_thread_id = "thread123"
    return letter


def test_record_sent_to_data_dir(tmp_path):
    from letter_engine.tracker import record_sent
    letter = _make_letter()
    record_sent(letter, data_dir=tmp_path)
    log_file = tmp_path / "sent_letters.json"
    assert log_file.exists()
    records = json.loads(log_file.read_text())
    assert len(records) == 1
    assert records[0]["company_name"] == "Spotify"


def test_get_log_from_data_dir(tmp_path):
    from letter_engine.tracker import record_sent, get_log
    letter = _make_letter()
    record_sent(letter, data_dir=tmp_path)
    log = get_log(data_dir=tmp_path)
    assert len(log) == 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_tracker_refactor.py -v`
Expected: FAIL — `TypeError: record_sent() got an unexpected keyword argument 'data_dir'`

- [ ] **Step 3: Add `data_dir` parameter to tracker functions**

In `letter_engine/tracker.py`, modify all three public functions to accept `data_dir`:

```python
def record_sent(letter: SARLetter, *, path: Path | None = None, data_dir: Path | None = None):
    if path is None:
        path = (data_dir / "sent_letters.json") if data_dir else _TRACKER_PATH
    # ... rest unchanged

def record_subprocessor_request(letter, domain, *, path: Path | None = None, data_dir: Path | None = None):
    if path is None:
        path = (data_dir / "subprocessor_requests.json") if data_dir else _SUBPROCESSOR_REQUESTS_PATH
    # ... rest unchanged

def get_log(*, path: Path | None = None, data_dir: Path | None = None) -> list[dict]:
    if path is None:
        path = (data_dir / "sent_letters.json") if data_dir else _TRACKER_PATH
    # ... rest unchanged
```

- [ ] **Step 4: Run all tracker tests**

Run: `.venv/bin/pytest tests/unit/test_tracker_refactor.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add letter_engine/tracker.py tests/unit/test_tracker_refactor.py
git commit -m "feat: add data_dir parameter to letter tracker"
```

---

## Task 5: Data Path Refactor — Gmail OAuth

**Files:**
- Modify: `auth/gmail_oauth.py` (lines 17, 80-156, 159-215)

- [ ] **Step 1: Write test for `get_gmail_service` with `tokens_dir`**

```python
# tests/unit/test_oauth_refactor.py
from pathlib import Path
from unittest.mock import patch, MagicMock
import json


def test_get_gmail_service_uses_tokens_dir(tmp_path):
    """get_gmail_service should look for tokens in the given tokens_dir."""
    from auth.gmail_oauth import get_gmail_service, _safe_email

    # Create a fake token file
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
        mock_creds_cls.from_authorized_user_file.return_value = mock_creds

        service, email = get_gmail_service(
            email_hint="alice@gmail.com",
            tokens_dir=tokens_dir,
        )

        mock_creds_cls.from_authorized_user_file.assert_called_once()
        call_path = mock_creds_cls.from_authorized_user_file.call_args[0][0]
        assert str(tokens_dir) in call_path
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_oauth_refactor.py -v`
Expected: FAIL — `TypeError: get_gmail_service() got an unexpected keyword argument 'tokens_dir'`

- [ ] **Step 3: Add `tokens_dir` parameter to OAuth functions**

In `auth/gmail_oauth.py`, add `tokens_dir: Path | None = None` parameter to `get_gmail_service` (line 80), `check_send_token_valid` (line 159), and `get_gmail_send_service` (line 187). When `tokens_dir` is provided, use it instead of module-level `_TOKENS_DIR`:

```python
def get_gmail_service(email_hint=None, *, tokens_dir: Path | None = None):
    """Get Gmail readonly service. Uses tokens_dir if given, else module default."""
    td = tokens_dir or _TOKENS_DIR
    td.mkdir(parents=True, exist_ok=True)
    # Replace all _TOKENS_DIR references in this function with td
    # ... rest of logic unchanged
```

Apply the same pattern to `check_send_token_valid` and `get_gmail_send_service`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/pytest tests/unit/test_oauth_refactor.py -v`
Expected: PASS

- [ ] **Step 5: Run existing tests to verify no breakage**

Run: `.venv/bin/pytest tests/unit/ -q`
Expected: No regressions (existing callers don't pass `tokens_dir`, so they get the default)

- [ ] **Step 6: Commit**

```bash
git add auth/gmail_oauth.py tests/unit/test_oauth_refactor.py
git commit -m "feat: add tokens_dir parameter to Gmail OAuth functions"
```

---

## Task 6: Data Path Refactor — Composer

**Files:**
- Modify: `letter_engine/composer.py` (lines 13-44, 47-87)

- [ ] **Step 1: Write test for `compose` with explicit user identity**

```python
# tests/unit/test_composer_refactor.py
from unittest.mock import MagicMock


def test_compose_uses_user_identity():
    from letter_engine.composer import compose

    record = MagicMock()
    record.domain = "spotify.com"
    record.company_name = "Spotify"
    record.contact.email = "privacy@spotify.com"
    record.contact.preferred_method = "email"
    record.contact.dsar_portal_url = ""

    user_identity = {
        "user_full_name": "Alice Smith",
        "user_email": "alice@gmail.com",
        "user_address_line1": "",
        "user_address_city": "",
        "user_address_postcode": "",
        "user_address_country": "",
        "gdpr_framework": "UK GDPR",
    }

    letter = compose(record, user_identity=user_identity)
    assert "Alice Smith" in letter.body
    assert "alice@gmail.com" in letter.body
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_composer_refactor.py -v`
Expected: FAIL — `TypeError: compose() got an unexpected keyword argument 'user_identity'`

- [ ] **Step 3: Add `user_identity` parameter to `compose` and `compose_subprocessor_request`**

In `letter_engine/composer.py`, change the template variable dict (lines 20-26) to use `user_identity` when provided, falling back to settings for backward compatibility:

```python
def compose(record: CompanyRecord, *, user_identity: dict | None = None) -> SARLetter:
    if user_identity is None:
        # Legacy path for CLI usage
        from config.settings import settings
        user_identity = {
            "user_full_name": settings.user_full_name,
            "user_email": settings.user_email,
            "user_address_line1": settings.user_address_line1,
            "user_address_city": settings.user_address_city,
            "user_address_postcode": settings.user_address_postcode,
            "user_address_country": settings.user_address_country,
            "gdpr_framework": settings.gdpr_framework,
        }
    # Use user_identity dict for template variables
    template_vars = {
        **user_identity,
        "company_name": record.company_name,
        # ... rest of template vars
    }
```

**Important:** The `from config.settings import settings` is moved inside the `if` block so it only executes on the legacy path. This prevents import errors when USER_* fields are removed from settings (Task 2).

Apply the same pattern to `compose_subprocessor_request`.

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/unit/test_composer_refactor.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add letter_engine/composer.py tests/unit/test_composer_refactor.py
git commit -m "feat: add user_identity parameter to letter composer"
```

---

## Task 7: Data Path Refactor — Scan State & Dashboard Helpers

**Files:**
- Modify: `dashboard/scan_state.py` (lines 6, 24-50)

- [ ] **Step 1: Write test for scan_state with data_dir**

```python
# tests/unit/test_scan_state_refactor.py
from pathlib import Path


def test_load_scan_state_from_data_dir(tmp_path):
    from dashboard.scan_state import load_scan_state, save_scan_state
    save_scan_state("alice@gmail.com", {"status": "paused"}, data_dir=tmp_path)
    state = load_scan_state("alice@gmail.com", data_dir=tmp_path)
    assert state["status"] == "paused"


def test_scan_state_file_location(tmp_path):
    from dashboard.scan_state import save_scan_state
    save_scan_state("alice@gmail.com", {"status": "done"}, data_dir=tmp_path)
    assert (tmp_path / "scan_state.json").exists()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/pytest tests/unit/test_scan_state_refactor.py -v`
Expected: FAIL — `TypeError: ... got an unexpected keyword argument 'data_dir'`

- [ ] **Step 3: Add `data_dir` parameter to scan_state functions**

In `dashboard/scan_state.py`, add `data_dir: Path | None = None` to `load_scan_state` and `save_scan_state`. When `data_dir` is given, use `data_dir / "scan_state.json"` instead of `_SCAN_STATE_PATH`.

```python
def load_scan_state(account: str, *, path: Path | None = None, data_dir: Path | None = None) -> dict:
    if path is None:
        path = (data_dir / "scan_state.json") if data_dir else _SCAN_STATE_PATH
    # ... rest unchanged

def save_scan_state(account: str, state: dict, *, path: Path | None = None, data_dir: Path | None = None) -> None:
    if path is None:
        path = (data_dir / "scan_state.json") if data_dir else _SCAN_STATE_PATH
    # ... rest unchanged
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/unit/test_scan_state_refactor.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add dashboard/scan_state.py tests/unit/test_scan_state_refactor.py
git commit -m "feat: add data_dir parameter to scan_state"
```

---

## Task 8: Auth Routes Blueprint — Join, Login, Logout, OAuth Callback

**Files:**
- Create: `dashboard/auth_routes.py`
- Create: `tests/unit/test_auth_routes.py`

This is the core auth flow: invite link → onboarding → OAuth → session.

- [ ] **Step 1: Write test for invite link validation route**

```python
# tests/unit/test_auth_routes.py
import pytest
from flask import Flask
from unittest.mock import patch


@pytest.fixture
def app(tmp_path):
    """Create a test Flask app with auth blueprint."""
    app = Flask(__name__)
    app.config["SECRET_KEY"] = "test-secret"
    app.config["TESTING"] = True
    app.config["USERS_PATH"] = tmp_path / "users.json"
    app.config["USER_DATA_ROOT"] = tmp_path

    from flask_login import LoginManager
    from dashboard.auth_routes import auth_bp

    login_manager = LoginManager(app)
    app.register_blueprint(auth_bp)

    @login_manager.user_loader
    def load(email):
        from dashboard.user_model import load_user
        return load_user(email, path=app.config["USERS_PATH"])

    return app


def test_join_valid_token(app, tmp_path):
    from dashboard.user_model import generate_invite_token
    token = generate_invite_token("friend@gmail.com", secret_key="test-secret")
    with app.test_client() as client:
        resp = client.get(f"/join/{token}")
        assert resp.status_code == 200  # renders onboarding page


def test_join_invalid_token(app):
    with app.test_client() as client:
        resp = client.get("/join/bad-token")
        assert resp.status_code == 403


def test_join_existing_user_redirects_to_login(app, tmp_path):
    from dashboard.user_model import save_user, User, generate_invite_token
    save_user(
        User(email="friend@gmail.com", name="Friend", role="user"),
        path=tmp_path / "users.json",
    )
    token = generate_invite_token("friend@gmail.com", secret_key="test-secret")
    with app.test_client() as client:
        resp = client.get(f"/join/{token}")
        assert resp.status_code == 302  # redirect to login
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/test_auth_routes.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'dashboard.auth_routes'`

- [ ] **Step 3: Implement auth_routes blueprint**

```python
# dashboard/auth_routes.py
"""Authentication routes: invite, login, logout, OAuth callback."""

from __future__ import annotations

from pathlib import Path

from flask import (
    Blueprint, current_app, flash, redirect, render_template,
    request, session, url_for,
)
from flask_login import current_user, login_required, login_user, logout_user

from dashboard.user_model import (
    User, generate_invite_token, load_user, save_user,
    validate_invite_token, user_data_dir,
)

auth_bp = Blueprint("auth", __name__)


def _users_path() -> Path:
    return current_app.config.get("USERS_PATH", Path("user_data/users.json"))


def _user_data_root() -> Path:
    return current_app.config.get("USER_DATA_ROOT", Path("user_data"))


@auth_bp.route("/join/<token>")
def join(token: str):
    """Invite link landing page."""
    email = validate_invite_token(
        token, secret_key=current_app.config["SECRET_KEY"]
    )
    if email is None:
        return "Invalid or expired invite link.", 403

    # Check if user already exists
    existing = load_user(email, path=_users_path())
    if existing is not None:
        return redirect(url_for("auth.login"))

    # Store email in session for onboarding flow
    session["onboarding_email"] = email
    session["invite_token"] = token
    return render_template("onboarding.html", email=email)


@auth_bp.route("/onboarding", methods=["POST"])
def onboarding_submit():
    """Process onboarding form (name entry), redirect to Gmail OAuth."""
    email = session.get("onboarding_email")
    if not email:
        return redirect(url_for("auth.login"))

    name = request.form.get("name", "").strip()
    if not name:
        return render_template("onboarding.html", email=email, error="Name is required.")

    # Create user
    user = User(email=email, name=name, role="user")
    save_user(user, path=_users_path())

    # Create user data directory
    data_dir = user_data_dir(email, root=_user_data_root())
    data_dir.mkdir(parents=True, exist_ok=True)
    (data_dir / "tokens").mkdir(exist_ok=True)

    # Log the user in
    login_user(user, remember=True)

    # Redirect to Gmail OAuth
    return redirect(url_for("auth.start_gmail_oauth", scope="readonly"))


@auth_bp.route("/auth/gmail")
@login_required
def start_gmail_oauth():
    """Initiate Gmail OAuth flow."""
    from google_auth_oauthlib.flow import Flow
    from config.settings import settings

    scope_label = request.args.get("scope", "readonly")
    scopes = {
        "readonly": ["https://www.googleapis.com/auth/gmail.readonly"],
        "send": ["https://www.googleapis.com/auth/gmail.send"],
    }[scope_label]

    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
        scopes=scopes,
        redirect_uri=url_for("auth.oauth_callback", _external=True),
    )
    auth_url, state = flow.authorization_url(
        access_type="offline",
        prompt="consent",
        login_hint=current_user.email,
    )
    session["oauth_state"] = state
    session["oauth_scope_label"] = scope_label
    return redirect(auth_url)


@auth_bp.route("/auth/callback")
def oauth_callback():
    """Handle Google OAuth callback."""
    from google_auth_oauthlib.flow import Flow
    from config.settings import settings
    from auth.gmail_oauth import _safe_email

    scope_label = session.pop("oauth_scope_label", "readonly")
    scopes = {
        "readonly": ["https://www.googleapis.com/auth/gmail.readonly"],
        "send": ["https://www.googleapis.com/auth/gmail.send"],
    }[scope_label]

    flow = Flow.from_client_config(
        {
            "web": {
                "client_id": settings.google_client_id,
                "client_secret": settings.google_client_secret,
                "auth_uri": "https://accounts.google.com/o/oauth2/auth",
                "token_uri": "https://oauth2.googleapis.com/token",
            }
        },
        scopes=scopes,
        redirect_uri=url_for("auth.oauth_callback", _external=True),
    )
    flow.fetch_token(authorization_response=request.url)
    creds = flow.credentials

    # Determine the Gmail address from the token
    from googleapiclient.discovery import build
    service = build("gmail", "v1", credentials=creds)
    profile = service.users().getProfile(userId="me").execute()
    gmail_email = profile["emailAddress"]

    # Save token to user's tokens dir
    safe = _safe_email(gmail_email)
    tokens_dir = user_data_dir(
        current_user.email, root=_user_data_root()
    ) / "tokens"
    tokens_dir.mkdir(parents=True, exist_ok=True)
    token_file = tokens_dir / f"{safe}_{scope_label}.json"
    token_file.write_text(creds.to_json())

    # If this was onboarding, redirect to scan
    if session.pop("onboarding_email", None):
        return redirect(url_for("dashboard.scan_page", mailbox=gmail_email))

    # Otherwise redirect back to dashboard
    flash(f"Gmail account {gmail_email} connected ({scope_label}).")
    return redirect(url_for("dashboard.index"))


@auth_bp.route("/login")
def login():
    """Login page — 'Sign in with Google' button."""
    if current_user.is_authenticated:
        return redirect(url_for("dashboard.index"))
    return render_template("login.html")


@auth_bp.route("/login/google")
def login_google():
    """Start OAuth flow for returning users (login, not onboarding)."""
    session["login_flow"] = True
    return redirect(url_for("auth.start_gmail_oauth", scope="readonly"))


@auth_bp.route("/logout")
@login_required
def logout():
    """Log out the current user."""
    logout_user()
    return redirect(url_for("auth.login"))
```

- [ ] **Step 4: Create minimal onboarding and login templates**

```html
<!-- dashboard/templates/onboarding.html -->
{% extends "base.html" %}
{% block title %}Welcome — GDPR Agent{% endblock %}
{% block content %}
<div class="container mt-5" style="max-width: 480px;">
  <h2>Welcome to GDPR Agent</h2>
  <p>You've been invited to use this tool. Let's get you set up.</p>
  {% if error %}
  <div class="alert alert-danger">{{ error }}</div>
  {% endif %}
  <form method="POST" action="{{ url_for('auth.onboarding_submit') }}">
    <div class="mb-3">
      <label for="name" class="form-label">Your full name</label>
      <input type="text" class="form-control" id="name" name="name" required
             placeholder="Used in Subject Access Request letters">
    </div>
    <p class="text-muted small">Email: {{ email }}</p>
    <button type="submit" class="btn btn-primary w-100">
      Continue &amp; Connect Gmail
    </button>
  </form>
</div>
{% endblock %}
```

```html
<!-- dashboard/templates/login.html -->
{% extends "base.html" %}
{% block title %}Sign In — GDPR Agent{% endblock %}
{% block content %}
<div class="container mt-5" style="max-width: 480px;">
  <h2>GDPR Agent</h2>
  <p>Sign in with the Google account you used during setup.</p>
  <a href="{{ url_for('auth.login_google') }}" class="btn btn-outline-dark w-100">
    Sign in with Google
  </a>
</div>
{% endblock %}
```

- [ ] **Step 5: Run tests**

Run: `.venv/bin/pytest tests/unit/test_auth_routes.py -v`
Expected: PASS (at least the join route tests)

- [ ] **Step 6: Commit**

```bash
git add dashboard/auth_routes.py dashboard/templates/onboarding.html dashboard/templates/login.html tests/unit/test_auth_routes.py
git commit -m "feat: add auth blueprint with invite, login, logout, OAuth callback"
```

---

## Task 9: Admin Routes Blueprint

**Files:**
- Create: `dashboard/admin_routes.py`
- Create: `dashboard/templates/admin_invite.html`
- Create: `dashboard/templates/admin_users.html`

- [ ] **Step 1: Write test for admin invite route**

```python
# tests/unit/test_admin_routes.py
import pytest
from flask import Flask
from flask_login import LoginManager, login_user

from dashboard.user_model import User, save_user, load_user


@pytest.fixture
def app(tmp_path):
    app = Flask(__name__)
    app.config["SECRET_KEY"] = "test-secret"
    app.config["TESTING"] = True
    app.config["USERS_PATH"] = tmp_path / "users.json"
    app.config["USER_DATA_ROOT"] = tmp_path

    from dashboard.admin_routes import admin_bp
    from dashboard.auth_routes import auth_bp

    login_manager = LoginManager(app)
    app.register_blueprint(admin_bp)
    app.register_blueprint(auth_bp)

    @login_manager.user_loader
    def load(email):
        return load_user(email, path=app.config["USERS_PATH"])

    # Create admin user
    admin = User(email="admin@gmail.com", name="Admin", role="admin")
    save_user(admin, path=tmp_path / "users.json")
    (tmp_path / "admin_at_gmail_com").mkdir()

    return app


def test_admin_invite_generates_link(app, tmp_path):
    with app.test_client() as client:
        # Log in as admin
        with client.session_transaction() as sess:
            sess["_user_id"] = "admin@gmail.com"

        resp = client.post("/admin/invite", data={"email": "friend@gmail.com"})
        assert resp.status_code == 200
        assert b"join/" in resp.data  # page shows the invite link


def test_non_admin_cannot_access_invite(app, tmp_path):
    regular = User(email="user@gmail.com", name="User", role="user")
    save_user(regular, path=tmp_path / "users.json")

    with app.test_client() as client:
        with client.session_transaction() as sess:
            sess["_user_id"] = "user@gmail.com"

        resp = client.get("/admin/invite")
        assert resp.status_code == 403
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/test_admin_routes.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'dashboard.admin_routes'`

- [ ] **Step 3: Implement admin routes**

```python
# dashboard/admin_routes.py
"""Admin routes: invite users, manage users."""

from __future__ import annotations

from pathlib import Path
from functools import wraps

from flask import (
    Blueprint, current_app, render_template, request, abort,
)
from flask_login import current_user, login_required

from dashboard.user_model import (
    generate_invite_token, load_users,
)

admin_bp = Blueprint("admin", __name__, url_prefix="/admin")


def _users_path() -> Path:
    return current_app.config.get("USERS_PATH", Path("user_data/users.json"))


def admin_required(f):
    @wraps(f)
    @login_required
    def decorated(*args, **kwargs):
        if not current_user.is_admin:
            abort(403)
        return f(*args, **kwargs)
    return decorated


@admin_bp.route("/invite", methods=["GET", "POST"])
@admin_required
def invite():
    invite_link = None
    if request.method == "POST":
        email = request.form.get("email", "").strip()
        if email:
            token = generate_invite_token(
                email, secret_key=current_app.config["SECRET_KEY"]
            )
            invite_link = f"{request.host_url}join/{token}"

    return render_template("admin_invite.html", invite_link=invite_link)


@admin_bp.route("/users")
@admin_required
def users_list():
    users = load_users(path=_users_path())
    return render_template("admin_users.html", users=users)
```

- [ ] **Step 4: Create admin templates**

```html
<!-- dashboard/templates/admin_invite.html -->
{% extends "base.html" %}
{% block title %}Invite User — GDPR Agent{% endblock %}
{% block content %}
<div class="container mt-4" style="max-width: 600px;">
  <h3>Invite a Friend</h3>
  <form method="POST">
    <div class="mb-3">
      <label for="email" class="form-label">Friend's Gmail address</label>
      <input type="email" class="form-control" id="email" name="email" required>
    </div>
    <button type="submit" class="btn btn-primary">Generate Invite Link</button>
  </form>
  {% if invite_link %}
  <div class="alert alert-success mt-3">
    <strong>Share this link:</strong><br>
    <code>{{ invite_link }}</code>
  </div>
  {% endif %}
</div>
{% endblock %}
```

```html
<!-- dashboard/templates/admin_users.html -->
{% extends "base.html" %}
{% block title %}Users — GDPR Agent{% endblock %}
{% block content %}
<div class="container mt-4">
  <h3>Registered Users</h3>
  <table class="table">
    <thead><tr><th>Email</th><th>Name</th><th>Role</th><th>Joined</th></tr></thead>
    <tbody>
    {% for email, u in users.items() %}
      <tr>
        <td>{{ email }}</td>
        <td>{{ u.name }}</td>
        <td>{{ u.role }}</td>
        <td>{{ u.created_at[:10] }}</td>
      </tr>
    {% endfor %}
    </tbody>
  </table>
</div>
{% endblock %}
```

- [ ] **Step 5: Run tests**

Run: `.venv/bin/pytest tests/unit/test_admin_routes.py -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add dashboard/admin_routes.py dashboard/templates/admin_invite.html dashboard/templates/admin_users.html tests/unit/test_admin_routes.py
git commit -m "feat: add admin blueprint with invite and user list routes"
```

---

## Task 10: SSE Helper for Progressive Scan

**Files:**
- Create: `dashboard/sse.py`
- Create: `tests/unit/test_sse.py`

- [ ] **Step 1: Write test for SSE announcer**

```python
# tests/unit/test_sse.py
import queue


def test_format_sse_data_only():
    from dashboard.sse import format_sse
    result = format_sse("hello")
    assert result == "data: hello\n\n"


def test_format_sse_with_event():
    from dashboard.sse import format_sse
    result = format_sse("42%", event="progress")
    assert result == "event: progress\ndata: 42%\n\n"


def test_announcer_delivers_to_listener():
    from dashboard.sse import MessageAnnouncer, format_sse
    ann = MessageAnnouncer()
    q = ann.listen()
    ann.announce(format_sse("test"))
    msg = q.get_nowait()
    assert "data: test" in msg


def test_announcer_drops_full_queue():
    from dashboard.sse import MessageAnnouncer, format_sse
    ann = MessageAnnouncer()
    q = ann.listen()
    # Fill the queue beyond capacity
    for i in range(20):
        ann.announce(format_sse(str(i)))
    # Listener should have been dropped (queue full)
    assert len(ann.listeners) == 0
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/test_sse.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement SSE helper**

```python
# dashboard/sse.py
"""Server-Sent Events helper for real-time streaming to HTMX frontend."""

from __future__ import annotations

import queue


class MessageAnnouncer:
    """Fan-out message queue for SSE listeners."""

    def __init__(self):
        self.listeners: list[queue.Queue] = []

    def listen(self) -> queue.Queue:
        q: queue.Queue = queue.Queue(maxsize=10)
        self.listeners.append(q)
        return q

    def announce(self, msg: str) -> None:
        for i in reversed(range(len(self.listeners))):
            try:
                self.listeners[i].put_nowait(msg)
            except queue.Full:
                del self.listeners[i]


def format_sse(data: str, event: str | None = None) -> str:
    """Format a message as an SSE frame."""
    msg = f"data: {data}\n\n"
    if event:
        msg = f"event: {event}\n{msg}"
    return msg
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/unit/test_sse.py -v`
Expected: 4 PASSED

- [ ] **Step 5: Commit**

```bash
git add dashboard/sse.py tests/unit/test_sse.py
git commit -m "feat: add SSE helper for real-time scan progress"
```

---

## Task 11: Wire Flask-Login and Blueprints into Dashboard App

**Files:**
- Modify: `dashboard/app.py` (lines 80-101, 281-298, 434-471, 1642-1696)

This is the integration task — connecting all the pieces into the existing dashboard.

- [ ] **Step 1: Add Flask-Login initialization and blueprint registration**

At the top of `dashboard/app.py`, after the Flask app is created, add:

```python
from flask_login import LoginManager, current_user, login_required
from dashboard.user_model import load_user as _load_user_by_email, user_data_dir
from dashboard.auth_routes import auth_bp
from dashboard.admin_routes import admin_bp

# After app = Flask(__name__):
login_manager = LoginManager(app)
login_manager.login_view = "auth.login"

# Load or generate secret key
_SECRET_KEY_PATH = _USER_DATA / "secret_key.txt"
if _SECRET_KEY_PATH.exists():
    app.config["SECRET_KEY"] = _SECRET_KEY_PATH.read_text().strip()
else:
    import secrets
    key = secrets.token_hex(32)
    _SECRET_KEY_PATH.write_text(key)
    app.config["SECRET_KEY"] = key

app.config["USERS_PATH"] = _USER_DATA / "users.json"
app.config["USER_DATA_ROOT"] = _USER_DATA

app.register_blueprint(auth_bp)
app.register_blueprint(admin_bp)


@login_manager.user_loader
def _load_user(email):
    return _load_user_by_email(email, path=_USER_DATA / "users.json")
```

- [ ] **Step 2: Add `@app.before_request` to set `g.user`**

```python
from flask import g

@app.before_request
def _inject_user():
    """Set g.user for all routes. Skip auth routes."""
    if request.endpoint and request.endpoint.startswith(("auth.", "static")):
        return
    if not current_user.is_authenticated:
        if request.endpoint and request.endpoint.startswith("admin."):
            return login_manager.unauthorized()
        # Allow unauthenticated access only to auth routes
        if request.endpoint not in (None,):
            return login_manager.unauthorized()
    else:
        g.user = current_user
        g.data_dir = current_user.data_dir
```

- [ ] **Step 3: Refactor `_load_all_states` to use `g.data_dir`**

Modify `_load_all_states` (line 1673) to accept `data_dir` and use it for state file and sent_letters paths:

```python
def _load_all_states(account: str, *, data_dir: Path | None = None) -> dict[str, CompanyState]:
    dd = data_dir or _USER_DATA  # fallback for backward compat
    states = load_state(account, data_dir=dd)
    sent_log = get_log(data_dir=dd)
    # ... rest of merge logic unchanged
```

- [ ] **Step 4: Update route functions to use `g.data_dir`**

For each route that currently calls `_load_all_states(account)`, change to `_load_all_states(account, data_dir=g.data_dir)`. Similarly, update calls to `load_state`, `save_state`, `get_log`, `load_scan_state`, `save_scan_state` to pass `data_dir=g.data_dir`.

The account dropdown (`_get_accounts`) should now scan only the current user's data directory for token files and state keys, not the global `user_data/` directory.

Replace `_get_accounts()` (line 281):
```python
def _get_accounts() -> list[str]:
    """Return mailbox emails for the current logged-in user."""
    data_dir = g.data_dir
    accounts: set[str] = set()

    # From reply_state.json
    state_file = data_dir / "reply_state.json"
    if state_file.exists():
        import json
        with open(state_file) as f:
            data = json.load(f)
        for safe_key in data:
            accounts.add(_safe_email_to_address(safe_key))

    # From token files
    tokens_dir = data_dir / "tokens"
    if tokens_dir.exists():
        for p in tokens_dir.glob("*_readonly.json"):
            safe_key = p.stem.replace("_readonly", "")
            accounts.add(_safe_email_to_address(safe_key))

    return sorted(accounts)
```

- [ ] **Step 5: Add mailbox tab bar to base template**

In `dashboard/templates/base.html`, add the tab bar below the nav:

```html
{% if current_user.is_authenticated %}
<div class="container-fluid mt-2">
  <ul class="nav nav-tabs" id="mailbox-tabs">
    {% set mailboxes = get_mailboxes() %}
    {% set active_mailbox = request.args.get('mailbox', 'all') %}
    <li class="nav-item">
      <a class="nav-link {% if active_mailbox == 'all' %}active{% endif %}"
         href="?mailbox=all">All ({{ total_count }})</a>
    </li>
    {% for mb in mailboxes %}
    <li class="nav-item">
      <a class="nav-link {% if active_mailbox == mb.safe %}active{% endif %}"
         href="?mailbox={{ mb.safe }}">{{ mb.short }} ({{ mb.count }})</a>
    </li>
    {% endfor %}
    <li class="nav-item">
      <a class="nav-link" href="{{ url_for('auth.start_gmail_oauth', scope='readonly') }}">+</a>
    </li>
  </ul>
</div>
{% endif %}
```

- [ ] **Step 6: Add login/logout to nav**

In `dashboard/templates/base.html`, add to the navbar:

```html
{% if current_user.is_authenticated %}
  <span class="navbar-text me-3">{{ current_user.name }}</span>
  {% if current_user.is_admin %}
    <a href="{{ url_for('admin.invite') }}" class="btn btn-sm btn-outline-secondary me-2">Invite</a>
  {% endif %}
  <a href="{{ url_for('auth.logout') }}" class="btn btn-sm btn-outline-secondary">Logout</a>
{% endif %}
```

- [ ] **Step 7: Restrict `/costs` route to admin**

Find the existing `/costs` route in `dashboard/app.py` and add admin check:

```python
@app.route("/costs")
@login_required
def costs():
    if not g.user.is_admin:
        abort(403)
    # ... existing route logic
```

- [ ] **Step 8: Update sender.py to pass data_dir to tracker**

In `letter_engine/sender.py`, the `send_letter` function (line 58) calls `record_sent()`. Add `data_dir` passthrough:

```python
def send_letter(letter, scan_email: str, *, record: bool = True, data_dir: Path | None = None, tokens_dir: Path | None = None):
    result = _dispatch_email(letter, scan_email, tokens_dir=tokens_dir)
    if record:
        from letter_engine.tracker import record_sent
        record_sent(letter, data_dir=data_dir)
    return result
```

Apply the same to `preview_and_send` and `send_thread_reply`.

- [ ] **Step 9: Run existing dashboard tests (if any) + manual smoke test**

Run: `.venv/bin/pytest tests/unit/ -q`
Expected: All existing tests pass. New auth routes require manual testing with a browser.

- [ ] **Step 10: Commit**

```bash
git add dashboard/app.py dashboard/templates/base.html letter_engine/sender.py
git commit -m "feat: wire Flask-Login, auth/admin blueprints, and mailbox tabs into dashboard"
```

---

## Task 12: Progressive Scan Page

**Files:**
- Create: `dashboard/templates/scan.html`
- Modify: `dashboard/app.py` (add scan route with SSE)

- [ ] **Step 1: Add scan route with SSE endpoint**

Add to `dashboard/app.py`:

```python
import threading
from dashboard.sse import MessageAnnouncer, format_sse

# Per-user scan announcers (keyed by user email)
_scan_announcers: dict[str, MessageAnnouncer] = {}


@app.route("/scan")
@login_required
def scan_page():
    """Progressive scan page."""
    mailbox = request.args.get("mailbox", "")
    data_dir = g.data_dir
    scan_state = {}
    if mailbox:
        from dashboard.scan_state import load_scan_state
        scan_state = load_scan_state(mailbox, data_dir=data_dir)
    return render_template("scan.html", mailbox=mailbox, scan_state=scan_state)


@app.route("/scan/start", methods=["POST"])
@login_required
def scan_start():
    """Start or resume a scan for the given mailbox."""
    mailbox = request.form.get("mailbox", "")
    batch_size = int(request.form.get("batch_size", 500))
    user_email = g.user.email
    data_dir = g.data_dir

    ann = MessageAnnouncer()
    _scan_announcers[user_email] = ann

    def _run_scan():
        from auth.gmail_oauth import get_gmail_service
        from scanner.inbox_reader import fetch_emails
        from scanner.service_extractor import extract_services
        from dashboard.scan_state import load_scan_state, save_scan_state

        try:
            tokens_dir = data_dir / "tokens"
            service, email = get_gmail_service(
                email_hint=mailbox, tokens_dir=tokens_dir
            )

            # Get mailbox size estimate
            profile = service.users().getProfile(userId="me").execute()
            total = profile.get("messagesTotal", 0)
            ann.announce(format_sse(
                f'{{"total_estimate": {total}}}', event="estimate"
            ))

            # Load existing scan state for resume
            state = load_scan_state(mailbox, data_dir=data_dir)
            page_token = state.get("next_page_token")

            # Fetch batch
            emails = fetch_emails(service, max_results=batch_size)
            services = extract_services(emails)

            for i, svc in enumerate(services):
                ann.announce(format_sse(
                    f'{{"domain": "{svc["domain"]}", "name": "{svc["company_name"]}", "confidence": "{svc["confidence"]}"}}',
                    event="service",
                ))

            ann.announce(format_sse(
                f'{{"scanned": {len(emails)}, "services": {len(services)}, "done": true}}',
                event="progress",
            ))

            # Save scan state
            save_scan_state(mailbox, {
                "emails_scanned": state.get("emails_scanned", 0) + len(emails),
                "total_estimate": total,
                "services_found": [s["domain"] for s in services],
                "status": "paused",
            }, data_dir=data_dir)

        except Exception as e:
            ann.announce(format_sse(f'{{"error": "{str(e)}"}}', event="error"))

    thread = threading.Thread(target=_run_scan, daemon=True)
    thread.start()
    return "", 204


@app.route("/scan/stream")
@login_required
def scan_stream():
    """SSE endpoint for scan progress."""
    user_email = g.user.email
    ann = _scan_announcers.get(user_email)
    if not ann:
        return "No active scan", 404

    def stream():
        q = ann.listen()
        try:
            while True:
                msg = q.get(timeout=60)
                yield msg
        except Exception:
            pass

    return Response(
        stream(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
```

- [ ] **Step 2: Create scan template with HTMX SSE**

```html
<!-- dashboard/templates/scan.html -->
{% extends "base.html" %}
{% block title %}Scan — GDPR Agent{% endblock %}
{% block extra_head %}
<script src="https://unpkg.com/htmx.org@1.9.12/dist/ext/sse.js"></script>
{% endblock %}
{% block content %}
<div class="container mt-4">
  <h3>Scan Your Inbox</h3>

  {% if not mailbox %}
  <p>Connect a Gmail account first.</p>
  {% else %}
  <p>Scanning <strong>{{ mailbox }}</strong></p>

  <form method="POST" action="{{ url_for('scan_start') }}">
    <input type="hidden" name="mailbox" value="{{ mailbox }}">
    <button type="submit" name="batch_size" value="500" class="btn btn-primary me-2">
      Scan 500 emails
    </button>
    <button type="submit" name="batch_size" value="0" class="btn btn-outline-secondary">
      Scan entire mailbox
    </button>
  </form>

  {% if scan_state.get('emails_scanned') %}
  <p class="mt-2 text-muted">
    Previously scanned: {{ scan_state.emails_scanned }} emails,
    found {{ scan_state.services_found | length }} services
  </p>
  {% endif %}

  <div id="scan-progress" class="mt-3"
       hx-ext="sse" sse-connect="{{ url_for('scan_stream') }}">
    <div sse-swap="progress" hx-swap="innerHTML">
      <!-- Progress updates appear here -->
    </div>
    <div sse-swap="estimate" hx-swap="innerHTML">
      <!-- Mailbox size estimate appears here -->
    </div>
    <div id="services" sse-swap="service" hx-swap="beforeend">
      <!-- Service cards stream in here -->
    </div>
  </div>
  {% endif %}
</div>
{% endblock %}
```

- [ ] **Step 3: Commit**

```bash
git add dashboard/app.py dashboard/templates/scan.html
git commit -m "feat: add progressive scan page with SSE streaming"
```

---

## Task 13: Migration Script

**Files:**
- Create: `scripts/migrate_to_multiuser.py`
- Create: `tests/unit/test_migration.py`

- [ ] **Step 1: Write test for migration**

```python
# tests/unit/test_migration.py
import json
from pathlib import Path


def _setup_legacy_data(root: Path, email: str = "nemasha@gmail.com"):
    """Create a legacy single-user data layout."""
    (root / "tokens").mkdir(parents=True)
    (root / "tokens" / "nemasha_at_gmail_com_readonly.json").write_text("{}")
    (root / "tokens" / "nemasha_at_gmail_com_send.json").write_text("{}")

    (root / "reply_state.json").write_text(json.dumps({
        "nemasha_at_gmail_com": {
            "spotify.com": {"domain": "spotify.com", "company_name": "Spotify"}
        }
    }))

    (root / "sent_letters.json").write_text(json.dumps([
        {"company_name": "Spotify", "to_email": "privacy@spotify.com"}
    ]))

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
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `.venv/bin/pytest tests/unit/test_migration.py -v`
Expected: FAIL — `ModuleNotFoundError`

- [ ] **Step 3: Implement migration script**

```python
# scripts/migrate_to_multiuser.py
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
        # Write only this user's entries to the per-user file
        user_states = {}
        for key in list(data.keys()):
            user_states[key] = data[key]
        (user_dir / "reply_state.json").write_text(
            json.dumps(user_states, indent=2)
        )
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
    parser.add_argument("--email", required=True, help="Your Gmail address (becomes admin)")
    parser.add_argument("--name", required=True, help="Your full name")
    args = parser.parse_args()

    migrate(admin_email=args.email, admin_name=args.name)
```

- [ ] **Step 4: Run tests**

Run: `.venv/bin/pytest tests/unit/test_migration.py -v`
Expected: 3 PASSED

- [ ] **Step 5: Commit**

```bash
git add scripts/migrate_to_multiuser.py tests/unit/test_migration.py
git commit -m "feat: add single-user to multiuser data migration script"
```

---

## Task 14: Update CLI Entry Points (run.py, monitor.py)

**Files:**
- Modify: `run.py` (lines 33-106)
- Modify: `monitor.py` (lines 82-253)

- [ ] **Step 1: Update run.py to resolve data_dir**

At the top of `run.py`, after getting the Gmail service:

```python
# After: service, email = get_gmail_service(email_hint=args.gmail)
# Add:
from dashboard.user_model import user_data_dir
data_dir = user_data_dir(email)
data_dir.mkdir(parents=True, exist_ok=True)
tokens_dir = data_dir / "tokens"
```

Then pass `data_dir=data_dir` to all relevant function calls throughout `run.py`:
- `get_gmail_service(email_hint=args.gmail, tokens_dir=tokens_dir)` (line 49)
- Any calls to `record_sent` or `get_log` in the pipeline
- Pass `user_identity` dict to `compose()` calls

For the `user_identity`, build it from `users.json` or fall back to env vars:

```python
from dashboard.user_model import load_user

user = load_user(email)
if user:
    user_identity = {
        "user_full_name": user.name,
        "user_email": email,
        "user_address_line1": "",
        "user_address_city": "",
        "user_address_postcode": "",
        "user_address_country": "",
        "gdpr_framework": settings.gdpr_framework,
    }
else:
    # Legacy: no users.json yet, use env vars
    user_identity = None  # composer falls back to settings
```

- [ ] **Step 2: Update monitor.py to resolve data_dir**

Same pattern: after `service, email = get_gmail_service(...)`, resolve `data_dir`:

```python
from dashboard.user_model import user_data_dir
data_dir = user_data_dir(email)
```

Pass `data_dir=data_dir` to `load_state`, `save_state`, and any tracker calls.

- [ ] **Step 3: Run existing unit tests**

Run: `.venv/bin/pytest tests/unit/ -q`
Expected: All pass (CLI entry points are not unit-tested, but their callees are)

- [ ] **Step 4: Commit**

```bash
git add run.py monitor.py
git commit -m "feat: update CLI entry points to use per-user data directories"
```

---

## Task 15: Export & Delete Account Routes

**Files:**
- Modify: `dashboard/app.py` (add routes)
- Modify: `dashboard/admin_routes.py` or create new settings routes

- [ ] **Step 1: Add export route**

```python
# In dashboard/app.py (or a new dashboard/settings_routes.py)
import zipfile
import io

@app.route("/settings/export")
@login_required
def export_data():
    """Download a zip of the user's data directory."""
    data_dir = g.data_dir
    if not data_dir.exists():
        return "No data found.", 404

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for file_path in data_dir.rglob("*"):
            if file_path.is_file():
                arcname = file_path.relative_to(data_dir)
                zf.write(file_path, arcname)

    buf.seek(0)
    safe = _safe_email(g.user.email)
    return Response(
        buf.getvalue(),
        mimetype="application/zip",
        headers={"Content-Disposition": f"attachment; filename=gdpr-agent-{safe}.zip"},
    )
```

- [ ] **Step 2: Add delete account route**

```python
@app.route("/settings/delete-account", methods=["POST"])
@login_required
def delete_account():
    """Delete the current user's account and all their data."""
    import shutil
    from dashboard.user_model import delete_user

    email = g.user.email
    data_dir = g.data_dir

    # Remove user data directory
    if data_dir.exists():
        shutil.rmtree(data_dir)

    # Remove from users.json
    delete_user(email, path=_USER_DATA / "users.json")

    # Log out
    from flask_login import logout_user
    logout_user()

    return redirect(url_for("auth.login"))
```

- [ ] **Step 3: Commit**

```bash
git add dashboard/app.py
git commit -m "feat: add data export and account deletion routes"
```

---

## Task 16: End-to-End Smoke Test

**Files:** None created — manual testing checklist.

- [ ] **Step 1: Run migration on existing data**

```bash
python scripts/migrate_to_multiuser.py --email YOUR_EMAIL --name "Your Name"
```

Verify: `user_data/YOUR_SAFE_EMAIL/` directory created with tokens, state files, received data.

- [ ] **Step 2: Start dashboard and verify login**

```bash
python dashboard/app.py
```

Open `http://localhost:5001` — should redirect to `/login`. Sign in with Google → should redirect to dashboard showing your existing data.

- [ ] **Step 3: Test invite flow**

As admin, visit `/admin/invite` → enter a test email → copy the invite link → open in incognito → should show onboarding page.

- [ ] **Step 4: Test multi-mailbox tabs**

Connect a second Gmail account → verify tab bar shows both mailboxes + "All" tab → verify data isolation (each tab shows only that mailbox's companies).

- [ ] **Step 5: Test progressive scan**

Click "+" to add a new mailbox → after OAuth, should redirect to scan page → click "Scan 500 emails" → verify SSE progress and service cards stream in.

- [ ] **Step 6: Run full test suite**

```bash
.venv/bin/pytest tests/unit/ -v
```

Expected: All tests pass.

- [ ] **Step 7: Commit any fixes from smoke testing**

```bash
git add -A
git commit -m "fix: address issues found during multiuser smoke testing"
```
