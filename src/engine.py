"""
SwiPay Worldline-Konditionenvergleich - Engine-Kern (framework-agnostisch).

Reine Rechenlogik, KEIN I/O, KEIN UI. Bewusst so gehalten, damit Streamlit
(oder irgendeine andere Huelle) nur noch aufruft und die Engine fuer sich
getestet werden kann.

Vorzeichen-Konvention: Alle Gebuehren sind POSITIVE Kosten-Magnituden.
Ein Refund liefert negative Werte (Gutschrift). Bruttobetrag ist vorzeichen-
behaftet (negativ bei Refund).
"""

from __future__ import annotations
from dataclasses import dataclass


# --------------------------------------------------------------------------
# Parametermodell
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class BrandParams:
    """SwiPay-Konditionen je Brand (Brand-Typ-Modell).

    asf_pct  ASF-Anteil vom Bruttobetrag (0.0011 = 0.11%)
    asf_fix  Trx-Fee: fixer Zuschlag je Transaktion, CHF (Default 0.01 = 1 Rp.)
    min_fee  Mindestgebuehr, greift gegen die GESAMTGEBUEHR
             ASF + Trx-Fee + ICF + CSF, CHF (realisiert ueber die ASF)
    ic_cap   optionaler Interchange-Cap je TRX, CHF (None = kein Cap)

    DCC-Cashback ist ein globaler Satz, nicht je Kategorie — wird als
    eigenes Argument an swipay_fee() / run_comparison() uebergeben.
    """
    asf_pct: float
    asf_fix: float = 0.0
    min_fee: float = 0.0
    ic_cap: float | None = None


class ParamTable:
    """Tabelle der SwiPay-Konditionen, gekeyt auf Kategorie (oder Brand).

    Echte Tabelle statt hartcodierter Konstanten: jeder Key kann eigene Werte
    tragen, mit Fallback auf einen Default-Key.
    """

    def __init__(self, rows: dict[str, BrandParams], fallback_key: str = "Credit"):
        self._rows = rows
        self._fallback_key = fallback_key

    def resolve(self, key: str) -> BrandParams:
        return self._rows.get(key, self._rows[self._fallback_key])


@dataclass(frozen=True)
class Offer:
    """Welche Brands SwiPay anbieten kann. Nicht enthaltene Brands -> Delta 0."""
    offerable_brands: frozenset[str]

    def is_offerable(self, brand: str) -> bool:
        return brand in self.offerable_brands


# --------------------------------------------------------------------------
# Ergebnis-Typen
# --------------------------------------------------------------------------

@dataclass(frozen=True)
class FeeResult:
    asf: float          # ASF-Anteil (Kosten-Magnitude, negativ bei Refund)
    fee_total: float    # ASF + ICF + CSF nach Floor
    cashback: float     # DCC-Gutschrift (>= 0)
    net_cost: float     # fee_total - cashback
    floored: bool
    offerable: bool


# --------------------------------------------------------------------------
# Kern: eine Transaktion
# --------------------------------------------------------------------------

def swipay_fee(
    brutto: float,
    scheme_fee: float,          # positive Magnitude, Pass-through (= Worldline)
    interchange: float,         # positive Magnitude, Pass-through (= Worldline)
    params: BrandParams,
    dcc_cashback_pct: float,    # global rate, same for all brands/categories
    is_dcc: bool,
    is_refund: bool,
    offerable: bool,
) -> FeeResult:
    """SwiPay-Gebuehr fuer eine einzelne Transaktion.

    Reihenfolge ist bewusst:
      1. ASF aus Prozent + Fix
      2. Mindestgebuehr-Floor auf ASF+ICF+CSF, realisiert ueber die ASF
      3. DCC-Cashback als SEPARATE Gutschrift NACH dem Floor
    Refunds laufen vorzeichenrichtig durch, ohne Floor.
    """
    # Brand nicht im Angebot -> keine Veraenderung. Huelle spiegelt Worldline.
    if not offerable:
        return FeeResult(0.0, 0.0, 0.0, 0.0, floored=False, offerable=False)

    sf, ic = scheme_fee, interchange

    if is_refund:
        asf = params.asf_pct * abs(brutto) + params.asf_fix
        fee_total = asf + sf + ic
        # Reverse sign: a refund gives money back.
        return FeeResult(-asf, -fee_total, 0.0, -fee_total, floored=False,
                         offerable=True)

    asf = params.asf_pct * brutto + params.asf_fix
    fee_before = asf + sf + ic

    floored = False
    if fee_before < params.min_fee:
        # Floor realised via ASF; ICF/CSF stay untouched. Never push ASF below 0.
        asf = max(params.min_fee - sf - ic, 0.0)
        fee_total = asf + sf + ic
        floored = True
    else:
        fee_total = fee_before

    cashback = dcc_cashback_pct * brutto if is_dcc else 0.0
    net_cost = fee_total - cashback
    return FeeResult(asf, fee_total, cashback, net_cost, floored, offerable=True)


def worldline_net(
    processing_fee: float,  # positive Magnitude
    scheme_fee: float,      # positive Magnitude
    interchange: float,     # positive Magnitude
    dcc_payback: float,     # positive Gutschrift aus den Daten
) -> tuple[float, float, float]:
    """Worldline-Vergleichsbasis aus den Ist-Daten. (fee_total, cashback, net)."""
    fee_total = processing_fee + scheme_fee + interchange
    return fee_total, dcc_payback, fee_total - dcc_payback
