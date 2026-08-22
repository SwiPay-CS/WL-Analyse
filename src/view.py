"""
Eine Quelle fuer alle Zahlen der Praesentation UND des Kunden-PDF.

Die Seite darf NIE Ist- und Jahreswerte mischen. Statt dass jede Kachel, jedes
Chart und jeder PDF-Block einzeln entscheidet, baut build_view() genau EIN
Zahlenpaket -- entweder komplett hochgerechnet oder komplett Ist -- und alles
danach liest nur noch daraus. Das PDF bekommt dasselbe Objekt wie der
Bildschirm, deshalb koennen die beiden nicht auseinanderlaufen.

Bewusst frei von Streamlit und pandas-Abhaengigkeiten, damit reporter.py es
importieren kann.
"""

from __future__ import annotations

from dataclasses import dataclass

BASIS_PA = "pa"
BASIS_IST = "ist"

# Effektive Gebuehrenraten werden mit DREI Dezimalen ausgewiesen. Bei zwei
# stehen 0.75 % und 0.67 % da, und 0.08 / 0.75 ergibt 10.7 % -- was der
# Gebuehrenveraenderung (-11.3 %) widerspricht. Mit drei Dezimalen geht die
# Rechnung fuer den Leser auf: 0.085 / 0.754 = 11.3 %.
RATE_DEC = 3


@dataclass(frozen=True)
class ViewNumbers:
    """Ein in sich konsistentes Zahlenpaket auf EINER Basis."""

    basis: str          # BASIS_PA | BASIS_IST
    suffix: str         # "p.a." | "im Zeitraum"
    note: str           # "hochgerechnet" | "Ist-Werte"

    brutto: float
    n_txn: float
    avg_ticket: float

    # Gebuehren vor DCC-Cashback (Acquiring) und netto (nach Cashback).
    wl_fee: float
    sp_fee: float
    wl_net: float
    sp_net: float
    wl_cb: float
    sp_cb: float

    # Zerlegung des Vorteils. Es gilt exakt total == acquiring + dcc, weil
    # wl_net = wl_fee - wl_cb gilt (siehe engine.py). Kein Residuum.
    acquiring: float
    dcc: float
    total: float

    # Volumenbasen: netto fuer die Ausschoepfungsquote, Kaeufe fuer jede
    # Cashback-Satz-Rechnung (siehe projection.VolumeBases).
    dcc_vol: float
    fx_vol: float
    dcc_vol_purch: float
    fx_vol_purch: float

    # ASF-Ebene fuer den Ø-Satz-Vergleich (SwiPays einziger variabler Hebel
    # gegen Worldlines Processing Fee). NICHT als Zerlegung der Ersparnis
    # lesen -- siehe asf_rate().
    sp_asf: float = 0.0
    wl_processing: float = 0.0

    # Nur auf der p.a.-Basis gesetzt.
    band_low: float | None = None
    band_high: float | None = None

    @property
    def is_projected(self) -> bool:
        return self.basis == BASIS_PA

    @property
    def wl_rate(self) -> float | None:
        """Effektive Gebuehrenrate Worldline als Anteil vom Bruttoumsatz.
        None ohne Umsatzbasis -- lieber eine Luecke als eine 0."""
        return (self.wl_net / self.brutto) if self.brutto else None

    @property
    def sp_rate(self) -> float | None:
        return (self.sp_net / self.brutto) if self.brutto else None

    @property
    def rate_delta(self) -> float | None:
        """Ratendifferenz in Prozentpunkten (als Anteil), positiv = guenstiger."""
        wl, sp = self.wl_rate, self.sp_rate
        return None if wl is None else wl - sp

    @property
    def rel_pct(self) -> float:
        """Relative ERSPARNIS in Prozent der Worldline-Gebuehren."""
        return (self.total / abs(self.wl_net) * 100) if self.wl_net else 0.0

    @property
    def fee_change_pct(self) -> float:
        """Gebuehrenveraenderung: negativ = Gebuehren sinken. Das Gegenteil von
        rel_pct, weil die Kachel die Richtung der GEBUEHREN zeigt."""
        return -self.rel_pct

    @property
    def asf_rate(self) -> float | None:
        """Ø ASF-Satz von SwiPay als Anteil vom Bruttoumsatz.

        Der Vergleich mit wl_processing_rate zeigt den einzigen variablen
        Hebel (ICF/CSF laufen als Pass-through identisch durch). Die Differenz
        ist ein SATZVERGLEICH und NICHT exakt die Acquiring-Ersparnis: auf
        Refunds modelliert die Engine eine volle Umkehr mit abs-Komponenten,
        Worldline bucht dort gemischte Vorzeichen (siehe pipeline.py). Auf dem
        Davos-Datensatz sind das CHF 175 auf CHF 8'561 Ersparnis.
        """
        return (self.sp_asf / self.brutto) if self.brutto else None

    @property
    def wl_processing_rate(self) -> float | None:
        """Ø Processing-Fee-Satz von Worldline -- das Gegenstueck zur ASF."""
        return (self.wl_processing / self.brutto) if self.brutto else None

    @property
    def dcc_share(self) -> float:
        """Ausschoepfung: Anteil des DCC-faehigen Volumens, das als DCC laeuft.
        Netto-Basis -- so sind die Davos-Anker gelockt."""
        return (self.dcc_vol / self.fx_vol) if self.fx_vol else 0.0

    def cashback_ceiling(self, dcc_pct: float) -> float:
        """SwiPay-Cashback bei 100 % Ausschoepfung. Auf dem KAUFvolumen, weil
        Cashback nur auf Kaeufe gezahlt wird."""
        return dcc_pct * self.fx_vol_purch


