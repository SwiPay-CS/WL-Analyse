"""Tests for ui.py: pure formatting helpers and chart specs (the chart
builders are Altair-only, so they need no Streamlit runtime either)."""

import pytest

import ui
from ui import pid


def test_pid_strips_trailing_dot_zero():
    assert pid(31035.0) == "31035"
    assert pid("31035.0") == "31035"


def test_pid_leaves_real_values_untouched():
    assert pid("31035") == "31035"
    assert pid("ABC-123") == "ABC-123"


def test_pid_strips_surrounding_whitespace():
    assert pid("  31035.0  ") == "31035"


# ── Vergleichs-Charts ─────────────────────────────────────────────────────────

def _bar_rows(chart) -> dict:
    """Bar rows of chart_compare(), keyed by Anbieter."""
    spec = chart.to_dict()
    for rows in spec["datasets"].values():
        if rows and "Anbieter" in rows[0]:
            return {r["Anbieter"]: r for r in rows}
    raise AssertionError("no bar dataset in compare spec")


def test_compare_chart_carries_both_sides_and_swiss_labels():
    r = _bar_rows(ui.chart_compare(530544.0, 470828.0, "Gebühren CHF"))
    assert r["Worldline"]["Wert"] == pytest.approx(530544.0)
    assert r["SwiPay"]["Wert"] == pytest.approx(470828.0)
    assert r["Worldline"]["lbl"] == "530'544"


def test_compare_chart_handles_a_worse_swipay_side():
    """SwiPay teurer: der Balken muss trotzdem korrekt skalieren, nicht am
    Rand abgeschnitten werden."""
    chart = ui.chart_compare(100.0, 140.0, "Gebühren CHF")
    r = _bar_rows(chart)
    assert r["SwiPay"]["Wert"] == pytest.approx(140.0)
    dom = chart.to_dict()["layer"][0]["encoding"]["y"]["scale"]["domain"]
    assert dom[1] > 140.0     # oberes Ende laesst Platz fuer das Label


def test_compare_chart_handles_all_zero_without_a_degenerate_scale():
    dom = (ui.chart_compare(0.0, 0.0, "Gebühren CHF")
           .to_dict()["layer"][0]["encoding"]["y"]["scale"]["domain"])
    assert dom[0] < dom[1]


def test_dcc_share_donut_labels_percentages():
    spec = ui.chart_dcc_share(4_118_543.0, 20_904_954.0).to_dict()
    rows = next(r for r in spec["datasets"].values() if r and "Segment" in r[0])
    by_seg = {r["Segment"]: r for r in rows}
    assert by_seg["DCC genutzt"]["lbl"] == "20%"
    assert by_seg["Nicht genutzt"]["lbl"] == "80%"


def test_dcc_share_donut_handles_zero_volume():
    """Ohne Fremdwaehrungsvolumen darf nicht durch Null geteilt werden."""
    spec = ui.chart_dcc_share(0.0, 0.0).to_dict()
    rows = next(r for r in spec["datasets"].values() if r and "Segment" in r[0])
    assert all(r["Anteil"] == 0.0 for r in rows)
