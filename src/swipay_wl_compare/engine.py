"""SwiPay fee engine: scalar reference (compute_swipay) and vectorised runner (run_engine)."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from .params import DEFAULT_CATEGORY, NON_OFFERABLE, PARAMS, BrandParams, get_params


@dataclass(frozen=True)
class TxResult:
    """Result for a single transaction. Costs are positive magnitudes for purchases."""

    asf_new: float    # SwiPay ASF component
    fee_total: float  # ASF + SF + IC after floor
    cashback: float   # DCC credit (>= 0); applied AFTER the floor
    net_cost: float   # fee_total - cashback
    floored: bool     # True when the minimum-fee floor was applied
    offerable: bool   # False for non-offerable brands (e.g. TWINT)


def compute_swipay(
    brutto: float,
    scheme_fee: float,
    interchange: float,
    category: str,
    is_dcc: bool,
    brand: str,
    is_refund: bool,
) -> TxResult:
    """Compute SwiPay fee for one transaction (scalar, readable reference).

    scheme_fee and interchange are positive magnitudes (pass-through = Worldline).
    This function is the authoritative reference for unit tests; run_engine() is
    its vectorised equivalent for bulk processing.
    """
    if brand in NON_OFFERABLE:
        return TxResult(0.0, 0.0, 0.0, 0.0, floored=False, offerable=False)

    p = get_params(category)
    sf = scheme_fee
    ic = min(interchange, p.ic_cap) if p.ic_cap is not None else interchange

    if is_refund:
        # Sign-correct pass-through; minimum-fee floor does not apply to refunds.
        asf_new = p.asf_pct * abs(brutto) + p.asf_fix
        fee_total = asf_new + sf + ic
        return TxResult(-asf_new, -fee_total, 0.0, -fee_total, floored=False, offerable=True)

    asf_new = p.asf_pct * brutto + p.asf_fix
    fee_before = asf_new + sf + ic

    # Floor: target is (ASF + SF + IC) >= min_fee; only the ASF is raised.
    if fee_before < p.min_fee:
        asf_new = max(p.min_fee - sf - ic, 0.0)
        fee_total = asf_new + sf + ic
        floored = True
    else:
        fee_total = fee_before
        floored = False

    # DCC cashback is a separate credit applied AFTER the floor — never mixed in.
    cashback = p.dcc_cashback_pct * brutto if is_dcc else 0.0
    net_cost = fee_total - cashback

    return TxResult(asf_new, fee_total, cashback, net_cost, floored, offerable=True)


def run_engine(
    df: pd.DataFrame,
    custom_params: dict[str, BrandParams] | None = None,
) -> pd.DataFrame:
    """Vectorised SwiPay engine for a full Worldline export DataFrame.

    Expected input columns (exact Worldline export names):
        Bruttobetrag, Scheme Fee, Interchange, Processing Fee,
        Karten Kategorie, Brand, DCC, DCC Payback, Transaktionstyp

    Returns a DataFrame with columns:
        sp_fee, sp_cashback, sp_net, floored, offerable

    Pass *custom_params* (built via build_params_table()) to override the
    module-level defaults for a single analysis run.
    """
    p_table: dict[str, BrandParams] = custom_params if custom_params is not None else PARAMS

    brutto = df["Bruttobetrag"].to_numpy(float)
    sf = df["Scheme Fee"].abs().fillna(0.0).to_numpy(float)
    ic = df["Interchange"].abs().fillna(0.0).to_numpy(float)
    pf = df["Processing Fee"].abs().fillna(0.0).to_numpy(float)
    cat = df["Karten Kategorie"].astype(str).to_numpy()
    brand_col = df["Brand"].astype(str).to_numpy()
    dcc = df["DCC"].astype(str).str.lower().eq("ja").to_numpy()
    ttype = df["Transaktionstyp"].astype(str).str.lower()
    wl_dcc_cashback = df["DCC Payback"].fillna(0.0).to_numpy(float)

    is_refund = (
        ttype.str.contains("gutschrift|refund|rueck|storno", regex=True).to_numpy()
        | (brutto < 0)
    )
    offerable = ~np.isin(brand_col, list(NON_OFFERABLE))

    default_p = p_table.get(DEFAULT_CATEGORY, PARAMS[DEFAULT_CATEGORY])

    def _pick(attr: str) -> np.ndarray:
        default_val = getattr(default_p, attr)
        mapping = {k: getattr(v, attr) for k, v in p_table.items()}
        return np.array([mapping.get(c, default_val) for c in cat], dtype=float)

    asf_pct = _pick("asf_pct")
    asf_fix = _pick("asf_fix")
    min_fee = _pick("min_fee")
    dcc_pct = _pick("dcc_cashback_pct")

    # ic_cap is optional; build a per-row cap array (np.inf = no cap).
    ic_cap_vals = np.array(
        [
            (getattr(p_table.get(c, default_p), "ic_cap") or float("inf"))
            for c in cat
        ],
        dtype=float,
    )
    ic_capped = np.minimum(ic, ic_cap_vals)

    # --- Normal purchases ---
    asf_raw = asf_pct * brutto + asf_fix
    fee_before = asf_raw + sf + ic_capped
    floored = (fee_before < min_fee) & ~is_refund & offerable
    asf_floored = np.maximum(min_fee - sf - ic_capped, 0.0)
    fee_total = np.where(floored, asf_floored + sf + ic_capped, fee_before)

    # --- Refunds: sign-correct, no floor ---
    refund_fee = -(asf_pct * np.abs(brutto) + asf_fix + sf + ic_capped)
    fee_total = np.where(is_refund & offerable, refund_fee, fee_total)

    # --- DCC cashback: separate credit AFTER floor ---
    cashback = np.where(dcc & offerable & ~is_refund, dcc_pct * brutto, 0.0)

    # --- Non-offerable brands: mirror Worldline using total Gebühren, delta = 0 ---
    # Use -Gebühren instead of pf+sf+ic so brands that don't break fees into
    # components (e.g. TWINT) still produce sp_net == wl_net and delta == 0.
    wl_fee_total = (-df["Gebühren"]).fillna(0.0).to_numpy(float)
    sp_fee = np.where(offerable, fee_total, wl_fee_total)
    sp_cashback = np.where(offerable, cashback, wl_dcc_cashback)
    sp_net = sp_fee - sp_cashback

    return pd.DataFrame(
        {
            "sp_fee": sp_fee,
            "sp_cashback": sp_cashback,
            "sp_net": sp_net,
            "floored": floored,
            "offerable": offerable,
        }
    )
