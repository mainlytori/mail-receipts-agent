"""Writes receipts to a Google Sheet."""

from __future__ import annotations

import logging

import re

import gspread
from googleapiclient.discovery import build

from ..google_auth import google_credentials
from ..models import ReceiptRow

log = logging.getLogger(__name__)

TAB = "Receipts"


class GoogleSheetsSink:
    def __init__(self, credentials_file: str, token_file: str, sheet_id: str | None):
        creds = google_credentials(credentials_file, token_file)
        gc = gspread.authorize(creds)
        if sheet_id:
            self.book = gc.open_by_key(_sheet_id(sheet_id))
        else:
            # Create through the Sheets API: it only needs the spreadsheets permission
            # (gspread's own create() needs full Google Drive access).
            sheets = build("sheets", "v4", credentials=creds, cache_discovery=False)
            created = sheets.spreadsheets().create(
                body={"properties": {"title": "Expenses (from email receipts)"}},
                fields="spreadsheetId",
            ).execute()
            self.book = gc.open_by_key(created["spreadsheetId"])
            from dotenv import set_key

            set_key(".env", "GOOGLE_SHEET_ID", self.book.id, quote_mode="never")
            print(
                f"\nCreated your Google Sheet: {self.book.url}\n"
                "It's in your Google Drive. You can move it to any folder there and it keeps working.\n"
            )
        try:
            self.ws = self.book.worksheet(TAB)
        except gspread.WorksheetNotFound:
            first = self.book.sheet1
            if first.title in ("Sheet1", "Лист1") and not first.get_all_values():
                first.update_title(TAB)
                self.ws = first
            else:
                self.ws = self.book.add_worksheet(TAB, rows=1000, cols=len(ReceiptRow.HEADERS))
        if not self.ws.row_values(1):
            self.ws.append_row(ReceiptRow.HEADERS)
            self.ws.format("1:1", {"textFormat": {"bold": True}})
            self.ws.freeze(rows=1)

    @property
    def location(self) -> str:
        return self.book.url

    def existing_keys(self) -> set[str]:
        col = ReceiptRow.HEADERS.index("Email ID") + 1
        return set(self.ws.col_values(col)[1:])

    def append(self, rows: list[ReceiptRow]) -> None:
        if not rows:
            return
        values = []
        for row in rows:
            v = row.values()
            v = ["" if x is None else x for x in v]
            link_idx = ReceiptRow.HEADERS.index("Link to email")
            if v[link_idx]:
                url = str(v[link_idx]).replace('"', '""')
                v[link_idx] = f'=HYPERLINK("{url}", "Open email")'
            values.append(v)
        self.ws.append_rows(values, value_input_option="USER_ENTERED")


def _sheet_id(value: str) -> str:
    """Accepts a sheet ID or a full Google Sheets link."""
    m = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]+)", value)
    return m.group(1) if m else value.strip()
