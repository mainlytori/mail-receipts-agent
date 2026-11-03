"""Reads emails from Gmail through the Gmail API (read-only)."""

from __future__ import annotations

import base64
import logging
import socket
import time
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from typing import Iterator

from google.auth.exceptions import TransportError
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError

from ..google_auth import google_credentials
from ..models import Email
from ..text import html_to_text

log = logging.getLogger(__name__)

# Gmail's own search narrows things down before the AI reads anything.
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
        self.account = _run(self.service.users().getProfile(userId="me"))["emailAddress"]

    def fetch(self, since_days: int) -> Iterator[Email]:
        q = f"{self.query} newer_than:{since_days}d"
        request = self.service.users().messages().list(userId="me", q=q, maxResults=100)
        while request is not None:
            page = _run(request)
            for ref in page.get("messages", []):
                msg = _run(
                    self.service.users().messages().get(userId="me", id=ref["id"], format="full")
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


def _run(request, tries: int = 5):
    """Runs a Gmail request, waiting and retrying through short internet blips."""
    for attempt in range(1, tries + 1):
        try:
            return request.execute()
        except HttpError as e:
            if e.resp.status not in (429, 500, 502, 503, 504) or attempt == tries:
                raise
        except (TransportError, ConnectionError, socket.timeout, TimeoutError, OSError):
            if attempt == tries:
                raise
        wait = 2**attempt
        log.warning("Internet hiccup talking to Gmail, retrying in %ds...", wait)
        time.sleep(wait)
