"""Tests for aggregation.py: combining Ist + Tier-B-Hochrechnung across
several merchants/groups for the Praesentation page's scopes (Alle, mehrere
Partner, eine Gruppe)."""

import pandas as pd
import pytest

from engine import BrandParams, ParamTable, Offer
from projection import CoverageLabel, project_tier_b
from aggregation import EntityInput, aggregate

DEBIT = BrandParams(asf_pct=0.001, asf_fix=0.0, min_fee=0.10)
DCC_RATE = 0.014
PARAMS = ParamTable({"VisaDebit": DEBIT, "Visa": DEBIT}, fallback_key="VisaDebit")
OFFER = Offer(frozenset({"VisaDebit", "Visa"}))


def _df(brutto: list[float]) -> pd.DataFrame:
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


def _entity(label: str, brutto: list[float], annual_volume: float = 0.0,
           ist_scale: float = 1.0) -> EntityInput:
    """Ist values default to the raw sum of `brutto` (as if wl_net==sp_net==
    brutto for simplicity; individual test cases override where it matters)."""
    df = _df(brutto)
    total = float(sum(brutto))
    return EntityInput(
        label=label, key=label, df=df, annual_volume=annual_volume,
        ist_wl_net=total * ist_scale, ist_sp_net=total * ist_scale * 0.9,
        ist_dcc_adv=0.0, ist_txn=float(len(brutto)), ist_brutto=total,
    )


def test_empty_scope_returns_none():
    assert aggregate([], PARAMS, OFFER, DCC_RATE) is None


def test_single_entity_with_projection_matches_project_tier_b_directly():
    df = _df([100.0, 50.0, 30.0])
    e = EntityInput(
        label="A", key="A", df=df, annual_volume=1_800.0,
        ist_wl_net=1.0, ist_sp_net=1.0, ist_dcc_adv=0.0, ist_txn=3.0,
        ist_brutto=180.0,
    )
    direct = project_tier_b(df, PARAMS, OFFER, DCC_RATE, annual_volume=1_800.0)
    result = aggregate([e], PARAMS, OFFER, DCC_RATE)

    assert result.saving_annual == pytest.approx(direct.saving_annual)
    assert result.band_low == pytest.approx(direct.band_low)
    assert result.band_high == pytest.approx(direct.band_high)
    assert result.coverage.label == direct.coverage.label
    assert result.used == ["A"]
    assert result.skipped == []
    assert result.has_projection is True
    # Single, fully-projected entity -> 100 % of its Ist-Umsatz was hochgerechnet.
    assert result.portfolio_coverage_pct == pytest.approx(1.0)


def test_entity_without_annual_volume_falls_back_to_ist():
    e = _entity("B", [100.0, 20.0])  # annual_volume defaults to 0
    result = aggregate([e], PARAMS, OFFER, DCC_RATE)

    assert result.used == []
    assert result.skipped == ["B"]
    assert result.has_projection is False
    assert result.wl_net_annual == pytest.approx(e.ist_wl_net)
    assert result.sp_net_annual == pytest.approx(e.ist_sp_net)
    # No projection at all -> no planning-band spread.
    assert result.band_low == pytest.approx(result.saving_annual)
    assert result.band_high == pytest.approx(result.saving_annual)
    assert result.coverage.label == CoverageLabel.INDICATIVE
    assert result.portfolio_coverage_pct == pytest.approx(0.0)


def test_mixed_scope_sums_projected_and_ist_and_computes_portfolio_coverage():
    projected = EntityInput(
        label="Projected", key="P", df=_df([100.0, 50.0, 30.0]),
        annual_volume=1_800.0, ist_wl_net=1.0, ist_sp_net=1.0,
        ist_dcc_adv=0.0, ist_txn=3.0, ist_brutto=180.0,
    )
    ist_only = _entity("IstOnly", [20.0])

    result = aggregate([projected, ist_only], PARAMS, OFFER, DCC_RATE)
    direct = project_tier_b(projected.df, PARAMS, OFFER, DCC_RATE,
                            annual_volume=1_800.0)

    assert result.used == ["Projected"]
    assert result.skipped == ["IstOnly"]
    assert result.wl_net_annual == pytest.approx(
        direct.wl_net_annual + ist_only.ist_wl_net)
    assert result.sp_net_annual == pytest.approx(
        direct.sp_net_annual + ist_only.ist_sp_net)
    # Band widens only around the projected portion's saving, Ist part is flat.
    ist_saving = ist_only.ist_wl_net - ist_only.ist_sp_net
    assert result.band_low == pytest.approx(direct.band_low + ist_saving)
    assert result.band_high == pytest.approx(direct.band_high + ist_saving)
    # Portfolio coverage = Ist-Umsatz(projiziert) / Ist-Umsatz(gesamt).
    expected_pct = projected.ist_brutto / (projected.ist_brutto + ist_only.ist_brutto)
    assert result.portfolio_coverage_pct == pytest.approx(expected_pct)


