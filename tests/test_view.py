"""Tests fuer view.py -- das gemeinsame Zahlenpaket von Praesentation und PDF."""

import pytest

from view import BASIS_IST, BASIS_PA, build_view


class _Agg:
    """Minimales AggregateProjection-Double."""
    brutto_annual = 70_000_000.0
    n_txn_annual = 600_000.0
    wl_fee_annual = 588_000.0
    sp_fee_annual = 547_000.0
    wl_net_annual = 530_000.0
    sp_net_annual = 470_000.0
    wl_cashback_annual = 58_000.0
    sp_cashback_annual = 77_000.0
    acquiring_advantage_annual = 41_000.0
    sp_asf_annual = 1_400.0
    wl_processing_annual = 2_100.0
    dcc_advantage_annual = 19_000.0
    saving_annual = 60_000.0
    dcc_volume_annual = 4_100_000.0
    fx_volume_annual = 20_900_000.0
    dcc_purchase_volume_annual = 4_120_000.0
    fx_purchase_volume_annual = 21_000_000.0
    band_low = 51_000.0
    band_high = 69_000.0


_T = {"wl_fee": 588.0, "sp_fee": 547.0, "wl_net": 530.0, "sp_net": 470.0,
      "wl_cashback": 58.0, "sp_cashback": 77.0,
      "sp_asf": 140.0, "wl_processing": 210.0}
_D = {"brutto": 70_000.0, "n_txn": 600, "avg_ticket": 130.0,
      "dcc_vol": 4_100.0, "fx_vol": 20_900.0,
      "dcc_vol_purch": 4_120.0, "fx_vol_purch": 21_000.0}


def test_pa_basis_reads_only_from_the_aggregate():
    v = build_view(_Agg(), _T, _D, BASIS_PA)
    assert v.is_projected
    assert v.suffix == "p.a."
    assert v.brutto == pytest.approx(70_000_000.0)
    assert v.total == pytest.approx(60_000.0)
    assert v.band_low == pytest.approx(51_000.0)


def test_ist_basis_reads_only_from_totals_and_derived():
    v = build_view(_Agg(), _T, _D, BASIS_IST)
    assert not v.is_projected
    assert v.suffix == "im Zeitraum"
    assert v.brutto == pytest.approx(70_000.0)
    assert v.total == pytest.approx(530.0 - 470.0)
    # Ohne Hochrechnung gibt es kein Planungsband.
    assert v.band_low is None and v.band_high is None


@pytest.mark.parametrize("basis", [BASIS_PA, BASIS_IST])
def test_advantage_decomposes_exactly_on_both_bases(basis):
    v = build_view(_Agg(), _T, _D, basis)
    assert v.total == pytest.approx(v.acquiring + v.dcc)


def test_fee_change_is_the_inverse_of_the_saving():
    """Die Kachel zeigt die Richtung der GEBUEHREN: Ersparnis -> negativ."""
    v = build_view(_Agg(), _T, _D, BASIS_PA)
    assert v.rel_pct > 0
    assert v.fee_change_pct == pytest.approx(-v.rel_pct)


def test_rate_is_none_without_a_turnover_base():
    """Lieber eine Luecke als eine 0: ohne Umsatz ist die Rate undefiniert."""
    d = dict(_D, brutto=0.0)
    v = build_view(_Agg(), _T, d, BASIS_IST)
    assert v.wl_rate is None and v.sp_rate is None and v.rate_delta is None


def test_cashback_ceiling_uses_the_purchase_base():
    """Cashback wird nur auf Kaeufe gezahlt -- die Obergrenze deshalb auch."""
    v = build_view(_Agg(), _T, _D, BASIS_PA)
    assert v.cashback_ceiling(0.0185) == pytest.approx(0.0185 * 21_000_000.0)


def test_dcc_share_uses_the_net_base_and_survives_zero_volume():
    v = build_view(_Agg(), _T, _D, BASIS_PA)
    assert v.dcc_share == pytest.approx(4_100_000.0 / 20_900_000.0)
    d = dict(_D, fx_vol=0.0)
    assert build_view(_Agg(), _T, d, BASIS_IST).dcc_share == 0.0


# ── ASF-Vergleich ─────────────────────────────────────────────────────────────

def test_asf_change_uses_the_same_sign_convention_as_the_fee_change():
    """Minus = SwiPays ASF ist günstiger, wie bei fee_change_pct."""
    v = build_view(_Agg(), _T, _D, BASIS_PA)
    # 1'400 gegen 2'100 -> ein Drittel günstiger.
    assert v.asf_change_pct == pytest.approx((1_400 - 2_100) / 2_100 * 100)
    assert v.asf_change_pct < 0


def test_asf_change_matches_the_rate_ratio():
    """Die Veränderung muss identisch sein, ob man sie aus den Beträgen oder
    aus den beiden Ø-Sätzen rechnet -- derselbe Nenner kürzt sich weg."""
    v = build_view(_Agg(), _T, _D, BASIS_PA)
    from_rates = (v.asf_rate - v.wl_processing_rate) / v.wl_processing_rate * 100
    assert v.asf_change_pct == pytest.approx(from_rates)


def test_asf_change_is_none_without_a_worldline_basis():
    """Ohne WL-ASF ist die Relation undefiniert -- None, nicht 0."""
    t = dict(_T, wl_processing=0.0)
    assert build_view(_Agg(), t, _D, BASIS_IST).asf_change_pct is None
