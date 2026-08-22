"""Tests for the Phase-3 projection layer (projection.py)."""

import pandas as pd
import pytest

from engine import BrandParams, ParamTable, Offer
from pipeline import run_comparison, totals
from projection import (
    CoverageLabel,
    CoverageTier,
    project_tier_a,
    project_tier_b,
    volume_bases,
)

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

DEBIT = BrandParams(asf_pct=0.001, asf_fix=0.0, min_fee=0.10)
DCC_RATE = 0.014

# Brand-keyed ParamTable (brand-type model).
PARAMS = ParamTable({"VisaDebit": DEBIT, "Visa": DEBIT}, fallback_key="VisaDebit")
OFFER  = Offer(frozenset({"VisaDebit", "Visa"}))  # TWINT excluded


def _sample_df() -> pd.DataFrame:
    """Four rows: small purchase, DCC purchase, refund, non-offerable brand."""
    return pd.DataFrame({
        "brutto":         [5.0,   100.0,  -50.0,  30.0],
        "gebuehren":      [-0.09, -1.00,   0.12,  -0.30],
        "scheme_fee":     [-0.00, -0.30,  -0.05,  -0.10],
        "interchange":    [-0.00, -0.20,  -0.03,  -0.08],
        "processing_fee": [-0.09, -0.50,  -0.04,  -0.12],
        "dcc_payback":    [0.0,    1.40,   0.0,    0.0],
        "category":       ["Debit", "Credit", "Debit", "Debit"],
        "brand":          ["VisaDebit", "Visa", "VisaDebit", "TWINT"],
        "is_dcc":         [False, True,  False, False],
        "is_refund":      [False, False, True,  False],
    })


def _flat_df(brutto: list[float]) -> pd.DataFrame:
    """Uniform offerable Debit rows -- callers override is_dcc/region/refund."""
    n = len(brutto)
    return pd.DataFrame({
        "brutto":         brutto,
        "gebuehren":      [-0.09] * n,
        "scheme_fee":     [-0.02] * n,
        "interchange":    [-0.02] * n,
        "processing_fee": [-0.05] * n,
        "dcc_payback":    [0.0] * n,
        "category":       ["Debit"] * n,
        "brand":          ["VisaDebit"] * n,
        "is_dcc":         [False] * n,
        "is_refund":      [False] * n,
    })


# ---------------------------------------------------------------------------
# Coverage tier unit tests
# ---------------------------------------------------------------------------

def test_coverage_simulatable():
    t = CoverageTier.classify(70.0, 100.0)
    assert t.label == CoverageLabel.SIMULATABLE
    assert t.coverage_pct == pytest.approx(0.70)


def test_coverage_low():
    t = CoverageTier.classify(40.0, 100.0)
    assert t.label == CoverageLabel.LOW_COVERAGE


def test_coverage_indicative():
    t = CoverageTier.classify(20.0, 100.0)
    assert t.label == CoverageLabel.INDICATIVE


def test_coverage_boundary_25_is_low_not_indicative():
    t = CoverageTier.classify(25.0, 100.0)
    assert t.label == CoverageLabel.LOW_COVERAGE


def test_coverage_boundary_60_is_low_not_simulatable():
    t = CoverageTier.classify(60.0, 100.0)
    assert t.label == CoverageLabel.LOW_COVERAGE


# ---------------------------------------------------------------------------
# Test 1: Tier A is exact when annual volumes equal observed volumes
# ---------------------------------------------------------------------------

def test_tier_a_exact_when_scale_is_one():
    """project_tier_a with annual == observed for every brand must return the
    same saving as the raw pipeline totals (scale factor = 1 throughout)."""
    df = _sample_df()

    # Reference: observed totals straight from the pipeline.
    comp = run_comparison(df, PARAMS, OFFER, DCC_RATE)
    t = totals(comp)

    # Build annual_by_brand = exactly observed purchase volume per brand.
    brands_arr = df["brand"].to_numpy()
    refund_arr = df["is_refund"].to_numpy(bool)
    brutto_arr = df["brutto"].to_numpy(float)
    annual_by_brand = {
        brand: float(brutto_arr[(brands_arr == brand) & ~refund_arr].sum())
        for brand in df["brand"].unique()
    }

    result = project_tier_a(df, PARAMS, OFFER, DCC_RATE, annual_by_brand)

    assert result.saving_annual   == pytest.approx(t["saving"],        rel=1e-9)
    assert result.wl_net_annual   == pytest.approx(t["wl_net"],        rel=1e-9)
    assert result.sp_net_annual   == pytest.approx(t["sp_net"],        rel=1e-9)
    assert result.coverage.label  == CoverageLabel.SIMULATABLE
    assert result.tier            == "A"


