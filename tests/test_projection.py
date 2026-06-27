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
