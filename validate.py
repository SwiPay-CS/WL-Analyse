"""Sanity-check: run the engine against real Worldline export data.

Expected result: Worldline net == 103'810.65 CHF (113'497 rows).
Place the export at data/Analyse_Davos-Klosters.xlsb before running.
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

import pandas as pd
from engine import BrandParams, ParamTable, Offer
from loader import load_worldline, add_keys
from pipeline import run_comparison, totals

XLSB = os.path.join(os.path.dirname(__file__), "data", "Analyse_Davos-Klosters.xlsb")

# Illustrative parameters (NOT the real price list).
PARAMS = ParamTable({
    "Debit":      BrandParams(0.0011, 0.0, 0.10),
    "Credit":     BrandParams(0.0016, 0.0, 0.12),
    "Commercial": BrandParams(0.0020, 0.0, 0.12),
})
# Global DCC cashback rate applied to all offerable brands.
DCC_CASHBACK_PCT = 0.014

OFFER = Offer(frozenset({
    "VisaDebit", "Debit Mastercard", "Mastercard", "Visa", "Maestro",
    "Maestro-CH", "V PAY", "Diners/Discover", "Union Pay",
}))

df = load_worldline(XLSB, sheet="WL")
print(f"Loaded: {len(df)} transactions, columns normalised")

df = add_keys(df)
dups = df["idempotency_key"].duplicated().sum()
print(f"Idempotency keys: {dups} duplicates across {len(df)} rows")

res = run_comparison(df, PARAMS, OFFER, dcc_cashback_pct=DCC_CASHBACK_PCT)
t = totals(res)
print(f"\nWorldline Netto: {t['wl_net']:>14,.2f} CHF")
print(f"SwiPay    Netto: {t['sp_net']:>14,.2f} CHF")
print(f"Ersparnis:       {t['saving']:>14,.2f} CHF")
print(f"Floor greift bei {t['n_floored']} Transaktionen")

assert abs(t["wl_net"] - 103810.65) < 0.01, \
    f"Worldline net deviates: {t['wl_net']:.2f} != 103810.65"
print("\nOK: Worldline-Netto deckt sich mit dem KPIs-Blatt.")
