"""Period detection and linear projection of fee comparison results."""
from __future__ import annotations

import logging
from dataclasses import dataclass

import pandas as pd

logger = logging.getLogger(__name__)

_EXCEL_EPOCH = pd.Timestamp("1899-12-30")


@dataclass(frozen=True)
class Period:
    start: pd.Timestamp
    end: pd.Timestamp
    actual_days: int


def detect_period(df: pd.DataFrame) -> Period:
    """Return the date range covered by the export.

    pyxlsb returns dates as Excel serial floats (days since 1899-12-30).
    CSV files may contain ISO date strings.
    """
    raw = df["Datum"].dropna()
    if pd.api.types.is_float_dtype(raw):
        dates = raw.apply(lambda x: _EXCEL_EPOCH + pd.Timedelta(days=int(x)))
    else:
        dates = pd.to_datetime(raw, errors="coerce").dropna()

    if dates.empty:
        raise ValueError("No parseable dates found in 'Datum' column.")

    start = dates.min()
    end = dates.max()
    actual_days = (end - start).days + 1
    logger.info(
        "Period: %s to %s (%d days)", start.date(), end.date(), actual_days
    )
    return Period(start=start, end=end, actual_days=actual_days)


def project_summary(
    wl_fee: float,
    wl_cashback: float,
    sp_fee: float,
    sp_cashback: float,
    tx_count: int,
    actual_days: int,
    target_days: int,
) -> dict[str, float | int]:
    """Scale all figures linearly to *target_days*."""
    f = target_days / max(actual_days, 1)
    wl_net = wl_fee - wl_cashback
    sp_net = sp_fee - sp_cashback
    return {
        "actual_days": actual_days,
        "target_days": target_days,
        "scale_factor": round(f, 4),
        "tx_count_actual": tx_count,
        "tx_count_proj": round(tx_count * f),
        "wl_fee_actual": wl_fee,
        "wl_fee_proj": wl_fee * f,
        "wl_cashback_actual": wl_cashback,
        "wl_cashback_proj": wl_cashback * f,
        "wl_net_actual": wl_net,
        "wl_net_proj": wl_net * f,
        "sp_fee_actual": sp_fee,
        "sp_fee_proj": sp_fee * f,
        "sp_cashback_actual": sp_cashback,
        "sp_cashback_proj": sp_cashback * f,
        "sp_net_actual": sp_net,
        "sp_net_proj": sp_net * f,
        "delta_actual": wl_net - sp_net,
        "delta_proj": (wl_net - sp_net) * f,
        "delta_pct": round((wl_net - sp_net) / max(abs(wl_net), 1e-9) * 100, 2),
    }


def project_breakdown(
    summary_df: pd.DataFrame,
    group_col: str,
    actual_days: int,
    target_days: int,
) -> pd.DataFrame:
    """Add projected columns to a breakdown DataFrame (in-place copy).

    Expects the DataFrame produced by report.build_breakdown().
    """
    f = target_days / max(actual_days, 1)
    proj = summary_df.copy()

    money_cols = {
        "WL Netto CHF": "WL Netto proj. CHF",
        "SP Netto CHF": "SP Netto proj. CHF",
        "Differenz Netto CHF": "Differenz proj. CHF",
    }
    for src, dst in money_cols.items():
        if src in proj.columns:
            proj[dst] = proj[src] * f

    if "Differenz proj. CHF" in proj.columns and "WL Netto proj. CHF" in proj.columns:
        proj["Differenz proj. %"] = (
            proj["Differenz proj. CHF"]
            / proj["WL Netto proj. CHF"].abs().replace(0, float("nan"))
            * 100
        ).round(2)

    return proj
