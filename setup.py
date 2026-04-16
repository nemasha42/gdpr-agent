"""First-time setup: guides a new user through creating their own Google Cloud
credentials so they can run the GDPR agent against their own Gmail account.

Usage:
    python setup.py

No tools to install — opens Google Cloud Console URLs in your browser and
walks you through ~13 clicks. Run once; produces credentials.json.
"""

import json
import time
import webbrowser
from pathlib import Path

_PROJECT_ROOT = Path(__file__).parent
_CREDENTIALS_PATH = _PROJECT_ROOT / "credentials.json"

_BOLD = "\033[1m"
_GREEN = "\033[32m"
_YELLOW = "\033[33m"
_RESET = "\033[0m"


def _header(text: str) -> None:
    print(f"\n{_BOLD}{text}{_RESET}")


def _step(n: int, total: int, text: str) -> None:
    print(f"\n{_BOLD}[{n}/{total}]{_RESET} {text}")


def _open(url: str) -> None:
    print(f"  Opening: {url}")
    webbrowser.open(url)


def _prompt(text: str) -> str:
    return input(f"\n  {_YELLOW}>{_RESET} {text}: ").strip()


def _ok(text: str) -> None:
    print(f"  {_GREEN}✓{_RESET} {text}")


def main() -> None:
    _header("GDPR Agent — Google Cloud Setup")
    print(
        "\nThis script sets up your own Google Cloud project so you can scan\n"
        "your Gmail inbox. It opens each step in your browser and waits.\n"
        "You will need a Google account (any Gmail address).\n"
    )

    if _CREDENTIALS_PATH.exists():
        print(f"{_GREEN}credentials.json already exists.{_RESET}")
        print("If it's working, you're done. To start fresh, delete it and re-run.")
        return

    input(f"  Press {_BOLD}Enter{_RESET} to begin...")

    total = 5

    # ── Step 1: Create project ───────────────────────────────────────────────
    _step(1, total, "Create a Google Cloud project")
    print("  Name it anything, e.g.  gdpr-agent")
    _open("https://console.cloud.google.com/projectcreate")
    project_id = _prompt(
        "Paste the Project ID shown after creation (e.g. gdpr-agent-123456)"
    )
    if not project_id:
        print("Project ID cannot be empty. Re-run and paste the ID.")
        return

    # ── Step 2: Enable Gmail API ─────────────────────────────────────────────
    _step(2, total, "Enable the Gmail API")
    print("  Click the blue  Enable  button on the page that opens.")
    _open(
        f"https://console.cloud.google.com/apis/library/gmail.googleapis.com?project={project_id}"
    )
    _prompt("Press Enter once the Gmail API shows as  Enabled")

    # ── Step 3: Configure OAuth consent screen ───────────────────────────────
    _step(3, total, "Configure the OAuth consent screen")
    print(
        "  On the page that opens:\n"
        "    1. Select  External  and click  Create\n"
        "    2. Fill in  App name  (e.g. GDPR Agent)  and  User support email\n"
        "    3. Scroll down, fill in  Developer contact email\n"
        "    4. Click  Save and Continue  through all screens until  Back to Dashboard"
    )
    _open(
        f"https://console.cloud.google.com/apis/credentials/consent?project={project_id}"
    )
    _prompt("Press Enter once you're back at the Dashboard")

    # ── Step 4: Create OAuth Desktop client ─────────────────────────────────
    _step(4, total, "Create an OAuth 2.0 Desktop client")
    print(
        "  On the page that opens:\n"
        "    1. Select application type:  Desktop app\n"
        "    2. Name it anything (e.g. GDPR Agent)\n"
        "    3. Click  Create\n"
        "    4. Click  Download JSON  (the ↓ icon on the right)\n"
        f"    5. Move the downloaded file to this folder and rename it  credentials.json\n"
        f"       ({_PROJECT_ROOT})"
    )
    _open(
        f"https://console.cloud.google.com/apis/credentials/oauthclient?project={project_id}"
    )

    # ── Step 5: Wait for credentials.json ────────────────────────────────────
    _step(5, total, "Waiting for credentials.json...")
    print(f"  Expected location: {_CREDENTIALS_PATH}")

    for _ in range(60):  # wait up to 2 minutes
        if _CREDENTIALS_PATH.exists():
            break
        time.sleep(2)
    else:
        print(
            "\nTimed out waiting. Move credentials.json to the project root and re-run."
        )
        return

    # Validate it looks right
    try:
        data = json.loads(_CREDENTIALS_PATH.read_text())
        assert "installed" in data
        _ok("credentials.json is valid.")
    except Exception:
        print(
            "\ncredentials.json doesn't look right. Download it again from the Console."
        )
        return

    print(
        f"\n{_GREEN}{_BOLD}Setup complete!{_RESET}\n\n"
        "Next steps:\n"
        "  1. Copy .env.example to .env and fill in your details\n"
        "  2. Run:  python run.py --dry-run\n"
        "     (A browser window will open to grant Gmail read access — sign in\n"
        "      with the same Google account you used above.)\n"
    )


if __name__ == "__main__":
    main()
