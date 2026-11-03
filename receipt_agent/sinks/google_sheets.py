"""Writes receipts to a Google Sheet."""

from __future__ import annotations

import logging

import gspread

from ..google_auth import google_credentials
from ..models import ReceiptRow

log = logging.getLogger(__name__)

TAB = "Receipts"


class GoogleSheetsSink:
    def __init__(self, credentials_file: str, token_file: str, sheet_id: str | None):
        gc = gspread.authorize(google_credentials(credentials_file, token_file))
        if sheet_id:
            self.book = gc.open_by_key(sheet_id)
        else:
            self.book = gc.create("Expenses (from email receipts)")
            print(
                f"\nCreated a new Google Sheet: {self.book.url}\n"
                f"Put this line in your .env so the next runs use it:\n"
                f"GOOGLE_SHEET_ID={self.book.id}\n"
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
