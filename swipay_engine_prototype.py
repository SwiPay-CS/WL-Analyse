"""
SwiPay Worldline-Konditionenvergleich - Validierungs-Prototyp (Phase 0)

Zweck: Den fachlichen Engine-Kern gegen die echten Beispieldaten absichern,
bevor in Claude Code ein Repo entsteht. Beweist:
  1. Gebuehren = Processing Fee + Scheme Fee + Interchange (Identitaet).
  2. Mindestgebuehr-Floor wirkt transaktionsgenau (nicht aggregiert).
  3. DCC-Cashback wird NACH dem Floor als separate Gutschrift verrechnet.
  4. Refunds laufen vorzeichenrichtig durch.
  5. Idempotenz-Schluessel ist kollisionsfrei.
  6. Fan-out-Verdacht (mehrfacher identischer Jahresumsatz) wird erkannt.

Alle SwiPay-Parameter hier sind ILLUSTRATIV, nicht die echte Preisliste.
"""

from __future__ import annotations
from dataclasses import dataclass, field
import pandas as pd

XLSB = "Analyse_Davos-Klosters.xlsb"
CSV = "03.02.2024.csv"

# Brands SwiPay nicht anbietet -> Veraenderung = 0 (Kunde bleibt beim Anbieter).
NON_OFFERABLE = {"TWINT"}


@dataclass(frozen=True)
class BrandParams:
    """SwiPay-Konditionen je Brand/Kategorie. Illustrative Beispielwerte."""
    asf_pct: float          # ASF als Anteil vom Bruttobetrag (z.B. 0.0035 = 0.35%)
    asf_fix: float = 0.0    # nominaler ASF-Zuschlag je Transaktion, in CHF
    min_fee: float = 0.0    # Mindestgebuehr auf ASF+SF+IC, in CHF
    dcc_cashback_pct: float = 0.0  # Cashback-Anteil vom Umsatz bei DCC-Transaktionen


# Illustrative Parametrisierung: nur Debit/Kredit getrennt (Praxis-Default).
# Saetze grob am Worldline-Blended-ASF (~0.155%) orientiert, NICHT die echte
# SwiPay-Preisliste. Dienen nur dem Nachweis der Mechanik.
PARAMS: dict[str, BrandParams] = {
    "Debit":      BrandParams(asf_pct=0.0011, asf_fix=0.0, min_fee=0.10, dcc_cashback_pct=0.014),
    "Credit":     BrandParams(asf_pct=0.0016, asf_fix=0.0, min_fee=0.12, dcc_cashback_pct=0.014),
    "Commercial": BrandParams(asf_pct=0.0020, asf_fix=0.0, min_fee=0.12, dcc_cashback_pct=0.014),
}


@dataclass
class TxResult:
    """Ergebnis je Transaktion. Kosten als positive Betraege (Magnitude)."""
    asf_new: float
    fee_total: float        # ASF + SF + IC nach Floor
    cashback: float         # DCC-Gutschrift (>= 0)
    net_cost: float         # fee_total - cashback (kann negativ = Gutschrift sein)
    floored: bool
    offerable: bool


def compute_swipay(
    brutto: float,
    scheme_fee_cost: float,   # positive Magnitude (Pass-through, = Worldline)
    interchange_cost: float,  # positive Magnitude (Pass-through, = Worldline)
    category: str,
    is_dcc: bool,
    brand: str,
    is_refund: bool,
) -> TxResult:
    """Per-transaction SwiPay fee. Costs are positive magnitudes."""
    # Brand not in SwiPay offer -> no change. Caller mirrors Worldline cost.
    if brand in NON_OFFERABLE:
        return TxResult(0.0, 0.0, 0.0, 0.0, False, offerable=False)

    p = PARAMS.get(category, PARAMS["Credit"])  # fallback to credit pricing
    sf, ic = scheme_fee_cost, interchange_cost

    if is_refund:
        # Refund: pass fees through with reversed sign, no minimum-fee floor.
        asf_new = p.asf_pct * abs(brutto) + p.asf_fix
        fee_total = asf_new + sf + ic
        return TxResult(-asf_new, -fee_total, 0.0, -fee_total, False, True)

    asf_new = p.asf_pct * brutto + p.asf_fix
    fee_before = asf_new + sf + ic

    # Minimum-fee floor: applies to ASF+SF+IC, realised via the ASF only.
    floored = False
    if fee_before < p.min_fee:
        asf_new = max(p.min_fee - sf - ic, 0.0)
        fee_total = asf_new + sf + ic
        floored = True
    else:
        fee_total = fee_before

    # DCC cashback is a SEPARATE credit, applied AFTER the floor. Never mixed in.
    cashback = p.dcc_cashback_pct * brutto if is_dcc else 0.0
    net_cost = fee_total - cashback

    return TxResult(asf_new, fee_total, cashback, net_cost, floored, True)


