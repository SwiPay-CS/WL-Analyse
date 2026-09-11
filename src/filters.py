"""Filter layer for the Transaktionen page. Streamlit-free, therefore testable.

Selection semantics follow the Etrax dashboard this design was taken from:
an EMPTY selection means "Alle" and filters nothing. The page used to
preselect every option instead, which filled the widgets with noise and made
an accidental clear empty the page.

Field mapping Etrax -> Worldline (the two exports run largely parallel):
    VP-Name             -> partner_name
    Terminal-ID         -> terminal_id
    Vertriebsweg        -> vertrag
    Herkunftsland       -> region  (Clearing Region)
    Brand               -> brand
    Kartentypen         -> category
Etrax's IBAN column has no Worldline counterpart and is left out rather than
rendered as an empty field.
"""

from __future__ import annotations

import pandas as pd

# (Schlüssel, Spalte, Label) — die Reihenfolge ist die Anzeigereihenfolge.
TRX_FIELDS: tuple[tuple[str, str, str], ...] = (
    ("partner",   "partner_name", "Partner"),
    ("terminal",  "terminal_id",  "Terminal-ID"),
    ("vertrieb",  "vertrag",      "Vertriebsweg"),
    ("region",    "region",       "Clearing Region"),
    ("brand",     "brand",        "Brand"),
    ("kartentyp", "category",     "Kartentyp"),
)

_NAN_LIKE = ("nan", "none", "nat")


def _clean(s: pd.Series) -> pd.Series:
    """Trimmed string view of a column, without the NaN-like leftovers the
    export produces in trailing rows."""
    out = s.dropna().astype(str).str.strip()
    return out[out.ne("") & ~out.str.lower().isin(_NAN_LIKE)]


def options_for(df: pd.DataFrame | None, column: str) -> list[str]:
    """Sorted distinct values of one column — the choices for its dropdown."""
    if df is None or df.empty or column not in df.columns:
        return []
    return sorted(_clean(df[column]).unique().tolist())


def apply_filters(
    df: pd.DataFrame,
    selections: dict[str, list[str]] | None = None,
    frm: str | None = None,
    to: str | None = None,
) -> pd.Series:
    """Boolean mask over df for the Transaktionen page.

    Several values inside one field are OR, several fields are AND. An empty
    or missing selection filters nothing. A field whose column the export does
    not carry is skipped rather than raising -- not every Worldline export has
    every column.
    """
    mask = pd.Series(True, index=df.index)
    if df.empty:
        return mask

    if frm and to and "_month" in df.columns:
        mask &= df["_month"].between(frm, to, inclusive="both")

    for key, column, _label in TRX_FIELDS:
        chosen = (selections or {}).get(key) or []
        if not chosen or column not in df.columns:
            continue
        wanted = {str(c).strip() for c in chosen}
        mask &= df[column].astype(str).str.strip().isin(wanted)
    return mask


def years_from_months(months) -> list[str]:
    """Years present in a list of 'YYYY-MM' months, newest first."""
    return sorted({str(m)[:4] for m in months if m}, reverse=True)


def year_range(year: str, months) -> tuple[str, str] | None:
    """The selected year clamped to the months the data actually has.

    A rolling twelve-month export starting in August has no January; setting
    2025-01..2025-12 would show an empty page without saying why. None when
    the year is absent altogether.
    """
    inside = sorted(m for m in months if str(m).startswith(f"{year}-"))
    return (inside[0], inside[-1]) if inside else None
