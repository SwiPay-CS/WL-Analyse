"""Fee parameter model: BrandParams table, NON_OFFERABLE set, lookup helper."""
from __future__ import annotations

from dataclasses import dataclass

# Brands SwiPay does not offer — caller mirrors Worldline cost, delta = 0.
NON_OFFERABLE: frozenset[str] = frozenset({"TWINT"})

# Category key used when the export value is unknown.
DEFAULT_CATEGORY = "Credit"


@dataclass(frozen=True)
class BrandParams:
    """SwiPay fee parameters per brand or card category.

    NOTE: all example values in PARAMS below are ILLUSTRATIVE —
    they are NOT the real SwiPay price list.
    """

    asf_pct: float              # ASF as fraction of gross amount (e.g. 0.0035 = 0.35 %)
    asf_fix: float = 0.0        # Fixed ASF surcharge per transaction, CHF
    min_fee: float = 0.0        # Minimum fee (ASF + SF + IC combined), CHF
    dcc_cashback_pct: float = 0.0   # DCC cashback as fraction of gross amount
    ic_cap: float | None = None     # Optional interchange cap, CHF (None = no cap)


# ---------------------------------------------------------------------------
# ILLUSTRATIVE parameter table — replace with real SwiPay pricing before use.
# Key = "Karten Kategorie" column value from the Worldline export.
# ---------------------------------------------------------------------------
PARAMS: dict[str, BrandParams] = {
    "Debit": BrandParams(
        asf_pct=0.0011,
        asf_fix=0.00,
        min_fee=0.10,
        dcc_cashback_pct=0.014,
        ic_cap=None,
    ),
    "Credit": BrandParams(
        asf_pct=0.0016,
        asf_fix=0.00,
        min_fee=0.12,
        dcc_cashback_pct=0.014,
        ic_cap=None,
    ),
    "Commercial": BrandParams(
        asf_pct=0.0020,
        asf_fix=0.00,
        min_fee=0.12,
        dcc_cashback_pct=0.014,
        ic_cap=None,
    ),
}


def get_params(category: str) -> BrandParams:
    """Return BrandParams for *category*, falling back to Credit if unknown."""
    return PARAMS.get(category, PARAMS[DEFAULT_CATEGORY])
