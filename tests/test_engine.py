"""Unit tests for the SwiPay fee engine.

Covers the critical mechanics from the engine spec:
  1. Identity: fee_total == asf_new + sf + ic (always, including after floor)
  2. Minimum-fee floor activates for small amounts
  3. Floor does NOT activate for normal amounts
  4. DCC order: floor applied first, then cashback as separate credit
  5. DCC cashback never lowers fee_total below min_fee
  6. Refund: negative fees, no floor, no DCC cashback
  7. Non-offerable brand (e.g. TWINT): all fee components are zero
  8. Unknown category falls back to Credit parameters without raising
"""
import pytest

from swipay_wl_compare.engine import TxResult, compute_swipay
from swipay_wl_compare.params import NON_OFFERABLE, PARAMS


# ---------------------------------------------------------------------------
# 1. Identity: fee_total = asf_new + sf + ic
# ---------------------------------------------------------------------------

def test_identity_normal_purchase():
    sf, ic = 0.05, 0.03
    r = compute_swipay(
        brutto=50.0, scheme_fee=sf, interchange=ic,
        category="Credit", is_dcc=False, brand="Visa", is_refund=False,
    )
    assert r.offerable
    assert not r.floored
    assert r.fee_total == pytest.approx(r.asf_new + sf + ic, abs=1e-9)


def test_identity_holds_after_floor():
    """Identity must hold even when the floor raises asf_new."""
    sf, ic = 0.00, 0.00
    r = compute_swipay(
        brutto=1.0, scheme_fee=sf, interchange=ic,
        category="Credit", is_dcc=False, brand="Visa", is_refund=False,
    )
    assert r.floored
    assert r.fee_total == pytest.approx(r.asf_new + sf + ic, abs=1e-9)


# ---------------------------------------------------------------------------
# 2. Minimum-fee floor activates for small amounts
# ---------------------------------------------------------------------------

def test_floor_activates_small_amount():
    """CHF 1.00 × 0.16 % = 0.0016 CHF ASF — well below Credit min_fee of 0.12."""
    sf, ic = 0.00, 0.00
    r = compute_swipay(
        brutto=1.0, scheme_fee=sf, interchange=ic,
        category="Credit", is_dcc=False, brand="Visa", is_refund=False,
    )
    p = PARAMS["Credit"]
    assert r.floored
    assert r.fee_total == pytest.approx(p.min_fee, abs=1e-9)


def test_floor_sets_fee_to_min_fee_when_sf_ic_are_zero():
    """When sf=ic=0, fee_total after floor equals exactly min_fee."""
    r = compute_swipay(
        brutto=0.50, scheme_fee=0.0, interchange=0.0,
        category="Debit", is_dcc=False, brand="Maestro", is_refund=False,
    )
    assert r.floored
    assert r.fee_total == pytest.approx(PARAMS["Debit"].min_fee, abs=1e-9)


# ---------------------------------------------------------------------------
# 3. Floor does NOT activate for large amounts
# ---------------------------------------------------------------------------

def test_floor_does_not_activate_large_amount():
    sf, ic = 0.05, 0.03
    r = compute_swipay(
        brutto=200.0, scheme_fee=sf, interchange=ic,
        category="Credit", is_dcc=False, brand="Mastercard", is_refund=False,
    )
    p = PARAMS["Credit"]
    assert not r.floored
    expected = p.asf_pct * 200.0 + p.asf_fix + sf + ic
    assert r.fee_total == pytest.approx(expected, abs=1e-9)


# ---------------------------------------------------------------------------
# 4. DCC order: floor first, then cashback as separate credit
# ---------------------------------------------------------------------------

