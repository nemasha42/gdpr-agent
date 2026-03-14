"""Gmail OAuth2 authentication — desktop app flow."""

from pathlib import Path
from typing import Any

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

_PROJECT_ROOT = Path(__file__).parent.parent
_CREDENTIALS_PATH = _PROJECT_ROOT / "credentials.json"
_TOKEN_PATH = _PROJECT_ROOT / "user_data" / "token.json"


def get_gmail_service(
    credentials_path: Path = _CREDENTIALS_PATH,
    token_path: Path = _TOKEN_PATH,
) -> Any:
    """Return an authenticated Gmail API service object.

    On first run, opens a browser for the user to approve access and saves
    the token to token_path. Subsequent runs load and auto-refresh the token.

    Args:
        credentials_path: Path to the OAuth2 credentials JSON file.
        token_path: Path where the user token is persisted.

    Returns:
        Authenticated Gmail API service (googleapiclient Resource).
    """
    creds: Credentials | None = None

    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)

    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            flow = InstalledAppFlow.from_client_secrets_file(
                str(credentials_path), SCOPES
            )
            creds = flow.run_local_server(port=0)

        token_path.parent.mkdir(parents=True, exist_ok=True)
        token_path.write_text(creds.to_json())

    return build("gmail", "v1", credentials=creds)
