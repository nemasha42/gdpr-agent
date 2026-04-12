"""Data models for portal submission results and state."""

from dataclasses import dataclass, field
from datetime import datetime


@dataclass
class PortalResult:
    """Result of a portal submission attempt."""
    success: bool = False
    needs_manual: bool = False
    confirmation_ref: str = ""
    screenshot_path: str = ""
    error: str = ""
    portal_status: str = ""  # "submitted", "awaiting_verification", "manual", "failed"


@dataclass
class CaptchaChallenge:
    """State for a CAPTCHA relay between Playwright and the dashboard."""
    domain: str = ""
    portal_url: str = ""
    created_at: str = field(default_factory=lambda: datetime.now().isoformat(timespec="seconds"))
    status: str = "pending"  # "pending", "solved", "expired"
    solution: str = ""
    screenshot_path: str = ""
