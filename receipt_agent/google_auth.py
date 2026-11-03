"""One Google sign-in shared by Gmail (read) and Google Sheets (write)."""

from __future__ import annotations

from pathlib import Path

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from google_auth_oauthlib.flow import InstalledAppFlow

SCOPES = [
    "https://www.googleapis.com/auth/gmail.readonly",
    "https://www.googleapis.com/auth/spreadsheets",
]

_cache: dict[str, Credentials] = {}


def google_credentials(credentials_file: str, token_file: str) -> Credentials:
    if token_file in _cache:
        return _cache[token_file]
    token_path = Path(token_file)
    creds = None
    if token_path.exists():
        creds = Credentials.from_authorized_user_file(str(token_path), SCOPES)
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            creds.refresh(Request())
        else:
            if not Path(credentials_file).exists():
                raise FileNotFoundError(
                    f"{credentials_file} not found. See 'Set up Google' in the README."
                )
            flow = InstalledAppFlow.from_client_secrets_file(credentials_file, SCOPES)
            creds = flow.run_local_server(port=0)  # opens the browser to sign in once
        token_path.write_text(creds.to_json())
    _cache[token_file] = creds
    return creds