def test_projection_ignores_empty_trailing_row():
    """A blank trailing row (all-NaN, as real WL exports carry) must not poison
    the projection with NaN — observed volume is summed nan-safe."""
    import numpy as np
    df = _sample_df()
    blank = {c: np.nan for c in df.columns}
    blank["is_refund"] = False
    blank["is_dcc"] = False
    df = pd.concat([df, pd.DataFrame([blank])], ignore_index=True)

    res = project_tier_b(df, PARAMS, OFFER, DCC_RATE, annual_volume=10_000.0)
    assert np.isfinite(res.saving_annual)
    assert np.isfinite(res.band_low) and np.isfinite(res.band_high)
    assert res.observed_volume == pytest.approx(135.0)  # blank row contributes 0


# ---------------------------------------------------------------------------
# Test 2: Tier B – Davos worst-case analogue (~22 % coverage) is "indikativ"
# ---------------------------------------------------------------------------

def test_tier_b_indicative_label_at_low_coverage():
    """When observed purchase volume is ~22 % of the stated annual volume the
    coverage label must be INDICATIVE and a planning band must be present."""
    df = _sample_df()

    # Purchase volume in sample = 5 + 100 + 30 = 135 CHF.
    # Set annual to ~4.5x that → coverage ≈ 22 % < 25 % → INDICATIVE.
    obs_vol     = 135.0
    annual_vol  = obs_vol / 0.22      # ≈ 613.6 CHF → coverage 22 %

    result = project_tier_b(df, PARAMS, OFFER, DCC_RATE, annual_volume=annual_vol)

    assert result.coverage.label    == CoverageLabel.INDICATIVE
    assert result.coverage.coverage_pct < 0.25
    assert result.band_low          <  result.saving_annual
    assert result.saving_annual     <  result.band_high
    assert result.tier              == "B"


def test_tier_b_scales_transaction_count_by_the_same_factor():
    """n_txn_annual must scale by the same factor as the CHF metrics (Tier B:
    observed mix preserved, one lump-sum scale for everything)."""
    df = _sample_df()  # 4 rows, purchase volume 135.0
    result = project_tier_b(df, PARAMS, OFFER, DCC_RATE, annual_volume=270.0)

    assert result.n_txn_observed == 4
    assert result.n_txn_annual   == pytest.approx(8.0)  # scale = 270/135 = 2


# ---------------------------------------------------------------------------
# Test 3: DCC advantage is positive when SwiPay rate exceeds Worldline rate
# ---------------------------------------------------------------------------

def test_dcc_advantage_positive_at_185_percent():
    """At SP DCC rate 1.85 %: SP cashback = 0.185 CHF vs WL cashback = 0.10 CHF
    on a 10 CHF DCC transaction → dcc_advantage = 0.085 CHF > 0."""
    df = pd.DataFrame({
        "brutto":         [10.0],
        "gebuehren":      [-0.50],
        "scheme_fee":     [-0.10],
        "interchange":    [-0.08],
        "processing_fee": [-0.32],
        "dcc_payback":    [0.10],     # Worldline pays 1.0 % = 0.10 CHF
        "category":       ["Credit"],
        "brand":          ["Visa"],
        "is_dcc":         [True],
        "is_refund":      [False],
    })
    params = ParamTable({"Visa": BrandParams(asf_pct=0.0016, min_fee=0.0)},
                        fallback_key="Visa")
    offer  = Offer(frozenset({"Visa"}))

    # annual_volume == observed purchase volume → scale = 1 → exact projection.
    result = project_tier_b(df, params, offer,
                            dcc_cashback_pct=0.0185,  # 1.85 %
                            annual_volume=10.0)

    assert result.sp_dcc_cashback_annual == pytest.approx(0.185)
    assert result.wl_dcc_cashback_annual == pytest.approx(0.10)
    assert result.dcc_advantage_annual   == pytest.approx(0.085)
    assert result.dcc_advantage_annual   > 0


# ── Fee/volume dimensions for the Acquiring vs DCC split ──────────────────────

