"""Record sent SAR letters to user_data/sent_letters.json."""

import json
from datetime import datetime
from pathlib import Path

from letter_engine.models import SARLetter

_TRACKER_PATH = Path(__file__).parent.parent / "user_data" / "sent_letters.json"
_SUBPROCESSOR_REQUESTS_PATH = Path(__file__).parent.parent / "user_data" / "subprocessor_requests.json"


def record_sent(letter: SARLetter, *, path: Path | None = None, data_dir: Path | None = None) -> None:
    """Append a sent letter entry to the tracker file."""
    if path is None:
        path = (data_dir / "sent_letters.json") if data_dir else _TRACKER_PATH
    log = get_log(path=path)
    log.append({
        "sent_at": datetime.now().isoformat(timespec="seconds"),
        "company_name": letter.company_name,
        "method": letter.method,
        "to_email": letter.to_email,
        "subject": letter.subject,
        "gmail_message_id": letter.gmail_message_id,
        "gmail_thread_id": letter.gmail_thread_id,
    })
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(log, indent=2))


def record_subprocessor_request(
    letter: SARLetter,
    domain: str,
    *,
    path: Path | None = None,
    data_dir: Path | None = None,
) -> None:
    """Append a sent subprocessor disclosure request to the tracker file."""
    if path is None:
        path = (data_dir / "subprocessor_requests.json") if data_dir else _SUBPROCESSOR_REQUESTS_PATH
    log = get_log(path=path)
    log.append({
        "sent_at": datetime.now().isoformat(timespec="seconds"),
        "domain": domain,
        "company_name": letter.company_name,
        "method": letter.method,
        "to_email": letter.to_email,
        "subject": letter.subject,
        "gmail_message_id": letter.gmail_message_id,
        "gmail_thread_id": letter.gmail_thread_id,
    })
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(log, indent=2))


def get_log(*, path: Path | None = None, data_dir: Path | None = None) -> list[dict]:
    """Return all recorded sent letters, or [] if the file doesn't exist."""
    if path is None:
        path = (data_dir / "sent_letters.json") if data_dir else _TRACKER_PATH
    if not path.exists():
        return []
    try:
        return json.loads(path.read_text())
    except (json.JSONDecodeError, OSError):
        return []