def test_dcc_cashback_applied_after_floor():
    """For a DCC transaction that triggers the floor, fee_total equals min_fee
    and cashback is non-zero and separate."""
    sf, ic = 0.00, 0.00
    brutto = 1.0
    r = compute_swipay(
        brutto=brutto, scheme_fee=sf, interchange=ic,
        category="Credit", is_dcc=True, brand="Visa", is_refund=False,
    )
    p = PARAMS["Credit"]
    assert r.floored
    assert r.fee_total == pytest.approx(p.min_fee, abs=1e-9)
    expected_cashback = p.dcc_cashback_pct * brutto
    assert r.cashback == pytest.approx(expected_cashback, abs=1e-9)
    assert r.net_cost == pytest.approx(r.fee_total - r.cashback, abs=1e-9)


# ---------------------------------------------------------------------------
# 5. DCC cashback never lowers fee_total below min_fee
# ---------------------------------------------------------------------------

def test_dcc_cashback_does_not_reduce_fee_total_below_min_fee():
    """fee_total must reflect the floored value; cashback only lowers net_cost."""
    sf, ic = 0.00, 0.00
    r = compute_swipay(
        brutto=5.0, scheme_fee=sf, interchange=ic,
        category="Credit", is_dcc=True, brand="Visa", is_refund=False,
    )
    p = PARAMS["Credit"]
    assert r.fee_total >= p.min_fee - 1e-9


# ---------------------------------------------------------------------------
# 6. Refund: negative fees, no floor, no DCC cashback
# ---------------------------------------------------------------------------

def test_refund_sign_correct():
    """Refund fees are negative magnitudes."""
    sf, ic = 0.05, 0.03
    r = compute_swipay(
        brutto=-50.0, scheme_fee=sf, interchange=ic,
        category="Credit", is_dcc=False, brand="Visa", is_refund=True,
    )
    assert r.offerable
    assert not r.floored
    assert r.asf_new < 0
    assert r.fee_total < 0
    assert r.cashback == 0.0
    assert r.net_cost == r.fee_total


def test_refund_no_dcc_cashback():
    """DCC flag is irrelevant for refunds — no cashback is generated."""
    r = compute_swipay(
        brutto=-50.0, scheme_fee=0.05, interchange=0.03,
        category="Credit", is_dcc=True, brand="Visa", is_refund=True,
    )
    assert r.cashback == 0.0


def test_refund_no_floor_even_for_tiny_amount():
    """A tiny refund must NOT be floored."""
    r = compute_swipay(
        brutto=-0.50, scheme_fee=0.00, interchange=0.00,
        category="Credit", is_dcc=False, brand="Visa", is_refund=True,
    )
    assert not r.floored
    assert r.fee_total < 0


# ---------------------------------------------------------------------------
# 7. Non-offerable brand: all fee components are zero, delta = 0
# ---------------------------------------------------------------------------

def test_non_offerable_brand_delta_zero():
    brand = next(iter(NON_OFFERABLE))  # e.g. "TWINT"
    r = compute_swipay(
        brutto=100.0, scheme_fee=0.05, interchange=0.10,
        category="Debit", is_dcc=False, brand=brand, is_refund=False,
    )
    assert not r.offerable
    assert r.asf_new == 0.0
    assert r.fee_total == 0.0
    assert r.cashback == 0.0
    assert r.net_cost == 0.0


def test_non_offerable_brand_with_dcc_still_zero():
    brand = next(iter(NON_OFFERABLE))
    r = compute_swipay(
        brutto=100.0, scheme_fee=0.05, interchange=0.10,
        category="Debit", is_dcc=True, brand=brand, is_refund=False,
    )
    assert r.net_cost == 0.0


# ---------------------------------------------------------------------------
# 8. Unknown category falls back to Credit parameters
# ---------------------------------------------------------------------------

def test_unknown_category_fallback():
    r = compute_swipay(
        brutto=100.0, scheme_fee=0.05, interchange=0.03,
        category="UnknownXYZ", is_dcc=False, brand="Visa", is_refund=False,
    )
    p = PARAMS["Credit"]
    expected_asf = p.asf_pct * 100.0 + p.asf_fix
    assert r.asf_new == pytest.approx(expected_asf, abs=1e-9)
