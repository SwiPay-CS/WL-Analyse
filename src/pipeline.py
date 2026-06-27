"""
Vektorisierter Engine-Durchlauf ueber einen normalisierten DataFrame.

Spiegelt swipay_fee() aus engine.py auf Spaltenebene fuer Tempo (>100k Zeilen).
Ein Test prueft, dass vektorisiert == skalar.
"""

from __future__ import annotations
import numpy as np
import pandas as pd

from engine import ParamTable, Offer


def run_comparison(
    df: pd.DataFrame,
    params: ParamTable,
    offer: Offer,
    dcc_cashback_pct: float,
) -> pd.DataFrame:
    """Erwartet normalisierte Spalten (siehe loader.NORMALIZED_COLUMNS).

    dcc_cashback_pct ist ein globaler Satz (z.B. 0.014 = 1.4%), gilt fuer
    alle Brands gleichermassen.

    params wird PRO BRAND aufgeloest (Brand-Typ-Modell): die ParamTable ist auf
    die rohen Brand-Codes gekeyt, nicht auf die Kartenkategorie. Commercial vs.
    Consumer (Spalte category) hat KEINE Wirkung auf die ASF — ICF/CSF laufen
    fuer beide identisch durch.

    Liefert je Zeile wl_fee, wl_cashback, wl_net, sp_fee, sp_cashback, sp_net,
    floored, offerable.
    """
    brutto = df["brutto"].to_numpy(float)
    sf = df["scheme_fee"].abs().fillna(0.0).to_numpy(float)
    ic = df["interchange"].abs().fillna(0.0).to_numpy(float)
    pf = df["processing_fee"].abs().fillna(0.0).to_numpy(float)
    wl_dcc = df["dcc_payback"].fillna(0.0).to_numpy(float)
    brand = df["brand"].astype(str).to_numpy()
    is_dcc = df["is_dcc"].to_numpy(bool)
    is_refund = df["is_refund"].to_numpy(bool)

    offerable = np.array([offer.is_offerable(b) for b in brand], bool)

    # Resolve params per row into arrays. Keyed on the raw brand code
    # (brand-type model), NOT on the card category.
    def col(attr):
        return np.array([getattr(params.resolve(b), attr) for b in brand], float)

    asf_pct, asf_fix = col("asf_pct"), col("asf_fix")
    min_fee = col("min_fee")

    # Normal case.
    asf = asf_pct * brutto + asf_fix
    fee_before = asf + sf + ic
    floored = (fee_before < min_fee) & ~is_refund & offerable
    sp_fee = np.where(floored, np.maximum(min_fee - sf - ic, 0.0) + sf + ic, fee_before)

    # Refund: reversed sign, no floor.
    refund_fee = -(asf_pct * np.abs(brutto) + asf_fix + sf + ic)
    sp_fee = np.where(is_refund & offerable, refund_fee, sp_fee)

    # DCC cashback after floor, separate (global rate).
    sp_cashback = np.where(is_dcc & offerable & ~is_refund, dcc_cashback_pct * brutto, 0.0)

    # Worldline baseline straight from the raw signed total (source of truth).
    # gebuehren is negative for a cost, positive for a credit (refund).
    geb = df["gebuehren"].fillna(0.0).to_numpy(float)
    wl_fee = -geb               # positive = cost magnitude, negative = credit
    wl_cashback = wl_dcc

    # Non-offerable brand -> SwiPay mirrors Worldline (delta 0).
    sp_fee = np.where(offerable, sp_fee, wl_fee)
    sp_cashback = np.where(offerable, sp_cashback, wl_cashback)

    out = pd.DataFrame({
        "wl_fee": wl_fee, "wl_cashback": wl_cashback, "wl_net": wl_fee - wl_cashback,
        "sp_fee": sp_fee, "sp_cashback": sp_cashback, "sp_net": sp_fee - sp_cashback,
        "floored": floored, "offerable": offerable,
    })
    return out


def totals(result: pd.DataFrame) -> dict[str, float]:
    """Aggregierte Kennzahlen fuer die Anzeige."""
    wl_net = float(result["wl_net"].sum())
    sp_net = float(result["sp_net"].sum())
    return {
        "wl_fee": float(result["wl_fee"].sum()),
        "wl_cashback": float(result["wl_cashback"].sum()),
        "wl_net": wl_net,
        "sp_fee": float(result["sp_fee"].sum()),
        "sp_cashback": float(result["sp_cashback"].sum()),
        "sp_net": sp_net,
        "saving": wl_net - sp_net,
        "n_floored": int(result["floored"].sum()),
    }