def load_wl() -> pd.DataFrame:
    df = pd.read_excel(XLSB, sheet_name="WL", engine="pyxlsb", header=0)
    for c in ["Bruttobetrag", "Gebühren", "Processing Fee", "Scheme Fee",
              "Interchange", "DCC Payback"]:
        df[c] = pd.to_numeric(df[c], errors="coerce")
    return df


def check_identity(df: pd.DataFrame) -> None:
    """1. Gebuehren = PF + SF + IC."""
    m = df[["Processing Fee", "Scheme Fee", "Interchange"]].notna().all(axis=1)
    recomputed = df.loc[m, ["Processing Fee", "Scheme Fee", "Interchange"]].sum(axis=1)
    diff = (df.loc[m, "Gebühren"] - recomputed).round(2)
    print("1) IDENTITAET  Gebuehren = PF + SF + IC")
    print(f"   geprueft: {m.sum():>7} Zeilen | max Abweichung: {diff.abs().max():.2f} CHF "
          f"| Mismatches: {(diff != 0).sum()}")


def run_engine(df: pd.DataFrame) -> pd.DataFrame:
    """SwiPay-Engine transaktionsgenau, vektorisiert (113k Zeilen -> schnell).

    Gleiche Logik wie compute_swipay(), nur auf Spaltenebene. compute_swipay
    bleibt als lesbare Referenz fuer die Unit-Tests in Claude Code.
    """
    import numpy as np

    brutto = df["Bruttobetrag"].to_numpy(float)
    sf = df["Scheme Fee"].abs().fillna(0.0).to_numpy(float)
    ic = df["Interchange"].abs().fillna(0.0).to_numpy(float)
    pf = df["Processing Fee"].abs().fillna(0.0).to_numpy(float)
    cat = df["Karten Kategorie"].astype(str).to_numpy()
    brand = df["Brand"].astype(str).to_numpy()
    dcc = df["DCC"].astype(str).str.lower().eq("ja").to_numpy()
    ttype = df["Transaktionstyp"].astype(str).str.lower()
    wl_dcc_row = df["DCC Payback"].fillna(0.0).to_numpy(float)

    is_refund = (ttype.str.contains("gutschrift|refund|rueck|storno", regex=True)
                 .to_numpy() | (brutto < 0))
    offerable = ~np.isin(brand, list(NON_OFFERABLE))

    # Parameter je Kategorie in Arrays aufloesen (Fallback Credit).
    def pick(attr):
        default = getattr(PARAMS["Credit"], attr)
        m = {k: getattr(v, attr) for k, v in PARAMS.items()}
        return np.array([m.get(c, default) for c in cat], float)

    asf_pct, asf_fix = pick("asf_pct"), pick("asf_fix")
    min_fee, dcc_pct = pick("min_fee"), pick("dcc_cashback_pct")

    # Normalfall (offerierbar, kein Refund).
    asf_new = asf_pct * brutto + asf_fix
    fee_before = asf_new + sf + ic
    floored = (fee_before < min_fee) & ~is_refund & offerable
    fee_total = np.where(floored, np.maximum(min_fee - sf - ic, 0.0) + sf + ic, fee_before)

    # Refund: vorzeichenrichtig durchreichen, kein Floor.
    refund_fee = -(asf_pct * np.abs(brutto) + asf_fix + sf + ic)
    fee_total = np.where(is_refund & offerable, refund_fee, fee_total)

    # DCC-Cashback: separate Gutschrift NACH Floor, nur DCC + offerierbar + kein Refund.
    cashback = np.where(dcc & offerable & ~is_refund, dcc_pct * brutto, 0.0)

    # Nicht-offerierbare Brands: SwiPay spiegelt Worldline -> Delta 0.
    sp_fee = np.where(offerable, fee_total, pf + sf + ic)
    sp_cashback = np.where(offerable, cashback, wl_dcc_row)
    sp_net = sp_fee - sp_cashback

    return pd.DataFrame({
        "sp_fee": sp_fee, "sp_cashback": sp_cashback, "sp_net": sp_net,
        "floored": floored, "offerable": offerable,
    })


