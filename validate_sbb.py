"""Sanity-check: run the SBB IC++ loader against a real SBB export and verify
the acceptance anchors below (test export, Januar 2026, TUG/SBB).

Place the export at data/SBB_Export_ICpp.xlsx before running, or point SBB_XLSX
at another path. Lieber eine Luecke als eine Luege: anchors that do not
reproduce are reported as FAIL, never quietly adjusted.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "src"))

from loader import load_sbb, add_keys

SBB_XLSX = os.environ.get(
    "SBB_XLSX",
    os.path.join(os.path.dirname(__file__), "data", "SBB_Export_ICpp.xlsx"),
)


def chf(v: float) -> str:
    return f"{v:,.2f}".replace(",", "'")


def check(label: str, got: float, want: float, tol: float = 0.01) -> bool:
    ok = abs(got - want) < tol
    flag = "PASS" if ok else "FAIL"
    delta = "" if ok else f"   (Delta {got - want:+,.2f})".replace(",", "'")
    print(f"  [{flag}] {label:36} {chf(got):>16}  Soll {chf(want):>16}{delta}")
    return ok


if not os.path.exists(SBB_XLSX):
    print(f"SBB-Export nicht gefunden: {SBB_XLSX}\n"
          "Datei dorthin legen oder SBB_XLSX=<Pfad> setzen.")
    sys.exit(1)

df = load_sbb(SBB_XLSX)
df = add_keys(df)
print(f"Geladen: {len(df)} Zeilen\n")

results = []
print("SBB-Anker (Testexport Januar 2026, TUG):")
results.append(check("Zeilen", float(len(df)), 23213.0, tol=0.5))
results.append(check("Brutto (Betrag der Verbrauchsposition/ICF++)",
                      float(df["brutto"].sum()), 1340789.29))
results.append(check("Gebuehren (Kommission)", float(df["gebuehren"].sum()), -8551.18))
results.append(check("DCC-Ertrag", float(df["dcc_payback"].sum()), 486.68))
results.append(check("Refund-Zeilen", float(df["is_refund"].sum()), 7.0, tol=0.5))
results.append(check("DCC-genutzt-Zeilen", float(df["is_dcc"].sum()), 178.0, tol=0.5))
results.append(check("Eindeutige Idempotenz-Keys",
                      float(df["idempotency_key"].nunique()), float(len(df)), tol=0.5))

n_pass = sum(results)
print(f"\n{n_pass}/{len(results)} Anker bestanden.")

if n_pass < len(results):
    print("\nACHTUNG: nicht alle Anker reproduzieren. Vor Kundeneinsatz klaeren.")
    sys.exit(1)
print("\nOK: alle Anker bestanden.")