def test_tier_b_exposes_fee_levels_andvolume_bases():
    """The advantage split needs the fee level BEFORE cashback plus the DCC and
    FX volume bases, all scaled by the same Tier-B factor."""
    df = _flat_df([100.0, 100.0, 200.0])          # obs purchase volume 400
    df["is_dcc"] = [True, False, True]
    df["region"] = ["Intra", "Domestic", "Intra"]
    df["dcc_payback"] = [0.80, 0.0, 1.60]

    r = project_tier_b(df, PARAMS, OFFER, DCC_RATE, annual_volume=4_000.0)
    scale = 4_000.0 / 400.0

    assert r.acquiring_advantage_annual == pytest.approx(
        r.wl_fee_annual - r.sp_fee_annual)
    # Identity: net saving decomposes exactly into the two levers.
    assert r.saving_annual == pytest.approx(
        r.acquiring_advantage_annual + r.dcc_advantage_annual)
    # Volume bases: net of refunds, scaled like every CHF metric.
    assert r.dcc_volume_annual == pytest.approx(300.0 * scale)
    assert r.fx_volume_annual == pytest.approx(300.0 * scale)


def testvolume_bases_are_net_of_refunds():
    """DCC/FX volume is a NET volume: a refund reduces it. This mirrors the
    locked Davos anchors in validate.py -- do not switch to purchases-only."""
    df = _flat_df([100.0, -40.0])
    df["is_dcc"] = [True, True]
    df["region"] = ["Intra", "Intra"]
    df["is_refund"] = [False, True]

    # Purchase volume (the Tier-B anchor) is 100; the refund only affects the
    # volume bases, so scale stays 1.0 here.
    r = project_tier_b(df, PARAMS, OFFER, DCC_RATE, annual_volume=100.0)
    assert r.dcc_volume_annual == pytest.approx(60.0)
    assert r.fx_volume_annual == pytest.approx(60.0)


# ── Volumen-Basen: netto vs. nur Kaeufe ───────────────────────────────────────

def test_volume_bases_reports_net_and_purchase_separately():
    """Netto traegt die Ausschoepfungsquote (Davos-Anker), Kaeufe tragen jede
    Satz-Rechnung. Beide muessen einzeln abrufbar sein."""
    df = _flat_df([100.0, -40.0, 60.0])
    df["is_dcc"] = [True, True, True]
    df["region"] = ["Intra", "Intra", "Intra"]
    df["is_refund"] = [False, True, False]

    vb = volume_bases(df)
    assert vb.dcc_net == pytest.approx(120.0)        # 100 - 40 + 60
    assert vb.fx_net == pytest.approx(120.0)
    assert vb.dcc_purchase == pytest.approx(160.0)   # 100 + 60, Refund raus
    assert vb.fx_purchase == pytest.approx(160.0)


def test_cashback_rate_resolves_exactly_on_the_purchase_base():
    """sp_cashback wird nur auf Kaeufe gezahlt. Geteilt durch das KAUF-Volumen
    muss deshalb exakt der eingestellte Satz herauskommen; auf der Netto-Basis
    tut es das nicht, weil Zaehler und Nenner verschieden maskiert sind."""
    df = _flat_df([1_000.0, -250.0, 500.0])
    df["is_dcc"] = [True, True, True]
    df["region"] = ["Intra", "Intra", "Intra"]
    df["is_refund"] = [False, True, False]

    t = totals(run_comparison(df, PARAMS, OFFER, DCC_RATE))
    vb = volume_bases(df)

    assert t["sp_cashback"] / vb.dcc_purchase == pytest.approx(DCC_RATE, abs=1e-12)
    # Gegenprobe: die Netto-Basis weicht ab -- genau der behobene Fehler.
    assert t["sp_cashback"] / vb.dcc_net != pytest.approx(DCC_RATE, abs=1e-6)


def test_both_volume_bases_scale_with_the_tier_b_factor():
    df = _flat_df([200.0, -50.0])
    df["is_dcc"] = [True, True]
    df["region"] = ["Intra", "Intra"]
    df["is_refund"] = [False, True]

    # Kaufvolumen 200 -> Faktor 5 auf einen Jahresumsatz von 1'000.
    r = project_tier_b(df, PARAMS, OFFER, DCC_RATE, annual_volume=1_000.0)
    assert r.dcc_volume_annual == pytest.approx(150.0 * 5)          # netto
    assert r.dcc_purchase_volume_annual == pytest.approx(200.0 * 5)  # Kaeufe
    assert r.fx_purchase_volume_annual == pytest.approx(200.0 * 5)
