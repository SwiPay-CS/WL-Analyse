"""Fee parameter model: BrandParams table, NON_OFFERABLE set, lookup helper."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

# Brands SwiPay does not offer — caller mirrors Worldline cost, delta = 0.
NON_OFFERABLE: frozenset[str] = frozenset({"TWINT"})

# Category key used when the export value is unknown.
DEFAULT_CATEGORY = "Credit"

# Ordered category list — used for UI rendering and form parsing.
CATEGORIES: tuple[str, ...] = ("Debit", "Credit", "Commercial")


@dataclass(frozen=True)
class BrandParams:
    """SwiPay fee parameters per brand or card category."""

    asf_pct: float              # ASF as fraction of gross amount (e.g. 0.0035 = 0.35 %)
    asf_fix: float = 0.0        # Fixed ASF surcharge per transaction, CHF
    min_fee: float = 0.0        # Minimum fee (ASF + SF + IC combined), CHF
    dcc_cashback_pct: float = 0.0   # DCC cashback as fraction of gross amount
    ic_cap: float | None = None     # Optional interchange cap, CHF (None = no cap)


# Default SwiPay parameter table.
# Key = "Karten Kategorie" column value from the Worldline export.
# Commercial shares ASF with Credit; the differentiation vs. Debit/Credit
# comes entirely from ICF and CSF, which are passed through from the CSV.
PARAMS: dict[str, BrandParams] = {
    "Debit": BrandParams(
        asf_pct=0.0035,
        asf_fix=0.01,
        min_fee=0.15,
        dcc_cashback_pct=0.015,
    ),
    "Credit": BrandParams(
        asf_pct=0.0040,
        asf_fix=0.01,
        min_fee=0.20,
        dcc_cashback_pct=0.015,
    ),
    "Commercial": BrandParams(
        asf_pct=0.0040,
        asf_fix=0.01,
        min_fee=0.20,
        dcc_cashback_pct=0.015,
    ),
}


def get_params(category: str) -> BrandParams:
    """Return BrandParams for *category*, falling back to Credit if unknown."""
    return PARAMS.get(category, PARAMS[DEFAULT_CATEGORY])


def build_params_table(form_data: dict[str, Any]) -> dict[str, BrandParams]:
    """Build a PARAMS table from user-submitted form data (Flask request.form).

    Form field names:  asf_pct_{cat}   (%, e.g. "0.35")
                       asf_fix_{cat}   (CHF)
                       min_fee_{cat}   (CHF)
                       dcc_pct_{cat}   (%, e.g. "1.5")
    Where {cat} is "debit", "credit", or "commercial".
    Missing or invalid fields fall back to the module-level defaults in PARAMS.
    """

    def _f(key: str, fallback: float) -> float:
        try:
            return float(form_data.get(key, fallback))
        except (ValueError, TypeError):
            return fallback

    result: dict[str, BrandParams] = {}
    for cat in ("Debit", "Credit"):
        k = cat.lower()
        d = PARAMS[cat]
        result[cat] = BrandParams(
            asf_pct=_f(f"asf_pct_{k}", d.asf_pct * 100) / 100,
            asf_fix=_f(f"asf_fix_{k}", d.asf_fix),
            min_fee=_f(f"min_fee_{k}", d.min_fee),
            dcc_cashback_pct=_f(f"dcc_pct_{k}", d.dcc_cashback_pct * 100) / 100,
        )
    # Commercial shares ASF/fees with Credit; differentiation is via ICF/CSF from the CSV.
    result["Commercial"] = result["Credit"]
    return result


def params_to_display(p_table: dict[str, BrandParams]) -> list[dict]:
    """Return a list of dicts suitable for Jinja2 rendering."""
    rows = []
    for cat in CATEGORIES:
        p = p_table.get(cat, PARAMS[cat])
        rows.append({
            "category": cat,
            "asf_pct": round(p.asf_pct * 100, 4),
            "asf_fix": p.asf_fix,
            "min_fee": p.min_fee,
            "dcc_pct": round(p.dcc_cashback_pct * 100, 4),
        })
    return rows
