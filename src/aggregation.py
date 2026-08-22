"""
Aggregation layer for the Praesentation page: combine Ist-Werte und Tier-B-
Hochrechnungen ueber mehrere Merchants/Gruppen gleichzeitig (Scope "Alle",
eine Gruppe, oder mehrere gleichzeitig ausgewaehlte Partner-IDs).

Design (abgestimmt mit dem Nutzer, siehe CLAUDE.md):
  * Jede Entity (Merchant oder Gruppe) wird EINZELN projiziert, wenn ein
    eigener Jahresumsatz vorliegt; sonst zaehlt ihr unveraendertes Ist.
    Reihenfolge je Gruppen-Entity (siehe app.py): Mitglieder-Werte auf
    Partner-Ebene schlagen den Gruppen-Lump-Sum; ohne beides bleibt die
    Gruppe beim Ist.
  * Deckungsgrad (Badge/Planungsband, siehe projection.CoverageTier) bezieht
    sich NUR auf die Entities, die tatsaechlich hochgerechnet wurden -- er
    misst, wie belastbar DIESE Projektion ist.
  * Portfolio-Abdeckung ist eine zweite, unabhaengige Kennzahl: welcher Anteil
    des Ist-Bruttoumsatzes im Scope ueberhaupt hochgerechnet wurde. Die sinkt,
    wenn viele Entities im Scope keine Hochrechnung haben -- anders als der
    Deckungsgrad, der davon unberuehrt bleibt.
  * Fan-out (mehrere Entities mit identischem Jahresumsatz-Betrag): wird
    IMMER summiert, nur als Hinweis markiert (kein Blocker, keine Persistenz
    einer Aufloesungs-Entscheidung).
"""

from __future__ import annotations

from dataclasses import dataclass, field

import pandas as pd

from engine import ParamTable, Offer
from projection import CoverageLabel, CoverageTier, project_tier_b

_BAND_PCT = 0.15  # wie projection.py -- Planungsband nur auf den projizierten Anteil


@dataclass
class EntityInput:
    """One merchant or group in the aggregation scope."""
    label: str          # Anzeigename fuer Hinweise/Captions, z.B. "Name (PID)"
    key: str             # Partner-ID oder Gruppenname (Fan-out-Clustering)
    df: pd.DataFrame      # rohe Transaktionen, bereits Zeitraum-/Auswahl-gefiltert
    annual_volume: float   # 0 = keine Hochrechnung hinterlegt
    ist_wl_net: float
    ist_sp_net: float
    ist_dcc_adv: float
    ist_txn: float
    ist_brutto: float       # Ist-Bruttoumsatz (Kaeufe) -- Nenner der Portfolio-Abdeckung
    # Ist-Zerlegung. Default 0.0, damit bestehende Aufrufer (und Tests) ohne
    # diese Dimensionen weiterlaufen; wird gebraucht, sobald eine Entity beim
    # Ist bleibt und ihr Beitrag trotzdem in Acquiring/DCC aufgeteilt wird.
    ist_wl_fee: float = 0.0        # Gebuehren VOR DCC-Cashback
    ist_sp_fee: float = 0.0
    ist_wl_cashback: float = 0.0
    ist_sp_cashback: float = 0.0
    ist_dcc_vol: float = 0.0       # genutztes DCC-Volumen, netto
    ist_fx_vol: float = 0.0        # DCC-faehiges Fremdwaehrungsvolumen, netto
    ist_dcc_purchase_vol: float = 0.0  # dieselben Volumen, nur Kaeufe --
    ist_fx_purchase_vol: float = 0.0   # Basis fuer jede Cashback-Satz-Rechnung
    ist_sp_asf: float = 0.0            # ASF-Ebene: SwiPays variabler Hebel
    ist_wl_processing: float = 0.0     # Worldlines Gegenstueck dazu


