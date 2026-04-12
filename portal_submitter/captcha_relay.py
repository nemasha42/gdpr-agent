"""CAPTCHA relay: save screenshot for dashboard, poll for user solution."""

import json
import time
from pathlib import Path

from portal_submitter.models import CaptchaChallenge

_DEFAULT_BASE_DIR = Path(__file__).parent.parent / "user_data" / "captcha_pending"
_DEFAULT_TIMEOUT = 300  # 5 minutes
_DEFAULT_POLL_INTERVAL = 2  # seconds


def request_solve(
    domain: str,
    portal_url: str,
    screenshot_bytes: bytes,
    *,
    base_dir: Path = _DEFAULT_BASE_DIR,
) -> CaptchaChallenge:
    """Save a CAPTCHA screenshot and challenge file for the dashboard to display."""
    base_dir.mkdir(parents=True, exist_ok=True)

    screenshot_path = base_dir / f"{domain}.png"
    screenshot_path.write_bytes(screenshot_bytes)

    challenge = CaptchaChallenge(
        domain=domain,
        portal_url=portal_url,
        screenshot_path=str(screenshot_path),
    )
    challenge_path = _challenge_path(domain, base_dir)
    challenge_path.write_text(json.dumps({
        "domain": challenge.domain,
        "portal_url": challenge.portal_url,
        "created_at": challenge.created_at,
        "status": challenge.status,
        "solution": challenge.solution,
    }, indent=2))

    return challenge


def poll_solution(
    domain: str,
    *,
    base_dir: Path = _DEFAULT_BASE_DIR,
    timeout: float = _DEFAULT_TIMEOUT,
    poll_interval: float = _DEFAULT_POLL_INTERVAL,
) -> str | None:
    """Poll for a CAPTCHA solution written by the dashboard. Returns solution or None on timeout."""
    path = _challenge_path(domain, base_dir)
    deadline = time.monotonic() + timeout

    while time.monotonic() < deadline:
        try:
            data = json.loads(path.read_text())
            if data.get("status") == "solved" and data.get("solution"):
                _cleanup(domain, base_dir)
                return data["solution"]
        except (json.JSONDecodeError, FileNotFoundError):
            pass
        time.sleep(poll_interval)

    _cleanup(domain, base_dir)
    return None


def _challenge_path(domain: str, base_dir: Path) -> Path:
    return base_dir / f"{domain}.json"


def _cleanup(domain: str, base_dir: Path) -> None:
    """Remove pending CAPTCHA files."""
    for suffix in (".json", ".png"):
        path = base_dir / f"{domain}{suffix}"
        try:
            path.unlink(missing_ok=True)
        except Exception:
            pass
