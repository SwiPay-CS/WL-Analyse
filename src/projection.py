"""
Phase-3 projection layer: coverage classification, Tier A/B scaling, DCC advantage.

Tier A — annual volume known per Brand:
    Each brand's observed metrics are scaled independently to its annual volume.
    Observed slice is used only for mix plausibility (brand-share deviation > 5 pp
    generates a hint; never a blocker, no percentage gate).

Tier B — only a lump-sum annual volume known:
    Observed mix is preserved; all metrics are scaled by one factor:
        scale = annual_volume / observed_purchase_volume
    NOT a calendar-day extrapolation.

Coverage labels (observed purchase vol / annual vol):
    > 60 %         simulierbar      (point estimate)
    25 – 60 %      niedrige Deckung (point estimate + label)
    < 25 %         indikativ        (range; conservative end = headline)

Planning band ±15 % is attached whenever a projection is made (scale != 1),
separately from the coverage label.

DCC advantage = SwiPay cashback – Worldline cashback (positive = SwiPay pays more).
Subject to the same coverage/tier logic because DCC share is seasonal.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import NamedTuple

import numpy as np

from engine import ParamTable, Offer
from pipeline import run_comparison, totals as _totals


_BAND_PCT = 0.15  # planning band half-width


# ---------------------------------------------------------------------------
# Coverage
# ---------------------------------------------------------------------------

class CoverageLabel(str, Enum):
    SIMULATABLE  = "simulierbar"       # > 60 %
    LOW_COVERAGE = "niedrige Deckung"  # 25 – 60 %
    INDICATIVE   = "indikativ"         # < 25 %


@dataclass(frozen=True)
class CoverageTier:
    label: CoverageLabel
    coverage_pct: float  # 0.0 – 1.0

    @classmethod
    def classify(cls, observed_vol: float, annual_vol: float) -> "CoverageTier":
        if annual_vol <= 0:
            return cls(CoverageLabel.INDICATIVE, 0.0)
        ratio = min(observed_vol / annual_vol, 1.0)
        if ratio > 0.60:
            lbl = CoverageLabel.SIMULATABLE
        elif ratio >= 0.25:
            lbl = CoverageLabel.LOW_COVERAGE
        else:
            lbl = CoverageLabel.INDICATIVE
        return cls(lbl, ratio)


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------

@dataclass
class ProjectionResult:
    tier: str                       # "A" or "B"
    coverage: CoverageTier
    observed_volume: float          # CHF, purchase transactions only
    annual_volume: float            # CHF, as provided

    # Projected annual metrics (CHF)
    wl_net_annual: float
    sp_net_annual: float
    saving_annual: float            # wl_net – sp_net  (positive = SwiPay cheaper)

    # DCC breakdown (annual-scaled)
    wl_dcc_cashback_annual: float
    sp_dcc_cashback_annual: float
    dcc_advantage_annual: float     # sp – wl  (positive = SwiPay pays more cashback)

    # Planning band ±15 %, present whenever scale_factor != 1
    band_low: float                 # conservative end
    band_high: float

    # Mix plausibility hints (Tier A only; empty for Tier B)
    mix_hints: list[str] = field(default_factory=list)

    # Transaction count (all rows, incl. refunds), scaled the same way as the
    # CHF metrics above.
    n_txn_observed: int = 0
    n_txn_annual: float = 0.0

    # Fee level before DCC cashback, annual-scaled. Needed to split the
    # advantage into its two independent levers:
    #   acquiring_advantage = wl_fee - sp_fee        (fees saved)
    #   dcc_advantage       = sp_cashback - wl_cashback  (extra cashback earned)
    #   saving              = acquiring + dcc        (exact identity, no residual)
    wl_fee_annual: float = 0.0
    sp_fee_annual: float = 0.0
    acquiring_advantage_annual: float = 0.0

    # ASF-Ebene: SwiPays einziger variabler Hebel gegen Worldlines Processing
    # Fee. Fuer den Ø-Satz-Vergleich; NICHT als Zerlegung der Ersparnis lesen
    # (das Refund-Modell der Engine weicht dort bewusst von Worldlines
    # gemischten Vorzeichen ab, siehe pipeline.py).
    sp_asf_annual: float = 0.0
    wl_processing_annual: float = 0.0

    # Volume bases for the DCC view, annual-scaled. See VolumeBases: the net
    # figures carry the utilisation share, the purchase figures carry any
    # cashback-rate arithmetic.
    dcc_volume_annual: float = 0.0            # netto (Refunds inklusive)
    fx_volume_annual: float = 0.0             # netto
    dcc_purchase_volume_annual: float = 0.0   # nur Kaeufe
    fx_purchase_volume_annual: float = 0.0    # nur Kaeufe


# ---------------------------------------------------------------------------
# Volume bases
# ---------------------------------------------------------------------------

class VolumeBases(NamedTuple):
    """DCC- und Fremdwaehrungsvolumen auf BEIDEN Basen, die die Seite braucht.

    dcc_net / fx_net
        Vorzeichenrichtig, Refunds INKLUSIVE -- also Netto-Volumen. Das ist die
        gelockte Definition hinter den Davos-Ankern (validate.py: fx
        4'336'735.23, dcc 905'721.92) und die Basis fuer die
        Ausschoepfungsquote (dcc_net / fx_net).

    dcc_purchase / fx_purchase
        Nur Kaeufe. Cashback wird ausschliesslich auf Kaeufe gezahlt (siehe
        pipeline.run_comparison: sp_cashback ist auf ~is_refund maskiert), also
        MUSS jede Satz-Rechnung (Cashback / Volumen) auf dieser Basis laufen.
        Auf der Netto-Basis ergaebe sp_cashback / dcc_net 1.8518 % statt der
        eingestellten 1.85 % -- Zaehler und Nenner sassen auf verschiedenen
        Basen.

    Beide skalieren unter Tier B mit demselben Faktor, behalten also ihre
    Semantik in der Hochrechnung.
    """
    dcc_net: float
    fx_net: float
    dcc_purchase: float
    fx_purchase: float


def volume_bases(df) -> VolumeBases:
    """DCC- und Fremdwaehrungsvolumen, netto und nur-Kaeufe (siehe VolumeBases)."""
    brutto = df["brutto"].to_numpy(float)
    purch  = ~df["is_refund"].to_numpy(bool)

    if "is_dcc" in df.columns:
        dcc = df["is_dcc"].to_numpy(bool)
        dcc_net = float(np.nansum(brutto[dcc]))
        dcc_pur = float(np.nansum(brutto[dcc & purch]))
    else:
        dcc_net = dcc_pur = 0.0

    if "region" in df.columns:
        r = df["region"].astype(str).str.strip().str.lower()
        fx = ((r != "domestic") & r.ne("nan")).to_numpy(bool)
        fx_net = float(np.nansum(brutto[fx]))
        fx_pur = float(np.nansum(brutto[fx & purch]))
    else:
        fx_net = fx_pur = 0.0

    return VolumeBases(dcc_net, fx_net, dcc_pur, fx_pur)


# ---------------------------------------------------------------------------
# Tier B
# ---------------------------------------------------------------------------

def project_tier_b(
    df,
    params: ParamTable,
    offer: Offer,
    dcc_cashback_pct: float,
    annual_volume: float,
) -> ProjectionResult:
    """Scale observed metrics to a known annual volume (lump sum).

    The observed brand/category mix is preserved. annual_volume is the anchor;
    calendar days are not used.
    """
    comp = run_comparison(df, params, offer, dcc_cashback_pct)
    t = _totals(comp)

    is_refund = df["is_refund"].to_numpy(bool)
    brutto    = df["brutto"].to_numpy(float)
    # nansum: real exports can carry an empty trailing row (brutto NaN) that
    # must not poison the observed volume.
    obs_vol   = float(np.nansum(brutto[~is_refund]))

    if obs_vol <= 0:
        raise ValueError("No purchase transactions in dataset.")

    scale  = annual_volume / obs_vol
    wl_net = t["wl_net"]      * scale
    sp_net = t["sp_net"]      * scale
    saving = wl_net - sp_net
    wl_dcc = t["wl_cashback"] * scale
    sp_dcc = t["sp_cashback"] * scale
    wl_fee = t["wl_fee"]      * scale
    sp_fee = t["sp_fee"]      * scale
    sp_asf = t["sp_asf"]        * scale
    wl_pro = t["wl_processing"] * scale
    vb = volume_bases(df)
    n_txn_obs = len(df)

    coverage = CoverageTier.classify(obs_vol, annual_volume)

    return ProjectionResult(
        tier="B",
        coverage=coverage,
        observed_volume=obs_vol,
        annual_volume=annual_volume,
        wl_net_annual=wl_net,
        sp_net_annual=sp_net,
        saving_annual=saving,
        wl_dcc_cashback_annual=wl_dcc,
        sp_dcc_cashback_annual=sp_dcc,
        dcc_advantage_annual=sp_dcc - wl_dcc,
        band_low=saving  * (1 - _BAND_PCT),
        band_high=saving * (1 + _BAND_PCT),
        n_txn_observed=n_txn_obs,
        n_txn_annual=n_txn_obs * scale,
        wl_fee_annual=wl_fee,
        sp_fee_annual=sp_fee,
        acquiring_advantage_annual=wl_fee - sp_fee,
        sp_asf_annual=sp_asf,
        wl_processing_annual=wl_pro,
        dcc_volume_annual=vb.dcc_net * scale,
        fx_volume_annual=vb.fx_net * scale,
        dcc_purchase_volume_annual=vb.dcc_purchase * scale,
        fx_purchase_volume_annual=vb.fx_purchase * scale,
    )


# ---------------------------------------------------------------------------
# Tier A
# ---------------------------------------------------------------------------

def project_tier_a(
    df,
    params: ParamTable,
    offer: Offer,
    dcc_cashback_pct: float,
    annual_by_brand: dict[str, float],
) -> ProjectionResult:
    """Scale each brand independently to its known annual volume.

    Brands absent from annual_by_brand are treated as 100 % covered (scale = 1).
    Mix plausibility: brand-share deviations > 5 pp are recorded as hints only.
    """
    comp = run_comparison(df, params, offer, dcc_cashback_pct)

    brands   = df["brand"].to_numpy()
    refund   = df["is_refund"].to_numpy(bool)
    brutto   = df["brutto"].to_numpy(float)
    wl_net_v = comp["wl_net"].to_numpy(float)
    sp_net_v = comp["sp_net"].to_numpy(float)
    wl_cb_v  = comp["wl_cashback"].to_numpy(float)
    sp_cb_v  = comp["sp_cashback"].to_numpy(float)
    wl_fee_v = comp["wl_fee"].to_numpy(float)
    sp_fee_v = comp["sp_fee"].to_numpy(float)
    sp_asf_v = comp["sp_asf"].to_numpy(float)
    wl_pro_v = comp["wl_processing"].to_numpy(float)

    wl_net_total = sp_net_total = 0.0
    wl_dcc_total = sp_dcc_total = 0.0
    wl_fee_total = sp_fee_total = 0.0
    sp_asf_total = wl_pro_total = 0.0
    dcc_vol_total = fx_vol_total = 0.0
    dcc_pur_total = fx_pur_total = 0.0
    total_obs_vol = total_ann_vol = 0.0
    n_txn_obs_total = 0
    n_txn_total = 0.0
    mix_hints: list[str] = []

    seen_brands: list[str] = list(dict.fromkeys(df["brand"].tolist()))

    for brand in seen_brands:
        b      = brands == brand
        purch  = b & ~refund
        obs_vol = float(np.nansum(brutto[purch])) if purch.any() else 0.0
        ann_vol = annual_by_brand.get(brand, obs_vol)
        n_b = int(b.sum())
        n_txn_obs_total += n_b

        if brand not in annual_by_brand:
            mix_hints.append(
                f"{brand}: no annual volume provided — using observed as-is"
            )

        total_obs_vol += obs_vol
        total_ann_vol += ann_vol

        if obs_vol <= 0:
            mix_hints.append(
                f"{brand}: zero observed purchase volume — cannot project"
            )
            n_txn_total += n_b  # scale undefined here; keep the raw count
            continue

        scale = ann_vol / obs_vol
        wl_net_total += float(wl_net_v[b].sum()) * scale
        sp_net_total += float(sp_net_v[b].sum()) * scale
        wl_dcc_total += float(wl_cb_v[b].sum()) * scale
        sp_dcc_total += float(sp_cb_v[b].sum()) * scale
        wl_fee_total += float(wl_fee_v[b].sum()) * scale
        sp_fee_total += float(sp_fee_v[b].sum()) * scale
        sp_asf_total += float(sp_asf_v[b].sum()) * scale
        wl_pro_total += float(wl_pro_v[b].sum()) * scale
        n_txn_total  += n_b * scale
        # Volume bases scale per brand, same factor as that brand's CHF metrics.
        vb_b = volume_bases(df[b])
        dcc_vol_total += vb_b.dcc_net * scale
        fx_vol_total  += vb_b.fx_net  * scale
        dcc_pur_total += vb_b.dcc_purchase * scale
        fx_pur_total  += vb_b.fx_purchase  * scale

    # Mix plausibility: compare share of brands that have annual data.
    total_ann_prov = sum(annual_by_brand.values()) or 1.0
    total_obs_prov = sum(
        float(np.nansum(brutto[(brands == b) & ~refund])) for b in annual_by_brand
    ) or 1.0
    for brand, ann_vol in annual_by_brand.items():
        ann_share = ann_vol / total_ann_prov
        obs_vol_b = float(np.nansum(brutto[(brands == brand) & ~refund]))
        obs_share = obs_vol_b / total_obs_prov
        if abs(ann_share - obs_share) > 0.05:
            mix_hints.append(
                f"{brand}: share {obs_share:.0%} observed vs {ann_share:.0%} annual"
            )

    saving  = wl_net_total - sp_net_total
    dcc_adv = sp_dcc_total - wl_dcc_total
    coverage = CoverageTier.classify(total_obs_vol, total_ann_vol)

    return ProjectionResult(
        tier="A",
        coverage=coverage,
        observed_volume=total_obs_vol,
        annual_volume=total_ann_vol,
        wl_net_annual=wl_net_total,
        sp_net_annual=sp_net_total,
        saving_annual=saving,
        wl_dcc_cashback_annual=wl_dcc_total,
        sp_dcc_cashback_annual=sp_dcc_total,
        dcc_advantage_annual=dcc_adv,
        band_low=saving  * (1 - _BAND_PCT),
        band_high=saving * (1 + _BAND_PCT),
        mix_hints=mix_hints,
        n_txn_observed=n_txn_obs_total,
        n_txn_annual=n_txn_total,
        wl_fee_annual=wl_fee_total,
        sp_fee_annual=sp_fee_total,
        acquiring_advantage_annual=wl_fee_total - sp_fee_total,
        sp_asf_annual=sp_asf_total,
        wl_processing_annual=wl_pro_total,
        dcc_volume_annual=dcc_vol_total,
        fx_volume_annual=fx_vol_total,
        dcc_purchase_volume_annual=dcc_pur_total,
        fx_purchase_volume_annual=fx_pur_total,
    )
