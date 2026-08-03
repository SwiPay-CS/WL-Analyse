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

    used: list[str] = field(default_factory=list)       # hochgerechnete Entities
    skipped: list[str] = field(default_factory=list)     # Ist uebernommen
    duplicate_groups: list[list[str]] = field(default_factory=list)  # Fan-out-Verdacht

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
    proj_saving = 0.0
    obs_vol_sum = ann_vol_sum = 0.0
    covered_brutto = total_brutto = 0.0
    used: list[str] = []
    skipped: list[str] = []

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
            wl += proj.wl_net_annual
            sp += proj.sp_net_annual
            dcc_adv += proj.dcc_advantage_annual
            txn += proj.n_txn_annual
            proj_saving += proj.saving_annual
            obs_vol_sum += proj.observed_volume
            ann_vol_sum += proj.annual_volume
            covered_brutto += e.ist_brutto
            used.append(e.label)
        else:
            wl += e.ist_wl_net
            sp += e.ist_sp_net
            dcc_adv += e.ist_dcc_adv
            txn += e.ist_txn
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
        used=used,
        skipped=skipped,
        duplicate_groups=_duplicate_clusters(entities),
    )
