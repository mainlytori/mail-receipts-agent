"""Writes receipts to an Excel (.xlsx) file on your computer."""

from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill

from ..models import ReceiptRow

SHEET = "Receipts"


class ExcelSink:
    def __init__(self, path: str):
        self.path = Path(path)

    def _open(self):
        if self.path.exists():
            wb = load_workbook(self.path)
            ws = wb[SHEET] if SHEET in wb.sheetnames else wb.create_sheet(SHEET)
        else:
            wb = Workbook()
            ws = wb.active
            ws.title = SHEET
        if ws.max_row == 1 and ws.cell(1, 1).value is None:
            for i, header in enumerate(ReceiptRow.HEADERS, start=1):
                ws.cell(1, i, header)
            for cell in ws[1]:
                cell.font = Font(bold=True, color="FFFFFF")
                cell.fill = PatternFill("solid", fgColor="2F5597")
            ws.freeze_panes = "A2"
            widths = [12, 24, 10, 9, 9, 16, 18, 40, 18, 26, 40, 30, 20]
            for i, w in enumerate(widths, start=1):
                ws.column_dimensions[ws.cell(1, i).column_letter].width = w
        return wb, ws

    def existing_keys(self) -> set[str]:
        if not self.path.exists():
            return set()
        _, ws = self._open()
        col = ReceiptRow.HEADERS.index("Email ID") + 1
        return {
            str(r[0]) for r in ws.iter_rows(min_row=2, min_col=col, max_col=col, values_only=True)
            if r[0]
        }

    def append(self, rows: list[ReceiptRow]) -> None:
        if not rows:
            return
        wb, ws = self._open()
        for row in rows:
            ws.append(row.values())
            r = ws.max_row
            ws.cell(r, 3).number_format = "#,##0.00"
            ws.cell(r, 5).number_format = "#,##0.00"
            link = ws.cell(r, 12)
            if link.value:
                link.hyperlink = link.value
                link.value = "Open email"
                link.font = Font(color="0563C1", underline="single")
        ws.auto_filter.ref = ws.dimensions
        wb.save(self.path)
