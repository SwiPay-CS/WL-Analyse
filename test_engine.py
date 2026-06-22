"""Unit-Tests fuer den Engine-Kern. Decken genau die nichtlinearen Stellen ab."""

import numpy as np
import pandas as pd
import pytest

from engine import BrandParams, ParamTable, Offer, swipay_fee, worldline_net
from pipeline import run_comparison


DEBIT = BrandParams(asf_pct=0.001, asf_fix=0.0, min_fee=0.10, dcc_cashback_pct=0.014)


def test_identity_reconstruction():
    """Worldline-Basis: fee_total = PF + SF + IC."""
    fee, cb, net = worldline_net(0.09, 0.05, 0.20, dcc_payback=0.0)
    assert fee == pytest.approx(0.34)
    assert net == pytest.approx(0.34)


def test_floor_binds_on_small_ticket():
    """Kleinbetrag: ASF+SF+IC unter min_fee -> Floor greift, realisiert via ASF."""
    r = swipay_fee(brutto=5.0, scheme_fee=0.0, interchange=0.0, params=DEBIT,
                   is_dcc=False, is_refund=False, offerable=True)
    assert r.floored is True
    assert r.fee_total == pytest.approx(0.10)
    assert r.asf == pytest.approx(0.10)


def test_floor_does_not_bind_when_components_exceed_min():
    """SF+IC schon ueber min_fee -> kein Floor, ICF/CSF bleiben unberuehrt."""
    r = swipay_fee(brutto=5.0, scheme_fee=0.08, interchange=0.05, params=DEBIT,
                   is_dcc=False, is_refund=False, offerable=True)
    assert r.floored is False
    assert r.fee_total == pytest.approx(0.005 + 0.08 + 0.05)


def test_dcc_cashback_applied_after_floor():
    """Floor entscheidet sich VOR dem Cashback; Cashback ist separate Gutschrift."""
    r = swipay_fee(brutto=5.0, scheme_fee=0.0, interchange=0.0, params=DEBIT,
                   is_dcc=True, is_refund=False, offerable=True)
    assert r.floored is True              # floor decided on fee, not on net
    assert r.fee_total == pytest.approx(0.10)
    assert r.cashback == pytest.approx(0.07)   # 0.014 * 5
    assert r.net_cost == pytest.approx(0.03)


def test_refund_passes_through_with_reversed_sign_and_no_floor():
    r = swipay_fee(brutto=-50.0, scheme_fee=0.05, interchange=0.03, params=DEBIT,
                   is_dcc=False, is_refund=True, offerable=True)
    assert r.floored is False
    assert r.fee_total == pytest.approx(-(0.05 + 0.05 + 0.03))
    assert r.cashback == 0.0
    assert r.net_cost < 0


def test_non_offerable_brand_is_zero():
    r = swipay_fee(brutto=20.0, scheme_fee=0.0, interchange=0.0, params=DEBIT,
                   is_dcc=False, is_refund=False, offerable=False)
    assert r.offerable is False
    assert r.fee_total == 0.0


def _sample_df():
    return pd.DataFrame({
        "brutto":        [5.0, 100.0, -50.0, 30.0],
        "gebuehren":     [-0.09, -1.00, 0.12, -0.30],
        "scheme_fee":    [-0.0, -0.30, -0.05, -0.10],
        "interchange":   [-0.0, -0.20, -0.03, -0.08],
        "processing_fee":[-0.09, -0.50, -0.04, -0.12],
        "dcc_payback":   [0.0, 1.40, 0.0, 0.0],
        "category":      ["Debit", "Credit", "Debit", "Debit"],
        "brand":         ["VisaDebit", "Visa", "VisaDebit", "TWINT"],
        "is_dcc":        [False, True, False, False],
        "is_refund":     [False, False, True, False],
    })


def test_non_offerable_brand_delta_zero_in_pipeline():
    """TWINT nicht im Angebot -> SwiPay-Netto = Worldline-Netto (Delta 0)."""
    df = _sample_df()
    params = ParamTable({"Debit": DEBIT, "Credit": DEBIT})
    offer = Offer(frozenset({"VisaDebit", "Visa"}))  # TWINT excluded
    res = run_comparison(df, params, offer)
    twint = res[df["brand"] == "TWINT"].iloc[0]
    assert twint["sp_net"] == pytest.approx(twint["wl_net"])
    assert bool(twint["offerable"]) is False


def test_vectorized_matches_scalar():
    """run_comparison muss zeilenweise mit swipay_fee uebereinstimmen."""
    df = _sample_df()
    params = ParamTable({"Debit": DEBIT, "Credit": DEBIT})
    offer = Offer(frozenset({"VisaDebit", "Visa"}))
    res = run_comparison(df, params, offer)

    for i, row in df.iterrows():
        scalar = swipay_fee(
            brutto=row["brutto"],
            scheme_fee=abs(row["scheme_fee"]),
            interchange=abs(row["interchange"]),
            params=params.resolve(row["category"]),
            is_dcc=bool(row["is_dcc"]),
            is_refund=bool(row["is_refund"]),
            offerable=offer.is_offerable(row["brand"]),
        )
        if offer.is_offerable(row["brand"]):
            assert res.loc[i, "sp_net"] == pytest.approx(scalar.net_cost)
