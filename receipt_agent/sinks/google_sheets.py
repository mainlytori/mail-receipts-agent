"""Writes receipts to a Google Sheet."""

from __future__ import annotations

import logging

import re

import gspread
from googleapiclient.discovery import build

from ..google_auth import google_credentials
from ..models import ReceiptRow, migrate_row
from .layout import SUMMARY_TAB, col_letter, safe_cell

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
        self._ensure_layout()

    def _ensure_layout(self) -> None:
        header = self.ws.row_values(1)
        if header == ReceiptRow.HEADERS:
            return
        rows = []
        if header:
            # An older layout: move the existing receipts into the new columns.
            log.info("Updating your sheet to the new column layout...")
            old = self.ws.get_all_values(
                value_render_option="UNFORMATTED_VALUE",
                date_time_render_option="FORMATTED_STRING",
            )
            rows = [migrate_row(header, r) for r in old[1:] if any(str(c).strip() for c in r)]
            self.ws.clear()
        values = [ReceiptRow.HEADERS] + [[safe_cell(v) for v in r] for r in rows]
        self.ws.update(values, "A1", value_input_option="USER_ENTERED")
        last = col_letter(ReceiptRow.HEADERS[-1])
        self.ws.format(f"A1:{last}1", {"textFormat": {"bold": True}})
        self.ws.freeze(rows=1)
        self._write_summary()

    def _write_summary(self) -> None:
        """A "Summary" tab with live totals per currency and per type."""
        try:
            ws = self.book.worksheet(SUMMARY_TAB)
        except gspread.WorksheetNotFound:
            ws = self.book.add_worksheet(SUMMARY_TAB, rows=100, cols=8)
        sep = ";" if _comma_decimal(self.book) else ","
        t, c, ty = col_letter("Total"), col_letter("Currency"), col_letter("Type")
        last = col_letter(ReceiptRow.HEADERS[-1])
        src = f"{TAB}!A:{last}"
        by_currency = (
            f'=QUERY({src}{sep} "select {c}, sum({t}) where {c} is not null group by {c} '
            f"label {c} 'Currency', sum({t}) 'Total spent'\"{sep} 1)"
        )
        by_type = (
            f'=QUERY({src}{sep} "select {ty}, {c}, sum({t}) where {c} is not null '
            f"group by {ty}, {c} label {ty} 'Type', {c} 'Currency', sum({t}) 'Total'\"{sep} 1)"
        )
        ws.clear()
        ws.update(
            [["Total spent", "", "", "", "By type"], [by_currency, "", "", "", by_type]],
            "A1",
            value_input_option="USER_ENTERED",
        )
        ws.format("A1:E1", {"textFormat": {"bold": True, "fontSize": 12}})

    @property
    def location(self) -> str:
        return self.book.url

    def existing_keys(self) -> set[str]:
        col = ReceiptRow.HEADERS.index("Email ID") + 1
        return set(self.ws.col_values(col)[1:])

    def append(self, rows: list[ReceiptRow]) -> None:
        if not rows:
            return
        values = [[safe_cell(v) for v in row.values()] for row in rows]
        self.ws.append_rows(values, value_input_option="USER_ENTERED", table_range="A1")


def _sheet_id(value: str) -> str:
    """Accepts a sheet ID or a full Google Sheets link."""
    m = re.search(r"/spreadsheets/d/([A-Za-z0-9_-]+)", value)
    return m.group(1) if m else value.strip()


# Locales whose numbers use a decimal point; most others use a comma, and then
# Google Sheets wants ";" between formula arguments.
_DOT_DECIMAL = ("en", "ja", "zh", "ko", "th", "he", "iw", "hi", "ms", "fil", "tl", "ga", "cy", "sw")


def _comma_decimal(book) -> bool:
    try:
        locale = (book.locale or "en_US").lower()
    except Exception:
        return False
    return not locale.startswith(_DOT_DECIMAL)
