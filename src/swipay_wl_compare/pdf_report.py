"""PDF report generator using fpdf2 (no system dependencies required)."""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd
from fpdf import FPDF, XPos, YPos

logger = logging.getLogger(__name__)

# --- Layout constants (mm) ---
_MARGIN = 15
_ROW_H = 7
_HEADER_H = 8
_COL_WIDTHS_CAT = [38, 20, 28, 28, 28, 28, 30]   # 7 cols for category table
_COL_WIDTHS_BRAND = [38, 20, 28, 28, 28, 28, 30]  # same shape for brand table

# Colours (R, G, B)
_DARK_BLUE = (44, 62, 80)
_LIGHT_GRAY = (245, 247, 250)
_WHITE = (255, 255, 255)
_TOTAL_BG = (220, 230, 240)
_RED = (192, 57, 43)
_GREEN = (39, 174, 96)

_DISPLAY_COLS = [
    "Anzahl Tx",
    "WL Netto CHF",
    "SP Netto CHF",
    "Differenz Netto CHF",
    "Differenz %",
]


_CHAR_MAP = str.maketrans({
    "—": "--",   # em dash
    "–": "-",    # en dash
    "‘": "'", "’": "'",   # curly single quotes
    "“": '"', "”": '"',   # curly double quotes
})


def _s(text: str) -> str:
    """Ensure text is safe for fpdf2 core fonts (Latin-1 range)."""
    return text.translate(_CHAR_MAP)


def _fmt(v: object) -> str:
    if isinstance(v, float):
        return f"{v:,.2f}".replace(",", "'")
    if isinstance(v, int):
        return f"{v:,}".replace(",", "'")
    return str(v)


class _SwipayPDF(FPDF):
    def __init__(self, title: str, meta: str) -> None:
        super().__init__(orientation="L", unit="mm", format="A4")
        self._doc_title = title
        self._doc_meta = meta
        self.set_margins(_MARGIN, _MARGIN, _MARGIN)
        self.set_auto_page_break(auto=True, margin=_MARGIN)

    def header(self) -> None:
        self.set_font("Helvetica", "B", 11)
        self.set_text_color(*_DARK_BLUE)
        self.cell(0, 8, _s(self._doc_title), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_font("Helvetica", "", 8)
        self.set_text_color(120, 120, 120)
        self.cell(0, 5, _s(self._doc_meta), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.ln(2)
        self.set_draw_color(*_DARK_BLUE)
        self.set_line_width(0.4)
        self.line(_MARGIN, self.get_y(), self.w - _MARGIN, self.get_y())
        self.ln(3)
        self.set_text_color(0, 0, 0)

    def footer(self) -> None:
        self.set_y(-12)
        self.set_font("Helvetica", "I", 7)
        self.set_text_color(160, 160, 160)
        self.cell(0, 5,
                  _s("Parameter sind illustrativ -- NICHT die echte SwiPay-Preisliste. "
                     "Vertraulich, nur fuer internen Gebrauch."),
                  align="C")
        self.set_text_color(0, 0, 0)

    def section_title(self, text: str) -> None:
        self.set_font("Helvetica", "B", 10)
        self.set_text_color(*_DARK_BLUE)
        self.cell(0, 7, _s(text), new_x=XPos.LMARGIN, new_y=YPos.NEXT)
        self.set_text_color(0, 0, 0)
        self.ln(1)

    def _table_header(self, col_widths: list[int], headers: list[str]) -> None:
        self.set_fill_color(*_DARK_BLUE)
        self.set_text_color(*_WHITE)
        self.set_font("Helvetica", "B", 8)
        for w, h in zip(col_widths, headers):
            align = "L" if h == headers[0] else "R"
            self.cell(w, _HEADER_H, _s(h), border=0, align=align, fill=True)
        self.ln()
        self.set_text_color(0, 0, 0)

    def _table_row(
        self,
        col_widths: list[int],
        values: list[str],
        is_total: bool,
        is_even: bool,
        delta_idx: int,
    ) -> None:
        if is_total:
            self.set_fill_color(*_TOTAL_BG)
            self.set_font("Helvetica", "B", 8)
        elif is_even:
            self.set_fill_color(*_LIGHT_GRAY)
            self.set_font("Helvetica", "", 8)
        else:
            self.set_fill_color(*_WHITE)
            self.set_font("Helvetica", "", 8)

        for i, (w, v) in enumerate(zip(col_widths, values)):
            align = "L" if i == 0 else "R"
            # Colour the delta column.
            if i == delta_idx and not is_total:
                try:
                    num = float(v.replace("'", "").replace(",", "."))
                    self.set_text_color(*(_GREEN if num > 0 else _RED if num < 0 else (0, 0, 0)))
                except ValueError:
                    pass
            self.cell(w, _ROW_H, _s(v), border=0, align=align, fill=True)
            self.set_text_color(0, 0, 0)
        self.ln()

    def breakdown_table(
        self,
        df: pd.DataFrame,
        group_col: str,
        col_widths: list[int],
    ) -> None:
        cols = [group_col] + _DISPLAY_COLS
        sub = df[[c for c in cols if c in df.columns]].copy()
        headers = [c.replace(" CHF", "") for c in sub.columns]
        delta_idx = headers.index("Differenz Netto") if "Differenz Netto" in headers else -1

        self._table_header(col_widths, headers)

        for row_i, (_, row) in enumerate(sub.iterrows()):
            is_total = str(row.iloc[0]) == "TOTAL"
            vals = [_fmt(v) for v in row]
            self._table_row(col_widths, vals, is_total, row_i % 2 == 0, delta_idx)

        self.ln(3)


def to_pdf(
    summaries: dict[str, tuple[pd.DataFrame, str, list[int]]],
    path: Path,
    title: str,
    meta: str,
) -> None:
    """Write a PDF report.

    *summaries* maps section title → (DataFrame, group_col, col_widths).
    """
    pdf = _SwipayPDF(title=title, meta=meta)
    pdf.add_page()

    for section_title, (df, group_col, col_widths) in summaries.items():
        pdf.section_title(section_title)
        pdf.breakdown_table(df, group_col, col_widths)

    pdf.output(str(path))
    logger.info("PDF written: %s", path.name)
