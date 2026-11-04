"""Helpers shared by the Google Sheets and Excel writers."""

from __future__ import annotations

from ..models import ReceiptRow

SUMMARY_TAB = "Summary"


def col_letter(header: str) -> str:
    """Column letter of a header in the current layout, e.g. "Total" -> "E"."""
    n = ReceiptRow.HEADERS.index(header) + 1
    letters = ""
    while n:
        n, rem = divmod(n - 1, 26)
        letters = chr(65 + rem) + letters
    return letters


def safe_cell(value):
    """Stops text such as an email subject starting with "=" from becoming a formula."""
    if isinstance(value, str) and value[:1] in ("=", "+", "-", "@"):
        return "'" + value
    return "" if value is None else value
