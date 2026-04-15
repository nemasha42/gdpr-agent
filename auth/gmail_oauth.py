"""Gmail OAuth2 authentication — desktop app flow, per-account token storage."""

import sys
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]
SEND_SCOPES = ["https://www.googleapis.com/auth/gmail.send"]

_PROJECT_ROOT = Path(__file__).parent.parent
_CREDENTIALS_PATH = _PROJECT_ROOT / "credentials.json"
_TOKENS_DIR = _PROJECT_ROOT / "user_data" / "tokens"

# Legacy flat token paths — migrated automatically on first run
_LEGACY_TOKEN_PATH = _PROJECT_ROOT / "user_data" / "token.json"
_LEGACY_SEND_TOKEN_PATH = _PROJECT_ROOT / "user_data" / "token_send.json"


# ---------------------------------------------------------------------------
# OAuth call logger — persistent counter + TSV log
# ---------------------------------------------------------------------------

_LOG_PATH = _PROJECT_ROOT / "user_data" / "oauth_calls.log"
_log_lock = threading.Lock()
_call_counter = 0
_counter_loaded = False


def _load_counter() -> int:
    """Read the last counter value from the log file."""
    global _call_counter, _counter_loaded
    if _counter_loaded:
        return _call_counter
    _counter_loaded = True
    if _LOG_PATH.exists():
        try:
            last_line = ""
            with open(_LOG_PATH) as f:
                for last_line in f:
                    pass
            if last_line.strip():
                _call_counter = int(last_line.split("\t", 1)[0])
        except (ValueError, OSError):
            pass
    return _call_counter


def _log_oauth_call(function: str, reason: str, user: str = "") -> None:
    """Append one line to oauth_calls.log.  Thread-safe."""
    global _call_counter
    caller = ""
    try:
        frame = sys._getframe(2)
        caller = f"{Path(frame.f_code.co_filename).name}:{frame.f_code.co_name}:{frame.f_lineno}"
    except (ValueError, AttributeError):
        pass
    with _log_lock:
        _load_counter()
        _call_counter += 1
        ts = datetime.now(timezone.utc).isoformat(timespec="seconds")
        line = f"{_call_counter}\t{ts}\t{function}\t{reason}\t{user}\t{caller}\n"
        try:
            _LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
            with open(_LOG_PATH, "a") as f:
                f.write(line)
        except OSError:
            pass


# ---------------------------------------------------------------------------
# In-memory service cache (TTL-based)
# ---------------------------------------------------------------------------

_service_cache: dict[tuple, tuple[Any, str, float]] = {}
_cache_lock = threading.Lock()
_CACHE_TTL = 300  # 5 minutes


def _cache_get(
    email: str, scope: str, tokens_dir: Path
) -> tuple[Any, str] | None:
    key = (email, scope, str(tokens_dir))
    with _cache_lock:
        entry = _service_cache.get(key)
        if entry and (time.monotonic() - entry[2]) < _CACHE_TTL:
            return entry[0], entry[1]
        if entry:
            del _service_cache[key]
    return None


def _cache_put(
    email: str, scope: str, tokens_dir: Path, service: Any, resolved_email: str
) -> None:
    key = (email, scope, str(tokens_dir))
    with _cache_lock:
        _service_cache[key] = (service, resolved_email, time.monotonic())


def clear_service_cache() -> None:
    """Drop all cached services.  Useful in tests."""
    with _cache_lock:
        _service_cache.clear()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _safe_email(email: str) -> str:
    """Return a filesystem-safe version of an email address."""
    return email.replace("@", "_at_").replace(".", "_")


def _get_account_email(service: Any) -> str:
    """Return the authenticated account's email address via Gmail API."""
    return service.users().getProfile(userId="me").execute()["emailAddress"]


def _token_files_to_emails(paths: list[Path]) -> list[str]:
    """Convert *_readonly.json token filenames back to email addresses."""
    emails = []
    for p in sorted(paths):
        name = p.stem.replace("_readonly", "")   # trader1620_at_gmail_com
        # reverse _safe_email: first _at_ → @, remaining _ → .
        if "_at_" in name:
            local, domain = name.split("_at_", 1)
            emails.append(f"{local}@{domain.replace('_', '.')}")
        else:
            emails.append(name)
    return emails