def build_view(agg, t: dict, d: dict, basis: str) -> ViewNumbers:
    """Zahlenpaket fuer eine Basis.

    basis == BASIS_PA  -> Jahreswerte aus der Hochrechnung (agg)
    basis == BASIS_IST -> Ist-Werte des gewaehlten Zeitraums (t/d)

    t ist pipeline.totals(), d ist app._derive().
    """
    if basis == BASIS_PA:
        return ViewNumbers(
            basis=BASIS_PA, suffix="p.a.", note="hochgerechnet",
            brutto=agg.brutto_annual,
            n_txn=agg.n_txn_annual,
            # Skalen-invariant: Tier B erhaelt den Mix, der Durchschnittsbon
            # aendert sich dadurch nicht.
            avg_ticket=d["avg_ticket"],
            wl_fee=agg.wl_fee_annual, sp_fee=agg.sp_fee_annual,
            wl_net=agg.wl_net_annual, sp_net=agg.sp_net_annual,
            wl_cb=agg.wl_cashback_annual, sp_cb=agg.sp_cashback_annual,
            acquiring=agg.acquiring_advantage_annual,
            dcc=agg.dcc_advantage_annual,
            total=agg.saving_annual,
            sp_asf=agg.sp_asf_annual, wl_processing=agg.wl_processing_annual,
            dcc_vol=agg.dcc_volume_annual, fx_vol=agg.fx_volume_annual,
            dcc_vol_purch=agg.dcc_purchase_volume_annual,
            fx_vol_purch=agg.fx_purchase_volume_annual,
            band_low=agg.band_low, band_high=agg.band_high,
        )
    return ViewNumbers(
        basis=BASIS_IST, suffix="im Zeitraum", note="Ist-Werte",
        brutto=d["brutto"],
        n_txn=float(d["n_txn"]),
        avg_ticket=d["avg_ticket"],
        wl_fee=t["wl_fee"], sp_fee=t["sp_fee"],
        wl_net=t["wl_net"], sp_net=t["sp_net"],
        wl_cb=t["wl_cashback"], sp_cb=t["sp_cashback"],
        acquiring=t["wl_fee"] - t["sp_fee"],
        dcc=t["sp_cashback"] - t["wl_cashback"],
        total=t["wl_net"] - t["sp_net"],
        sp_asf=t.get("sp_asf", 0.0),
        wl_processing=t.get("wl_processing", 0.0),
        dcc_vol=d["dcc_vol"], fx_vol=d["fx_vol"],
        dcc_vol_purch=d["dcc_vol_purch"], fx_vol_purch=d["fx_vol_purch"],
    )
