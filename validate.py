"""Validierung des modularen Kerns gegen die echten Beispieldaten."""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

import pandas as pd
from engine import BrandParams, ParamTable, Offer
from loader import load_worldline, add_keys
from pipeline import run_comparison, totals

XLSB = "/mnt/user-data/uploads/Analyse_Davos-Klosters.xlsb"

# Illustrative Parameter (NICHT die echte Preisliste).
PARAMS = ParamTable({
    "Debit":      BrandParams(0.0011, 0.0, 0.10, 0.014),
    "Credit":     BrandParams(0.0016, 0.0, 0.12, 0.014),
    "Commercial": BrandParams(0.0020, 0.0, 0.12, 0.014),
})
# Angebot: alles ausser TWINT (kein IC++-Produkt).
OFFER = Offer(frozenset({
    "VisaDebit", "Debit Mastercard", "Mastercard", "Visa", "Maestro",
    "Maestro-CH", "V PAY", "Diners/Discover", "Union Pay",
}))

df = load_worldline(XLSB, sheet="WL")
print(f"Geladen: {len(df)} Transaktionen, Spalten normalisiert")

df = add_keys(df)
dups = df["idempotency_key"].duplicated().sum()
print(f"Idempotenz-Schluessel: {dups} Duplikate auf {len(df)} Zeilen")

res = run_comparison(df, PARAMS, OFFER)
t = totals(res)
print(f"\nWorldline Netto: {t['wl_net']:>12,.2f} CHF")
print(f"SwiPay    Netto: {t['sp_net']:>12,.2f} CHF")
print(f"Ersparnis:       {t['saving']:>12,.2f} CHF")
print(f"Floor greift bei {t['n_floored']} Transaktionen")

# Erwartung aus dem Prototyp: Worldline-Netto 103'810.65
assert abs(t["wl_net"] - 103810.65) < 0.01, "Worldline-Netto weicht ab!"
print("\nOK: Worldline-Netto deckt sich mit dem KPIs-Blatt.")