@dataclass
class AggregateProjection:
    wl_net_annual: float
    sp_net_annual: float
    saving_annual: float
    dcc_advantage_annual: float
    n_txn_annual: float

    band_low: float
    band_high: float

    coverage: CoverageTier            # nur ueber die projizierten Entities
    annual_volume_total: float        # Summe der hinterlegten Jahresumsaetze (Anzeige/PDF)
    portfolio_coverage_pct: float     # Ist-Anteil hochgerechnet, 0..1

    # Zerlegung des Vorteils in seine zwei unabhaengigen Hebel. Es gilt exakt
    #   saving_annual = acquiring_advantage_annual + dcc_advantage_annual
    # weil jede Komponente linear ueber die Entities summiert (siehe Test).
    wl_fee_annual: float = 0.0                  # Gebuehren VOR DCC-Cashback
    sp_fee_annual: float = 0.0
    acquiring_advantage_annual: float = 0.0     # wl_fee - sp_fee (gesparte Gebuehren)
    sp_asf_annual: float = 0.0                  # nur fuer den Ø-Satz-Vergleich
    wl_processing_annual: float = 0.0
    wl_cashback_annual: float = 0.0
    sp_cashback_annual: float = 0.0

    # Effektive Umsatzbasis der obigen Betraege: hochgerechneter Jahresumsatz
    # fuer projizierte Entities PLUS Ist-Bruttoumsatz fuer die uebernommenen.
    # Das ist der einzig korrekte Nenner fuer eine effektive Gebuehrenrate,
    # weil der Zaehler dieselbe Mischung enthaelt.
    brutto_annual: float = 0.0
    # Netto-Volumen (Ausschoepfungsquote) und Kauf-Volumen (Satz-Rechnungen) --
    # siehe projection.VolumeBases.
    dcc_volume_annual: float = 0.0
    fx_volume_annual: float = 0.0
    dcc_purchase_volume_annual: float = 0.0
    fx_purchase_volume_annual: float = 0.0

    used: list[str] = field(default_factory=list)       # hochgerechnete Entities
    skipped: list[str] = field(default_factory=list)     # Ist uebernommen
    duplicate_groups: list[list[str]] = field(default_factory=list)  # Fan-out-Verdacht

    # Skalierungsfaktor je Entity, POSITIONSGLEICH zur uebergebenen entities-
    # Liste (1.0 = Ist uebernommen). Damit kann der Aufrufer beliebige eigene
    # Aufschluesselungen (z.B. nach Kartentyp) exakt hochrechnen, statt einen
    # gemischten Durchschnittsfaktor zu unterstellen -- jede Entity behaelt
    # ihren eigenen Faktor.
    scales: list[float] = field(default_factory=list)

    @property
    def has_projection(self) -> bool:
        return bool(self.used)


def _duplicate_clusters(entities: list[EntityInput]) -> list[list[str]]:
    """Entities, die sich denselben (gerundeten) Jahresumsatz teilen --
    Fan-out-Verdacht. Rein informativ; es wird trotzdem summiert."""
    by_value: dict[float, list[str]] = {}
    for e in entities:
        if e.annual_volume > 0:
            by_value.setdefault(round(e.annual_volume), []).append(e.label)
    return [labels for labels in by_value.values() if len(labels) > 1]


