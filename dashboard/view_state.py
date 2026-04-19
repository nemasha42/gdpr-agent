"""Track when the user last expanded the message thread for each domain.

Stores timestamps in user_data/view_state.json as:
  { "<safe_email_key>": { "<domain>": "<ISO datetime>" } }
"""

from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path

_log = logging.getLogger(__name__)

_VIEW_STATE_PATH = Path(__file__).parent.parent / "user_data" / "view_state.json"


def _safe_key(account: str) -> str:
    return account.replace("@", "_at_").replace(".", "_")


def _load() -> dict:
    try:
        return json.loads(_VIEW_STATE_PATH.read_text())
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def _save(data: dict) -> None:
    _VIEW_STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    _VIEW_STATE_PATH.write_text(json.dumps(data, indent=2))


def mark_viewed(account: str, domain: str) -> str:
    """Record that the user expanded the thread for *domain* just now.

    Returns the ISO timestamp that was saved.
    """
    data = _load()
    key = _safe_key(account)
    if key not in data:
        data[key] = {}
    now = datetime.now(timezone.utc).isoformat()
    data[key][domain] = now
    _save(data)
    return now


def last_viewed_at(account: str, domain: str) -> str:
    """Return ISO timestamp of last view, or empty string if never viewed."""
    data = _load()
    return data.get(_safe_key(account), {}).get(domain, "")


def has_new_messages(account: str, domain: str, replies: list) -> bool:
    """True if any GDPR reply arrived after the user last viewed this domain.

    *replies* is a list of ReplyRecord objects (from CompanyState.replies).
    """
    viewed = last_viewed_at(account, domain)
    if not viewed:
        # Never viewed — new if there are any company replies at all
        return any(
            "NON_GDPR" not in r.tags and "YOUR_REPLY" not in r.tags for r in replies
        )
    for r in replies:
        if "NON_GDPR" in r.tags or "YOUR_REPLY" in r.tags:
            continue
        if r.received_at > viewed:
            return True
    return False
