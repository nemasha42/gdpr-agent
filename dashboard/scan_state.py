"""Load and save per-account scan state (user_data/scan_state.json)."""

import json
from pathlib import Path

_SCAN_STATE_PATH = Path(__file__).parent.parent / "user_data" / "scan_state.json"
_MAX_SCANNED_IDS = 10_000


def _safe_key(account: str) -> str:
    """Encode an email address as a safe dict key (mirrors state_manager.py)."""
    return account.replace("@", "_at_").replace(".", "_")


def _load_all(path: Path = _SCAN_STATE_PATH) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return {}


def load_scan_state(account: str, *, path: Path | None = None, data_dir: Path | None = None) -> dict:
    """Return the scan state dict for *account*, or a fresh empty default."""
    if path is None:
        path = (data_dir / "scan_state.json") if data_dir else _SCAN_STATE_PATH
    all_data = _load_all(path)
    key = _safe_key(account)
    return all_data.get(key, {
        "last_scan_at": None,
        "scanned_message_ids": [],
        "discovered_companies": {},
    })


def save_scan_state(account: str, state: dict, *, path: Path | None = None, data_dir: Path | None = None) -> None:
    """Persist *state* for *account*, merging with other accounts in the file."""
    if path is None:
        path = (data_dir / "scan_state.json") if data_dir else _SCAN_STATE_PATH

    # Prune scanned_message_ids to avoid unbounded growth
    ids = state.get("scanned_message_ids", [])
    if len(ids) > _MAX_SCANNED_IDS:
        state["scanned_message_ids"] = ids[-_MAX_SCANNED_IDS:]

    all_data = _load_all(path)
    all_data[_safe_key(account)] = state
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(all_data, indent=2))


def get_all_accounts(*, path: Path = _SCAN_STATE_PATH) -> list[str]:
    """Return all account keys that have scan state (as raw safe-key strings)."""
    return list(_load_all(path).keys())
