"""OAuth credential management for Gmail API (read-only)."""

import json
import sys
from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = ["https://www.googleapis.com/auth/gmail.readonly"]

CONFIG_DIR = Path.home() / ".gmail-obsidian"
CREDENTIALS_FILE = CONFIG_DIR / "credentials.json"
TOKEN_FILE = CONFIG_DIR / "token.json"


def ensure_config_dir() -> None:
    """Create the config directory if it doesn't exist."""
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)


def load_credentials() -> Credentials | None:
    """Load stored OAuth credentials, refreshing if expired.

    Returns:
        Valid credentials, or None if no stored credentials or refresh fails.
    """
    if not TOKEN_FILE.exists():
        return None

    creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)

    if creds.valid:
        return creds

    if creds.expired and creds.refresh_token:
        try:
            creds.refresh(Request())
            _save_credentials(creds)
            return creds
        except Exception as e:
            print(f"Token refresh failed: {e}", file=sys.stderr)
            print("Run 'gmail-sync auth' to re-authenticate.", file=sys.stderr)
            return None

    return None


def run_auth_flow() -> Credentials:
    """Run the OAuth browser flow for initial authentication.

    Requires ~/.gmail-obsidian/credentials.json (GCP OAuth client ID).

    Returns:
        Authenticated credentials with gmail.readonly scope.

    Raises:
        FileNotFoundError: If credentials.json is missing.
    """
    if not CREDENTIALS_FILE.exists():
        print(
            f"Missing {CREDENTIALS_FILE}\n\n"
            "To set up Gmail API access:\n"
            "1. Go to https://console.cloud.google.com/\n"
            "2. Create a project and enable the Gmail API\n"
            "3. Create OAuth credentials (Desktop app type)\n"
            "4. Download the JSON and save it as:\n"
            f"   {CREDENTIALS_FILE}",
            file=sys.stderr,
        )
        raise FileNotFoundError(f"OAuth client ID file not found: {CREDENTIALS_FILE}")

    ensure_config_dir()
    flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), SCOPES)
    creds = flow.run_local_server(port=0)
    _save_credentials(creds)
    return creds


def get_credentials() -> Credentials:
    """Get valid credentials, running auth flow if needed.

    Returns:
        Valid OAuth credentials.

    Raises:
        SystemExit: If no stored credentials and auth flow cannot run.
    """
    creds = load_credentials()
    if creds is not None:
        return creds

    print("No valid credentials found. Run 'gmail-sync auth' first.", file=sys.stderr)
    sys.exit(1)


def _save_credentials(creds: Credentials) -> None:
    """Atomically save credentials to token.json."""
    ensure_config_dir()
    token_data = {
        "token": creds.token,
        "refresh_token": creds.refresh_token,
        "token_uri": creds.token_uri,
        "client_id": creds.client_id,
        "client_secret": creds.client_secret,
        "scopes": list(creds.scopes) if creds.scopes else SCOPES,
    }
    tmp = TOKEN_FILE.with_suffix(".tmp")
    tmp.write_text(json.dumps(token_data, indent=2))
    tmp.rename(TOKEN_FILE)
