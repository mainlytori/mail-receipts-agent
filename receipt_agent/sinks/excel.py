"""Writes receipts to an Excel (.xlsx) file on your computer."""

from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook, load_workbook
from openpyxl.styles import Font, PatternFill

from ..models import TYPES, ReceiptRow, migrate_row
from .layout import SUMMARY_TAB, col_letter

SHEET = "Receipts"
WIDTHS = {"Date": 12, "Store": 26, "Type": 14, "Category": 16, "Total": 12, "Currency": 9,
          "Payment method": 18, "Items": 40, "Order number": 18, "Email account": 26,
          "Email subject": 40, "Email ID": 20}


class ExcelSink:
    def __init__(self, path: str):
        self.path = Path(path)

    @property
    def location(self) -> str:
        return str(self.path.resolve())

    def _open(self):
        if self.path.exists():
            wb = load_workbook(self.path)
            ws = wb[SHEET] if SHEET in wb.sheetnames else wb.create_sheet(SHEET, 0)
        else:
            wb = Workbook()
            ws = wb.active
            ws.title = SHEET
        header = [c.value for c in ws[1]] if ws.max_row >= 1 else []
        while header and header[-1] is None:
            header.pop()
        if header != ReceiptRow.HEADERS:
            self._rebuild(ws, header)
        return wb, ws

    def _rebuild(self, ws, header: list) -> None:
        """Writes the header, moving rows from an older column layout if there are any."""
        rows = []
        if header:
            rows = [
                migrate_row(header, list(r))
                for r in ws.iter_rows(min_row=2, values_only=True)
                if any(v not in (None, "") for v in r)
            ]
            ws.delete_rows(1, ws.max_row)
            ws.delete_cols(1, ws.max_column)
        for i, h in enumerate(ReceiptRow.HEADERS, start=1):
            cell = ws.cell(1, i, h)
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="2F5597")
            ws.column_dimensions[cell.column_letter].width = WIDTHS.get(h, 14)
        ws.freeze_panes = "A2"
        for r in rows:
            self._add(ws, r)

    def _add(self, ws, values: list) -> None:
        ws.append(["" if v is None else v for v in values])
        for cell in ws[ws.max_row]:
            if isinstance(cell.value, str) and cell.value.startswith("="):
                cell.data_type = "s"  # an email subject, not a formula
        ws[f"{col_letter('Total')}{ws.max_row}"].number_format = "#,##0.00"

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
        wb, ws = self._open()
        for row in rows:
            self._add(ws, row.values())
        ws.auto_filter.ref = ws.dimensions
        self._write_summary(wb, ws)
        wb.save(self.path)

    def _write_summary(self, wb, ws) -> None:
        """A "Summary" sheet with totals per currency and per type (live formulas)."""
        if SUMMARY_TAB in wb.sheetnames:
            del wb[SUMMARY_TAB]
        sm = wb.create_sheet(SUMMARY_TAB)
        t, c, ty = col_letter("Total"), col_letter("Currency"), col_letter("Type")
        cur_idx = ReceiptRow.HEADERS.index("Currency")
        type_idx = ReceiptRow.HEADERS.index("Type")
        currencies, types = set(), set()
        for r in ws.iter_rows(min_row=2, values_only=True):
            if r[cur_idx]:
                currencies.add(str(r[cur_idx]))
                types.add(str(r[type_idx] or "Other"))
        bold = Font(bold=True, size=12)
        sm["A1"] = "Total spent"
        sm["A1"].font = bold
        sm.append(["Currency", "Total"])
        for cur in sorted(currencies):
            sm.append([cur, f'=SUMIF({SHEET}!{c}:{c},"{cur}",{SHEET}!{t}:{t})'])
        start = sm.max_row + 2
        sm.cell(start, 1, "By type").font = bold
        for i, label in enumerate(("Type", "Currency", "Total"), start=1):
            sm.cell(start + 1, i, label)
        row = start + 2
        for typ in [x for x in TYPES if x in types] + sorted(types - set(TYPES)):
            for cur in sorted(currencies):
                sm.cell(row, 1, typ)
                sm.cell(row, 2, cur)
                sm.cell(row, 3, f'=SUMIFS({SHEET}!{t}:{t},{SHEET}!{ty}:{ty},"{typ}",'
                                f'{SHEET}!{c}:{c},"{cur}")')
                row += 1
        for col, w in (("A", 16), ("B", 14), ("C", 14)):
            sm.column_dimensions[col].width = w
