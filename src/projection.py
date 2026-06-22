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
    obs_vol   = float(brutto[~is_refund].sum())

    if obs_vol <= 0:
        raise ValueError("No purchase transactions in dataset.")

    scale  = annual_volume / obs_vol
    wl_net = t["wl_net"]      * scale
    sp_net = t["sp_net"]      * scale
    saving = wl_net - sp_net
    wl_dcc = t["wl_cashback"] * scale
    sp_dcc = t["sp_cashback"] * scale

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

    wl_net_total = sp_net_total = 0.0
    wl_dcc_total = sp_dcc_total = 0.0
    total_obs_vol = total_ann_vol = 0.0
    mix_hints: list[str] = []

    seen_brands: list[str] = list(dict.fromkeys(df["brand"].tolist()))

    for brand in seen_brands:
        b      = brands == brand
        purch  = b & ~refund
        obs_vol = float(brutto[purch].sum()) if purch.any() else 0.0
        ann_vol = annual_by_brand.get(brand, obs_vol)

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
            continue

        scale = ann_vol / obs_vol
        wl_net_total += float(wl_net_v[b].sum()) * scale
        sp_net_total += float(sp_net_v[b].sum()) * scale
        wl_dcc_total += float(wl_cb_v[b].sum()) * scale
        sp_dcc_total += float(sp_cb_v[b].sum()) * scale

    # Mix plausibility: compare share of brands that have annual data.
    total_ann_prov = sum(annual_by_brand.values()) or 1.0
    total_obs_prov = sum(
        float(brutto[(brands == b) & ~refund].sum()) for b in annual_by_brand
    ) or 1.0
    for brand, ann_vol in annual_by_brand.items():
        ann_share = ann_vol / total_ann_prov
        obs_vol_b = float(brutto[(brands == brand) & ~refund].sum())
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
    )