def _load_creds(token_path: Path, scopes: list[str]) -> Credentials | None:
    if token_path.exists():
        return Credentials.from_authorized_user_file(str(token_path), scopes)
    return None


def _refresh_or_auth(
    creds: Credentials | None,
    scopes: list[str],
    credentials_path: Path,
    login_hint: str | None,
) -> Credentials:
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(Request())
        return creds
    flow = InstalledAppFlow.from_client_secrets_file(str(credentials_path), scopes)
    kwargs: dict = {}
    if login_hint:
        kwargs["login_hint"] = login_hint
    return flow.run_local_server(port=0, **kwargs)


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def get_gmail_service(
    email_hint: str | None = None,
    credentials_path: Path = _CREDENTIALS_PATH,
    tokens_dir: Path = _TOKENS_DIR,
) -> tuple[Any, str]:
    """Return (service, authenticated_email) for a Gmail readonly connection.

    On first run, opens a browser for OAuth consent and saves the token under
    user_data/tokens/{email}_readonly.json.  Subsequent runs load the cached
    token silently.

    Args:
        email_hint: Gmail address to use (e.g. "user@gmail.com").  If omitted
                    and exactly one token exists, that account is used.  If
                    multiple tokens exist, the tool exits with a list of known
                    accounts and asks the caller to re-run with --gmail EMAIL.
    """
    # ── Check cache first ────────────────────────────────────────────────────
    if email_hint:
        cached = _cache_get(email_hint, "readonly", tokens_dir)
        if cached:
            _log_oauth_call("get_gmail_service", "cache_hit", email_hint)
            return cached

    tokens_dir.mkdir(parents=True, exist_ok=True)

    # ── Auto-migrate legacy flat token ──────────────────────────────────────
    existing_readonly = list(tokens_dir.glob("*_readonly.json"))
    if _LEGACY_TOKEN_PATH.exists() and not existing_readonly:
        creds = _load_creds(_LEGACY_TOKEN_PATH, SCOPES)
        if creds:
            tmp_service = build("gmail", "v1", credentials=creds)
            email = _get_account_email(tmp_service)
            new_path = tokens_dir / f"{_safe_email(email)}_readonly.json"
            new_path.write_text(_LEGACY_TOKEN_PATH.read_text())
            _LEGACY_TOKEN_PATH.unlink()
            print(f"Migrated existing token for {email} to per-account storage.")
            _log_oauth_call("get_gmail_service", "legacy_migration", email)
            _cache_put(email, "readonly", tokens_dir, tmp_service, email)
            return tmp_service, email

    # ── Resolve token path ───────────────────────────────────────────────────
    if email_hint:
        token_path = tokens_dir / f"{_safe_email(email_hint)}_readonly.json"
    else:
        existing_readonly = list(tokens_dir.glob("*_readonly.json"))
        if len(existing_readonly) == 1:
            token_path = existing_readonly[0]
        elif len(existing_readonly) > 1:
            known = _token_files_to_emails(existing_readonly)
            print("Multiple Gmail accounts found:")
            for i, a in enumerate(known, 1):
                print(f"  [{i}] {a}")
            choice = input("\n  Which account to scan? Enter number or full email: ").strip()
            if choice.isdigit() and 1 <= int(choice) <= len(known):
                email_hint = known[int(choice) - 1]
            elif choice in known:
                email_hint = choice
            else:
                print("Invalid choice. Re-run and try again.")
                sys.exit(1)
            token_path = tokens_dir / f"{_safe_email(email_hint)}_readonly.json"
        else:
            # No tokens yet — ask which account to connect
            email_hint = input("  Enter Gmail address to scan: ").strip() or None
            token_path = tokens_dir / (
                f"{_safe_email(email_hint)}_readonly.json" if email_hint
                else "_pending_readonly.json"
            )

    # ── Load / refresh / auth ────────────────────────────────────────────────
    creds = _load_creds(token_path, SCOPES)
    had_creds = creds is not None
    if not creds or not creds.valid:
        creds = _refresh_or_auth(creds, SCOPES, credentials_path, email_hint)

    service = build("gmail", "v1", credentials=creds)

    # Skip the getProfile API call when we already know the email
    if email_hint and had_creds:
        email = email_hint
        reason = "disk_load_skip_profile"
    else:
        email = _get_account_email(service)
        reason = "browser_auth" if not had_creds else "disk_load"

    # Save under the correct email-based filename
    final_path = tokens_dir / f"{_safe_email(email)}_readonly.json"
    if token_path != final_path:
        token_path.unlink(missing_ok=True)
    final_path.parent.mkdir(parents=True, exist_ok=True)
    final_path.write_text(creds.to_json())

    _log_oauth_call("get_gmail_service", reason, email)
    _cache_put(email, "readonly", tokens_dir, service, email)
    return service, email


