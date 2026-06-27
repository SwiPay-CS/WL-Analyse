"""Sanity-check: run the engine against the real Worldline export and verify
every acceptance anchor (Davos dataset).

Place the export at data/Analyse_Davos-Klosters.xlsb before running.
Prints a PASS/FAIL line per anchor. Lieber eine Luecke als eine Luege:
anchors that do not reproduce are reported as FAIL, never quietly adjusted.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

import pandas as pd

from loader import load_worldline, add_keys
from pipeline import run_comparison, totals
from settings import load_brand_master, default_rate_profile, build_param_table

XLSB = os.path.join(os.path.dirname(__file__), "data", "Analyse_Davos-Klosters.xlsb")


def chf(v: float) -> str:
    return f"{v:,.2f}".replace(",", "'")


def check(label: str, got: float, want: float, tol: float = 0.01) -> bool:
    ok = abs(got - want) < tol
    flag = "PASS" if ok else "FAIL"
    delta = "" if ok else f"   (Delta {got - want:+,.2f})".replace(",", "'")
    print(f"  [{flag}] {label:36} {chf(got):>16}  Soll {chf(want):>16}{delta}")
    return ok


# --- load -------------------------------------------------------------------
df = load_worldline(XLSB, sheet="WL")
df = add_keys(df)
print(f"Geladen: {len(df)} Zeilen\n")

# --- engine (brand-type model, placeholder rates) ---------------------------
master = load_brand_master()
profile = default_rate_profile()
params, offer = build_param_table(master, profile, mode="schnell")
res = run_comparison(df, params, offer, dcc_cashback_pct=profile.dcc_pct)
t = totals(res)

# --- derived metrics --------------------------------------------------------
brutto = pd.to_numeric(df["brutto"], errors="coerce")
is_ref = df["is_refund"].to_numpy(bool)
purch = ~is_ref
brutto_purch = float(brutto[purch].sum())

# Foreign-currency volume: all rows whose clearing region is not Domestic
# (Liechtenstein neglected). Refunds included -> matches the acceptance anchor.
region = df["region"].astype(str).str.strip().str.lower()
fx_vol = float(brutto[(region != "domestic") & region.ne("nan")].sum())

dcc_vol = float(brutto[df["is_dcc"].to_numpy(bool)].sum())

# Commercial volume: all rows whose card category is "Commercial" (refunds
# included), in line with the confirmed anchor 740'529.33.
cat = df["category"].astype(str).str.strip()
comm_vol = float(brutto[cat.eq("Commercial")].sum())

results = []
print("Worldline-Anker (gelockt):")
results.append(check("Zeilen", float(len(df)), 113497.0, tol=0.5))
results.append(check("WL-Brutto-Gebuehren", t["wl_fee"], 116513.10))
results.append(check("WL-DCC-Cashback", t["wl_cashback"], 12702.45))
results.append(check("WL-Netto-Gebuehren", t["wl_net"], 103810.65))

print("\nNeue Anker (Datenqualitaet / Info-KPI):")
results.append(check("Bruttoumsatz (Kaeufe)", brutto_purch, 14793293.29))
results.append(check("DCC-faeh. Fremdwaehrungsvol.", fx_vol, 4336735.23))
results.append(check("genutztes DCC-Volumen", dcc_vol, 905721.92))
results.append(check("Bruttoumsatz Commercial", comm_vol, 740529.33))

n_pass = sum(results)
print(f"\n{n_pass}/{len(results)} Anker bestanden.")
print(f"SwiPay-Netto (Platzhalter-Saetze): {chf(t['sp_net'])} CHF  "
      f"Ersparnis {chf(t['saving'])} CHF")

if n_pass < len(results):
    print("\nACHTUNG: nicht alle Anker reproduzieren. Vor Kundeneinsatz klaeren.")
    sys.exit(1)
print("\nOK: alle Anker bestanden.")
