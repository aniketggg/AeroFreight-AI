"""Optional Google Drive upload for generated invoice PDFs."""

from __future__ import annotations

import json
import os


def _cfg() -> dict:
    return {
        "oauth_client_secret_file": (
            os.getenv("GOOGLE_OAUTH_CLIENT_SECRET_FILE", "") or ""
        ).strip(),
        "oauth_token_file": (
            os.getenv("GOOGLE_OAUTH_TOKEN_FILE", "google_oauth_token.json") or ""
        ).strip(),
        "service_account_file": (
            os.getenv("GOOGLE_SERVICE_ACCOUNT_FILE", "") or ""
        ).strip(),
        "service_account_json": (
            os.getenv("GOOGLE_SERVICE_ACCOUNT_JSON", "") or ""
        ).strip(),
        "folder_id": (os.getenv("GOOGLE_DRIVE_FOLDER_ID", "") or "").strip(),
    }


def is_configured() -> bool:
    config = _cfg()
    if config["oauth_client_secret_file"] and os.path.isfile(
        config["oauth_client_secret_file"]
    ):
        return True
    if config["service_account_json"]:
        return True
    return bool(
        config["service_account_file"]
        and os.path.isfile(config["service_account_file"])
    )


def _oauth_credentials(client_secret_file: str, token_file: str):
    from google.auth.transport.requests import Request as GoogleAuthRequest
    from google.oauth2.credentials import Credentials as OAuthCredentials
    from google_auth_oauthlib.flow import InstalledAppFlow

    scopes = ["https://www.googleapis.com/auth/drive"]
    creds = None
    if os.path.isfile(token_file):
        creds = OAuthCredentials.from_authorized_user_file(token_file, scopes)
    if creds and creds.expired and creds.refresh_token:
        creds.refresh(GoogleAuthRequest())
    if not creds or not creds.valid:
        flow = InstalledAppFlow.from_client_secrets_file(
            client_secret_file,
            scopes,
        )
        creds = flow.run_local_server(port=0)
    with open(token_file, "w", encoding="utf-8") as handle:
        handle.write(creds.to_json())
    return creds


def _credentials():
    from google.oauth2 import service_account

    config = _cfg()
    scopes = ["https://www.googleapis.com/auth/drive"]
    if config["oauth_client_secret_file"] and os.path.isfile(
        config["oauth_client_secret_file"]
    ):
        return _oauth_credentials(
            config["oauth_client_secret_file"],
            config["oauth_token_file"],
        )
    if config["service_account_json"]:
        info = json.loads(config["service_account_json"])
        return service_account.Credentials.from_service_account_info(
            info,
            scopes=scopes,
        )
    return service_account.Credentials.from_service_account_file(
        config["service_account_file"],
        scopes=scopes,
    )


def _drive_client():
    from googleapiclient.discovery import build
    from googleapiclient.http import MediaFileUpload

    return build("drive", "v3", credentials=_credentials(), cache_discovery=False), MediaFileUpload


def upload_invoice_and_get_link(local_path: str, drive_filename: str) -> str | None:
    """Upload a PDF and return a shareable link, or None when unavailable."""
    if not is_configured():
        return None
    try:
        service, MediaFileUpload = _drive_client()
        config = _cfg()

        file_metadata: dict = {
            "name": drive_filename,
            "mimeType": "application/pdf",
        }
        if config["folder_id"]:
            file_metadata["parents"] = [config["folder_id"]]

        media = MediaFileUpload(
            local_path,
            mimetype="application/pdf",
            resumable=False,
        )
        uploaded = (
            service.files()
            .create(body=file_metadata, media_body=media, fields="id, webViewLink")
            .execute()
        )
        file_id = uploaded["id"]

        service.permissions().create(
            fileId=file_id,
            body={"type": "anyone", "role": "reader"},
            fields="id",
        ).execute()

        refreshed = (
            service.files()
            .get(fileId=file_id, fields="webViewLink")
            .execute()
        )
        return refreshed.get("webViewLink") or uploaded.get("webViewLink")
    except Exception:
        return None
