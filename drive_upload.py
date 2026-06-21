"""
Uploads the generated invoice PDF to Google Drive and returns a public,
link-shareable URL.

Two auth modes are supported:

  OAuth (recommended for personal/free Gmail accounts):
    Uploads as YOUR real Google account, which has normal storage quota.
    Requires a one-time browser login the first time the agent runs; after
    that, a cached refresh token is reused automatically with no prompt.
    Set GOOGLE_OAUTH_CLIENT_SECRET_FILE (and optionally GOOGLE_OAUTH_TOKEN_FILE).

  Service account (only works on paid Google Workspace + Shared Drives):
    Service accounts have ZERO personal storage quota on free Gmail. Files
    created by a service account fail with storageQuotaExceeded unless the
    destination is a Shared Drive (a Workspace-only feature). Kept here for
    Workspace users who have a Shared Drive folder id to upload into.
    Set GOOGLE_SERVICE_ACCOUNT_FILE or GOOGLE_SERVICE_ACCOUNT_JSON.

If both are configured, OAuth takes priority.

Reads env vars at call time, not import time, so behavior is correct
regardless of when dotenv is loaded relative to this import.
"""

from __future__ import annotations

import json
import os

from google.auth.transport.requests import Request as GoogleAuthRequest
from google.oauth2 import service_account
from google.oauth2.credentials import Credentials as OAuthCredentials
from google_auth_oauthlib.flow import InstalledAppFlow
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

_SCOPES = ["https://www.googleapis.com/auth/drive"]


def _cfg() -> dict:
    return {
        "oauth_client_secret_file": (
            os.getenv("GOOGLE_OAUTH_CLIENT_SECRET_FILE", "") or ""
        ).strip(),
        "oauth_token_file": (
            os.getenv("GOOGLE_OAUTH_TOKEN_FILE", "google_oauth_token.json") or ""
        ).strip(),
        "service_account_file": (os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "") or "").strip(),
        "service_account_json": (os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "") or "").strip(),
        "folder_id": (os.getenv("GOOGLE_DRIVE_FOLDER_ID", "") or "").strip(),
    }


def is_configured() -> bool:
    c = _cfg()
    if c["oauth_client_secret_file"] and os.path.isfile(c["oauth_client_secret_file"]):
        return True
    if c["service_account_json"]:
        return True
    return bool(c["service_account_file"] and os.path.isfile(c["service_account_file"]))


def _oauth_credentials(client_secret_file: str, token_file: str):
    creds = None
    if os.path.isfile(token_file):
        creds = OAuthCredentials.from_authorized_user_file(token_file, _SCOPES)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(GoogleAuthRequest())
    if not creds or not creds.valid:
        # First run only: opens a browser for you to log into YOUR Google
        # account and grant access. After this, token_file caches it so
        # every later run is silent.
        flow = InstalledAppFlow.from_client_secrets_file(client_secret_file, _SCOPES)
        creds = flow.run_local_server(port=0)
    with open(token_file, "w") as f:
        f.write(creds.to_json())
    return creds


def _credentials():
    c = _cfg()
    if c["oauth_client_secret_file"] and os.path.isfile(c["oauth_client_secret_file"]):
        return _oauth_credentials(c["oauth_client_secret_file"], c["oauth_token_file"])
    if c["service_account_json"]:
        info = json.loads(c["service_account_json"])
        return service_account.Credentials.from_service_account_info(info, scopes=_SCOPES)
    return service_account.Credentials.from_service_account_file(
        c["service_account_file"], scopes=_SCOPES
    )


def _drive_client():
    return build("drive", "v3", credentials=_credentials(), cache_discovery=False)


def upload_invoice_and_get_link(local_path: str, drive_filename: str) -> str | None:
    """
    Uploads local_path to Google Drive, makes it public-readable, and returns
    a shareable view link. Returns None if not configured or the call fails.
    """
    if not is_configured():
        return None
    try:
        service = _drive_client()
        c = _cfg()

        file_metadata: dict = {"name": drive_filename, "mimeType": "application/pdf"}
        if c["folder_id"]:
            file_metadata["parents"] = [c["folder_id"]]

        media = MediaFileUpload(local_path, mimetype="application/pdf", resumable=False)
        uploaded = (
            service.files()
            .create(body=file_metadata, media_body=media, fields="id, webViewLink")
            .execute()
        )
        file_id = uploaded["id"]

        # Anyone with the link can view/download -- no sign-in required.
        service.permissions().create(
            fileId=file_id,
            body={"type": "anyone", "role": "reader"},
            fields="id",
        ).execute()

        refreshed = service.files().get(fileId=file_id, fields="webViewLink").execute()
        return refreshed.get("webViewLink") or uploaded.get("webViewLink")
    except Exception:
        import traceback

        traceback.print_exc()
        return None