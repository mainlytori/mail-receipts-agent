"""Reads emails from Outlook / Hotmail / Microsoft 365 through Microsoft Graph (read-only)."""

from __future__ import annotations

import logging
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Iterator

import msal
import requests

from ..models import Email
from ..text import html_to_text

log = logging.getLogger(__name__)

GRAPH = "https://graph.microsoft.com/v1.0"
SCOPES = ["Mail.Read", "User.Read"]

# Graph can't combine a date filter with free-text search, so we filter by keyword here
# before Claude reads anything.
KEYWORDS = re.compile(
    r"receipt|order|invoice|payment|purchase|paid|subscription|чек|заказ|оплат",
    re.IGNORECASE,
)


class OutlookSource:
    name = "outlook"

    def __init__(self, client_id: str, token_cache_file: str, authority: str = "common"):
        self.cache_path = Path(token_cache_file)
        self.cache = msal.SerializableTokenCache()
        if self.cache_path.exists():
            self.cache.deserialize(self.cache_path.read_text())
        self.app = msal.PublicClientApplication(
            client_id,
            authority=f"https://login.microsoftonline.com/{authority}",
            token_cache=self.cache,
        )
        self.session = requests.Session()
        self.session.headers["Authorization"] = f"Bearer {self._token()}"
        self.session.headers["Prefer"] = 'outlook.body-content-type="text"'
        me = self._get(f"{GRAPH}/me")
        self.account = me.get("mail") or me.get("userPrincipalName", "outlook")

    def _token(self) -> str:
        result = None
        accounts = self.app.get_accounts()
        if accounts:
            result = self.app.acquire_token_silent(SCOPES, account=accounts[0])
        if not result:
            flow = self.app.initiate_device_flow(scopes=SCOPES)
            if "user_code" not in flow:
                raise RuntimeError(f"Could not start Microsoft sign-in: {flow}")
            print("\n" + flow["message"] + "\n")  # tells the user which code to type, and where
            result = self.app.acquire_token_by_device_flow(flow)
        if "access_token" not in result:
            raise RuntimeError(f"Microsoft sign-in failed: {result.get('error_description')}")
        if self.cache.has_state_changed:
            self.cache_path.write_text(self.cache.serialize())
        return result["access_token"]

    def _get(self, url: str, params: dict | None = None) -> dict:
        r = self.session.get(url, params=params, timeout=60)
        r.raise_for_status()
        return r.json()

    def fetch(self, since_days: int) -> Iterator[Email]:
        since = (datetime.now(timezone.utc) - timedelta(days=since_days)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
        url: str | None = f"{GRAPH}/me/messages"
        params: dict | None = {
            "$filter": f"receivedDateTime ge {since}",
            "$orderby": "receivedDateTime desc",
            "$select": "id,subject,from,receivedDateTime,body,webLink,inferenceClassification",
            "$top": "50",
        }
        while url:
            page = self._get(url, params)
            for m in page.get("value", []):
                subject = m.get("subject") or ""
                body = m.get("body", {})
                text = body.get("content", "")
                if body.get("contentType") == "html":
                    text = html_to_text(text)
                if not KEYWORDS.search(subject) and not KEYWORDS.search(text[:3000]):
                    continue
                sender = m.get("from", {}).get("emailAddress", {})
                yield Email(
                    provider=self.name,
                    account=self.account,
                    message_id=m["id"],
                    subject=subject,
                    sender=f"{sender.get('name', '')} <{sender.get('address', '')}>",
                    received_at=datetime.fromisoformat(
                        m["receivedDateTime"].replace("Z", "+00:00")
                    ),
                    body=text,
                    link=m.get("webLink", ""),
                )
            url, params = page.get("@odata.nextLink"), None
