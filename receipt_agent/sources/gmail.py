"""Reads emails from Gmail through the Gmail API (read-only)."""

from __future__ import annotations

import base64
import logging
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Iterator

from googleapiclient.discovery import build

from ..google_auth import google_credentials
from ..models import Email
from ..text import html_to_text

log = logging.getLogger(__name__)

# Gmail's own search narrows things down before Claude reads anything.
DEFAULT_QUERY = (
    "(receipt OR order OR invoice OR payment OR purchase OR paid OR subscription "
    "OR чек OR заказ OR оплата) -category:promotions"
)


class GmailSource:
    name = "gmail"

    def __init__(self, credentials_file: str, token_file: str, query: str = DEFAULT_QUERY):
        creds = google_credentials(credentials_file, token_file)
        self.service = build("gmail", "v1", credentials=creds, cache_discovery=False)
        self.query = query
        self.account = self.service.users().getProfile(userId="me").execute()["emailAddress"]

    def fetch(self, since_days: int) -> Iterator[Email]:
        q = f"{self.query} newer_than:{since_days}d"
        request = self.service.users().messages().list(userId="me", q=q, maxResults=100)
        while request is not None:
            page = request.execute()
            for ref in page.get("messages", []):
                msg = (
                    self.service.users()
                    .messages()
                    .get(userId="me", id=ref["id"], format="full")
                    .execute()
                )
                yield self._to_email(msg)
            request = self.service.users().messages().list_next(request, page)

    def _to_email(self, msg: dict) -> Email:
        headers = {h["name"].lower(): h["value"] for h in msg["payload"].get("headers", [])}
        try:
            received = parsedate_to_datetime(headers["date"])
        except (KeyError, TypeError, ValueError):
            received = datetime.fromtimestamp(int(msg["internalDate"]) / 1000, tz=timezone.utc)
        return Email(
            provider=self.name,
            account=self.account,
            message_id=msg["id"],
            subject=headers.get("subject", ""),
            sender=headers.get("from", ""),
            received_at=received,
            body=_body_text(msg["payload"]),
            link=f"https://mail.google.com/mail/u/{self.account}/#all/{msg['id']}",
        )


def _body_text(payload: dict) -> str:
    """Prefers the plain-text part; falls back to HTML converted to text."""
    plain, html = [], []

    def walk(part: dict) -> None:
        mime = part.get("mimeType", "")
        data = part.get("body", {}).get("data")
        if data:
            text = base64.urlsafe_b64decode(data).decode("utf-8", errors="replace")
            if mime == "text/plain":
                plain.append(text)
            elif mime == "text/html":
                html.append(text)
        for sub in part.get("parts", []) or []:
            walk(sub)

    walk(payload)
    if plain and sum(len(p.strip()) for p in plain) > 50:
        return "\n".join(plain)
    if html:
        return html_to_text("\n".join(html))
    return "\n".join(plain)