def test_duplicate_annual_volumes_are_flagged_but_still_summed():
    a = _entity("A", [100.0], annual_volume=500_000.0)
    b = _entity("B", [80.0], annual_volume=500_000.0)  # identical -> Fan-out-Verdacht
    c = _entity("C", [60.0], annual_volume=250_000.0)  # unique -> no flag

    result = aggregate([a, b, c], PARAMS, OFFER, DCC_RATE)

    assert sorted(result.used) == ["A", "B", "C"]  # summed regardless of duplicate
    assert result.duplicate_groups == [["A", "B"]]


def test_entity_with_volume_but_empty_df_falls_back_to_ist():
    e = EntityInput(
        label="Empty", key="Empty", df=_df([]), annual_volume=100_000.0,
        ist_wl_net=0.0, ist_sp_net=0.0, ist_dcc_adv=0.0, ist_txn=0.0,
        ist_brutto=0.0,
    )
    result = aggregate([e], PARAMS, OFFER, DCC_RATE)
    assert result.skipped == ["Empty"]
    assert result.used == []


# ── Vorteils-Zerlegung: Acquiring + DCC = Total ───────────────────────────────

def _dcc_df(brutto: list[float]) -> pd.DataFrame:
    """Like _df() but every row runs as DCC on a foreign clearing region, so
    both cashback sides and the DCC/FX volume bases are non-zero."""
    d = _df(brutto)
    d["is_dcc"] = True
    d["region"] = "Intra"
    d["dcc_payback"] = [b * 0.008 for b in brutto]  # WL pays 0.8 %
    return d


def test_advantage_splits_exactly_into_acquiring_and_dcc_when_projected():
    """saving = acquiring + dcc must hold to the cent -- it is an algebraic
    identity (wl_net = wl_fee - wl_cb), not an approximation."""
    e = EntityInput(
        label="A", key="A", df=_dcc_df([100.0, 250.0, 400.0]),
        annual_volume=15_000.0, ist_wl_net=0.0, ist_sp_net=0.0,
        ist_dcc_adv=0.0, ist_txn=3.0, ist_brutto=750.0,
    )
    r = aggregate([e], PARAMS, OFFER, DCC_RATE)

    assert r.saving_annual == pytest.approx(
        r.acquiring_advantage_annual + r.dcc_advantage_annual)
    assert r.acquiring_advantage_annual == pytest.approx(
        r.wl_fee_annual - r.sp_fee_annual)
    assert r.dcc_advantage_annual == pytest.approx(
        r.sp_cashback_annual - r.wl_cashback_annual)
    # SwiPay pays 1.4 % vs Worldline 0.8 % -> DCC leg is a gain, not a saving.
    assert r.dcc_advantage_annual > 0
    # Effective denominator = the projected annual volume.
    assert r.brutto_annual == pytest.approx(15_000.0)
    # Every row is DCC on a foreign region -> both bases equal the volume.
    assert r.dcc_volume_annual == pytest.approx(15_000.0)
    assert r.fx_volume_annual == pytest.approx(15_000.0)


def test_advantage_split_holds_for_mixed_projected_and_ist_scope():
    """A skipped entity contributes its Ist decomposition; the identity must
    survive the mix of projected and Ist entities."""
    projected = EntityInput(
        label="P", key="P", df=_dcc_df([200.0, 300.0]), annual_volume=10_000.0,
        ist_wl_net=0.0, ist_sp_net=0.0, ist_dcc_adv=0.0, ist_txn=2.0,
        ist_brutto=500.0,
    )
    # Consistent Ist decomposition: wl_net = wl_fee - wl_cb, sp_net = sp_fee - sp_cb.
    ist_only = EntityInput(
        label="I", key="I", df=_df([50.0]), annual_volume=0.0,
        ist_wl_net=9.0 - 2.0, ist_sp_net=6.0 - 3.0, ist_dcc_adv=3.0 - 2.0,
        ist_txn=1.0, ist_brutto=50.0,
        ist_wl_fee=9.0, ist_sp_fee=6.0, ist_wl_cashback=2.0, ist_sp_cashback=3.0,
        ist_dcc_vol=50.0, ist_fx_vol=50.0,
    )
    r = aggregate([projected, ist_only], PARAMS, OFFER, DCC_RATE)

    assert r.used == ["P"] and r.skipped == ["I"]
    assert r.saving_annual == pytest.approx(
        r.acquiring_advantage_annual + r.dcc_advantage_annual)
    # Denominator mixes projected annual volume and the Ist entity's Umsatz.
    assert r.brutto_annual == pytest.approx(10_000.0 + 50.0)
    assert r.fx_volume_annual == pytest.approx(10_000.0 + 50.0)