def aggregate(
    entities: list[EntityInput],
    params: ParamTable,
    offer: Offer,
    dcc_cashback_pct: float,
) -> AggregateProjection | None:
    """Combine every entity's contribution. None if the scope is empty."""
    if not entities:
        return None

    wl = sp = dcc_adv = txn = 0.0
    wl_fee = sp_fee = wl_cb = sp_cb = 0.0
    sp_asf = wl_pro = 0.0
    brutto_eff = dcc_vol = fx_vol = 0.0
    dcc_pur = fx_pur = 0.0
    proj_saving = 0.0
    obs_vol_sum = ann_vol_sum = 0.0
    covered_brutto = total_brutto = 0.0
    used: list[str] = []
    skipped: list[str] = []
    scales: list[float] = []

    for e in entities:
        total_brutto += e.ist_brutto
        proj = None
        if e.annual_volume > 0 and not e.df.empty:
            try:
                proj = project_tier_b(
                    e.df, params, offer, dcc_cashback_pct, annual_volume=e.annual_volume
                )
            except ValueError:
                proj = None

        if proj is not None:
            scales.append(
                proj.annual_volume / proj.observed_volume
                if proj.observed_volume else 1.0)
            wl += proj.wl_net_annual
            sp += proj.sp_net_annual
            dcc_adv += proj.dcc_advantage_annual
            txn += proj.n_txn_annual
            wl_fee += proj.wl_fee_annual
            sp_fee += proj.sp_fee_annual
            sp_asf += proj.sp_asf_annual
            wl_pro += proj.wl_processing_annual
            wl_cb += proj.wl_dcc_cashback_annual
            sp_cb += proj.sp_dcc_cashback_annual
            brutto_eff += proj.annual_volume
            dcc_vol += proj.dcc_volume_annual
            fx_vol += proj.fx_volume_annual
            dcc_pur += proj.dcc_purchase_volume_annual
            fx_pur += proj.fx_purchase_volume_annual
            proj_saving += proj.saving_annual
            obs_vol_sum += proj.observed_volume
            ann_vol_sum += proj.annual_volume
            covered_brutto += e.ist_brutto
            used.append(e.label)
        else:
            scales.append(1.0)
            # Keine Hochrechnung: das Ist geht unveraendert ein. Der Nenner
            # (brutto_eff) waechst mit dem Ist-Umsatz, damit Zaehler und Nenner
            # dieselbe Mischung tragen.
            wl += e.ist_wl_net
            sp += e.ist_sp_net
            dcc_adv += e.ist_dcc_adv
            txn += e.ist_txn
            wl_fee += e.ist_wl_fee
            sp_fee += e.ist_sp_fee
            sp_asf += e.ist_sp_asf
            wl_pro += e.ist_wl_processing
            wl_cb += e.ist_wl_cashback
            sp_cb += e.ist_sp_cashback
            brutto_eff += e.ist_brutto
            dcc_vol += e.ist_dcc_vol
            fx_vol += e.ist_fx_vol
            dcc_pur += e.ist_dcc_purchase_vol
            fx_pur += e.ist_fx_purchase_vol
            skipped.append(e.label)

    saving = wl - sp
    # Planungsband gilt nur fuer den projizierten Teil der Ersparnis; der
    # Ist-Anteil ist bekannt, nicht geschaetzt (siehe Docstring oben).
    ist_saving = saving - proj_saving
    band_low = proj_saving * (1 - _BAND_PCT) + ist_saving
    band_high = proj_saving * (1 + _BAND_PCT) + ist_saving

    coverage = (
        CoverageTier.classify(obs_vol_sum, ann_vol_sum)
        if used else CoverageTier(CoverageLabel.INDICATIVE, 0.0)
    )
    portfolio_pct = (covered_brutto / total_brutto) if total_brutto > 0 else 0.0

    return AggregateProjection(
        wl_net_annual=wl,
        sp_net_annual=sp,
        saving_annual=saving,
        dcc_advantage_annual=dcc_adv,
        n_txn_annual=txn,
        band_low=band_low,
        band_high=band_high,
        coverage=coverage,
        annual_volume_total=ann_vol_sum,
        portfolio_coverage_pct=portfolio_pct,
        wl_fee_annual=wl_fee,
        sp_fee_annual=sp_fee,
        acquiring_advantage_annual=wl_fee - sp_fee,
        sp_asf_annual=sp_asf,
        wl_processing_annual=wl_pro,
        wl_cashback_annual=wl_cb,
        sp_cashback_annual=sp_cb,
        brutto_annual=brutto_eff,
        dcc_volume_annual=dcc_vol,
        fx_volume_annual=fx_vol,
        dcc_purchase_volume_annual=dcc_pur,
        fx_purchase_volume_annual=fx_pur,
        used=used,
        skipped=skipped,
        duplicate_groups=_duplicate_clusters(entities),
        scales=scales,
    )