def check_send_token_valid(
    email: str,
    tokens_dir: Path = _TOKENS_DIR,
) -> tuple[bool, str]:
    """Return (is_valid, error_message).

    Attempts a silent token refresh if the token is expired.  Does NOT open a
    browser.  Returns (False, reason) if the token is missing, invalid, or the
    refresh failed (e.g. revoked).
    """
    token_path = tokens_dir / f"{_safe_email(email)}_send.json"
    if not token_path.exists():
        _log_oauth_call("check_send_token_valid", "missing", email)
        return False, "No send token found"
    creds = _load_creds(token_path, SEND_SCOPES)
    if creds is None:
        _log_oauth_call("check_send_token_valid", "load_failed", email)
        return False, "Could not load send token"
    if creds.valid:
        _log_oauth_call("check_send_token_valid", "valid", email)
        return True, ""
    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            token_path.write_text(creds.to_json())
            _log_oauth_call("check_send_token_valid", "refreshed", email)
            return True, ""
        except Exception as exc:
            _log_oauth_call("check_send_token_valid", "refresh_failed", email)
            return False, str(exc)
    _log_oauth_call("check_send_token_valid", "no_refresh_token", email)
    return False, "Send token invalid — no refresh token"


def get_gmail_send_service(
    email: str,
    credentials_path: Path = _CREDENTIALS_PATH,
    tokens_dir: Path = _TOKENS_DIR,
) -> Any:
    """Return an authenticated Gmail API service with send permission.

    Args:
        email: The Gmail account address — must match the scan account so that
               the send token is isolated per account.
    """
    # ── Check cache first ────────────────────────────────────────────────────
    cached = _cache_get(email, "send", tokens_dir)
    if cached:
        _log_oauth_call("get_gmail_send_service", "cache_hit", email)
        return cached[0]  # service only, no email in return

    tokens_dir.mkdir(parents=True, exist_ok=True)

    # ── Auto-migrate legacy flat send token ──────────────────────────────────
    existing_send = list(tokens_dir.glob("*_send.json"))
    if _LEGACY_SEND_TOKEN_PATH.exists() and not existing_send:
        new_path = tokens_dir / f"{_safe_email(email)}_send.json"
        new_path.write_text(_LEGACY_SEND_TOKEN_PATH.read_text())
        _LEGACY_SEND_TOKEN_PATH.unlink()
        print(f"Migrated existing send token for {email} to per-account storage.")

    token_path = tokens_dir / f"{_safe_email(email)}_send.json"
    creds = _load_creds(token_path, SEND_SCOPES)
    had_creds = creds is not None

    if not creds or not creds.valid:
        creds = _refresh_or_auth(creds, SEND_SCOPES, credentials_path, email)
        token_path.write_text(creds.to_json())

    service = build("gmail", "v1", credentials=creds)
    reason = "disk_load" if had_creds else "browser_auth"
    _log_oauth_call("get_gmail_send_service", reason, email)
    _cache_put(email, "send", tokens_dir, service, email)
    return service