def main() -> None:
    df = load_wl()
    print(f"Datensatz: WL-Blatt, {len(df)} Transaktionen\n")

    check_identity(df)

    # Worldline-Ist (Magnituden)
    wl_fee = -df["Gebühren"].sum()
    wl_dcc = df["DCC Payback"].sum()
    wl_net = wl_fee - wl_dcc
    print("\n2) WORLDLINE IST")
    print(f"   Gebuehren: {wl_fee:>12,.2f} CHF | DCC-Cashback: {wl_dcc:>10,.2f} CHF "
          f"| Netto: {wl_net:>12,.2f} CHF")

    eng = run_engine(df)
    sp_fee = eng["sp_fee"].sum()
    sp_cashback = eng["sp_cashback"].sum()
    sp_net = eng["sp_net"].sum()
    print("\n3) SWIPAY (illustrative Parameter)")
    print(f"   Gebuehren: {sp_fee:>12,.2f} CHF | DCC-Cashback: {sp_cashback:>10,.2f} CHF "
          f"| Netto: {sp_net:>12,.2f} CHF")
    print(f"   Differenz Netto (Worldline - SwiPay): {wl_net - sp_net:>12,.2f} CHF")

    # 4) Floor-Wirkung: wie viele Transaktionen wuerden gefloored?
    n_floored = int(eng["floored"].sum())
    n_offer = int(eng["offerable"].sum())
    print("\n4) MINDESTGEBUEHR-FLOOR (transaktionsgenau)")
    print(f"   offerierbare Transaktionen: {n_offer} | davon gefloored: {n_floored} "
          f"({n_floored / max(n_offer,1) * 100:.1f}%)")
    small = df["Bruttobetrag"] < 32.47
    print(f"   Transaktionen < CHF 32.47: {int(small.sum())} "
          f"({small.mean() * 100:.1f}% des Volumens in Stueck)")

    # 5) DCC-Reihenfolge: nur DCC-Zeilen tragen Cashback, Floor davor.
    dcc_rows = df["DCC"].astype(str).str.lower().eq("ja")
    print("\n5) DCC-REIHENFOLGE  (Floor zuerst, Cashback danach separat)")
    print(f"   DCC-Transaktionen: {int(dcc_rows.sum())} | SwiPay-Cashback total: "
          f"{sp_cashback:,.2f} CHF | Worldline-Cashback: {wl_dcc:,.2f} CHF")

    # 6) Idempotenz-Schluessel kollisionsfrei
    key_cols = ["Datum", "Zeit", "Terminal ID", "Bruttobetrag",
                "Kartennummer", "Transaktionstyp"]
    dups = df.duplicated(subset=key_cols).sum()
    print("\n6) IDEMPOTENZ-SCHLUESSEL")
    print(f"   Datum+Zeit+Terminal+Brutto+Kartennummer+Typ: {dups} Duplikate "
          f"auf {len(df)} Zeilen")

    # 7) Fan-out-Verdacht im POS-Jahresblatt (Block 2)
    pos = pd.read_excel(XLSB, sheet_name="Auswertung POS 2025", engine="pyxlsb",
                        header=None)
    b2 = pos.iloc[2:, [10, 12, 13]].copy()
    b2.columns = ["KDNr", "Umsatz", "PID"]
    b2 = b2.dropna(subset=["KDNr"])
    b2["Umsatz"] = pd.to_numeric(b2["Umsatz"], errors="coerce")
    g = b2.groupby("PID").agg(n_kdnr=("KDNr", "nunique"),
                              n_distinct_umsatz=("Umsatz", lambda x: x.round(0).nunique()))
    fanout = g[(g["n_kdnr"] > 1) & (g["n_distinct_umsatz"] == 1)]
    print("\n7) FAN-OUT-VERDACHT (mehrere KD-Nr, identischer Jahresumsatz)")
    print(f"   verdaechtige Partner-IDs: {list(fanout.index.astype(int))}")
    print("   -> Default: nicht summieren, Flag zur manuellen Aufloesung")


if __name__ == "__main__":
    main()
