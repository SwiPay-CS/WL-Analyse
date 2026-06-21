"""Report generation: per-category and per-brand breakdown as CSV and HTML."""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

# Column labels used in the output tables (German, for business users).
_COL_LABELS = {
    "tx_count":      "Anzahl Tx",
    "wl_fee":        "WL Gebühren CHF",
    "wl_cashback":   "WL Cashback CHF",
    "wl_net":        "WL Netto CHF",
    "sp_fee":        "SP Gebühren CHF",
    "sp_cashback":   "SP Cashback CHF",
    "sp_net":        "SP Netto CHF",
    "delta":         "Differenz Netto CHF",
    "delta_pct":     "Differenz %",
}

_HTML_STYLE = """
<style>
  body { font-family: -apple-system, Arial, sans-serif; margin: 2rem; color: #1a1a1a; }
  h1   { font-size: 1.4rem; margin-bottom: 0.25rem; }
  p.meta { font-size: 0.85rem; color: #666; margin-bottom: 1.5rem; }
  h2   { font-size: 1.1rem; margin-top: 2rem; margin-bottom: 0.5rem; }
  table { border-collapse: collapse; font-size: 0.88rem; width: 100%; }
  th   { background: #2c3e50; color: #fff; padding: 6px 12px; text-align: right; white-space: nowrap; }
  th:first-child { text-align: left; }
  td   { padding: 5px 12px; text-align: right; border-bottom: 1px solid #e8e8e8; }
  td:first-child { text-align: left; font-weight: 500; }
  tr:hover td { background: #f5f7fa; }
  .total td { font-weight: 700; background: #f0f4f8; border-top: 2px solid #2c3e50; }
  p.disclaimer {
    font-size: 0.78rem; color: #999; margin-top: 2rem;
    border-top: 1px solid #e8e8e8; padding-top: 0.75rem;
  }
</style>
"""


def build_breakdown(
    df: pd.DataFrame,
    eng: pd.DataFrame,
    group_by: str,
) -> pd.DataFrame:
    """Build a summary table grouped by *group_by* column (e.g. 'Brand' or 'Karten Kategorie').

    Returns a DataFrame with human-readable column names and a totals row.
    """
    combined = df.assign(
        sp_fee=eng["sp_fee"],
        sp_cashback=eng["sp_cashback"],
        sp_net=eng["sp_net"],
        wl_fee_raw=-df["Gebühren"],
        wl_cashback_raw=df["DCC Payback"].fillna(0.0),
    )

    grp = combined.groupby(group_by, dropna=True)
    summary = grp.agg(
        tx_count=(group_by, "count"),
        wl_fee=("wl_fee_raw", "sum"),
        wl_cashback=("wl_cashback_raw", "sum"),
        sp_fee=("sp_fee", "sum"),
        sp_cashback=("sp_cashback", "sum"),
    ).reset_index()

    summary["wl_net"] = summary["wl_fee"] - summary["wl_cashback"]
    summary["sp_net"] = summary["sp_fee"] - summary["sp_cashback"]
    summary["delta"] = summary["wl_net"] - summary["sp_net"]
    summary["delta_pct"] = (summary["delta"] / summary["wl_net"].abs().replace(0, float("nan")) * 100).round(2)

    # Totals row.
    totals = summary[list(_COL_LABELS.keys())].sum(numeric_only=True).to_frame().T
    totals[group_by] = "TOTAL"
    totals["delta_pct"] = (
        totals["delta"] / totals["wl_net"].abs().replace(0, float("nan")) * 100
    ).round(2)
    totals["tx_count"] = totals["tx_count"].astype(int)
    summary = pd.concat([summary, totals], ignore_index=True)

    summary = summary.rename(columns=_COL_LABELS)
    summary = summary.rename(columns={group_by: group_by})
    return summary


def to_csv(summary: pd.DataFrame, path: Path) -> None:
    summary.to_csv(path, index=False, sep=";", decimal=",", float_format="%.2f")
    logger.info("CSV written: %s", path.name)


def to_html(
    summaries: dict[str, pd.DataFrame],
    path: Path,
    filename: str,
    meta: str = "",
) -> None:
    """Write one HTML file containing one table per entry in *summaries*."""

    def _fmt_num(v):
        if isinstance(v, float):
            return f"{v:,.2f}".replace(",", "'")
        if isinstance(v, int):
            return f"{v:,}".replace(",", "'")
        return v

    sections = []
    for title, df in summaries.items():
        # Format numbers for display.
        display = df.copy()
        for col in display.columns[1:]:
            display[col] = display[col].apply(_fmt_num)

        html_table = display.to_html(index=False, border=0, classes="breakdown")
        # Highlight totals row.
        html_table = html_table.replace(
            "<tr>\n      <td>TOTAL</td>",
            '<tr class="total">\n      <td>TOTAL</td>',
        )
        sections.append(f"<h2>{title}</h2>\n{html_table}")

    body = "\n".join(sections)
    html = (
        f"<!doctype html><html lang='de'><head><meta charset='utf-8'>"
        f"<title>{filename}</title>{_HTML_STYLE}</head><body>"
        f"<h1>{filename}</h1>"
        f"<p class='meta'>{meta}</p>"
        f"{body}"
        f"<p class='disclaimer'>Parameter sind illustrativ — NICHT die echte SwiPay-Preisliste. "
        f"Vertraulich, nur für internen Gebrauch.</p>"
        f"</body></html>"
    )
    path.write_text(html, encoding="utf-8")
    logger.info("HTML written: %s", path.name)
