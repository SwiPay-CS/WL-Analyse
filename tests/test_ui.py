"""Tests for ui.py: pure formatting helpers and chart specs (the chart
builders are Altair-only, so they need no Streamlit runtime either)."""

import pandas as pd
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
    assert r["Aktuell"]["Wert"] == pytest.approx(530544.0)
    assert r["SwiPay"]["Wert"] == pytest.approx(470828.0)
    assert r["Aktuell"]["lbl"] == "530'544"


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
    assert by_seg["DCC genutzt"]["lbl"] == "19.7%"
    assert by_seg["Nicht genutzt"]["lbl"] == "80.3%"


def test_dcc_share_donut_handles_zero_volume():
    """Ohne Fremdwaehrungsvolumen darf nicht durch Null geteilt werden."""
    spec = ui.chart_dcc_share(0.0, 0.0).to_dict()
    rows = next(r for r in spec["datasets"].values() if r and "Segment" in r[0])
    assert all(r["Anteil"] == 0.0 for r in rows)


# ── Ersparnis nach Kartentyp ──────────────────────────────────────────────────

_TYPE_ORDER = ["Debit", "Credit M/V", "Credit Rest", "QR-Code / n/a"]


def _type_chart_order(rows: list[tuple[str, float]]) -> list[str]:
    df = pd.DataFrame(rows, columns=["Typ", "Ersparnis"])
    spec = ui.chart_savings_by_type(df, order=_TYPE_ORDER).to_dict()
    return spec["layer"][0]["encoding"]["y"]["sort"]


def test_savings_by_type_keeps_the_fixed_order_regardless_of_amount():
    """Debit, Credit M/V, Credit Rest -- immer, auch wenn die Betraege eine
    andere Reihenfolge nahelegen. Sonst springen die Zeilen im Kundentermin."""
    order = _type_chart_order([
        ("Credit Rest", 52.0), ("Debit", 24_854.0), ("Credit M/V", 34_810.0)])
    assert order == ["Debit", "Credit M/V", "Credit Rest"]

    # Umgekehrte Betragslage, identische Reihenfolge.
    order = _type_chart_order([
        ("Credit M/V", 10.0), ("Credit Rest", 90_000.0), ("Debit", 500.0)])
    assert order == ["Debit", "Credit M/V", "Credit Rest"]


def test_savings_by_type_omits_types_absent_from_the_data():
    """Ein Typ ohne Daten darf keine leere Zeile im Chart reservieren."""
    order = _type_chart_order([("Debit", 100.0), ("Credit Rest", 20.0)])
    assert order == ["Debit", "Credit Rest"]


def test_savings_by_type_without_order_still_sorts_by_amount():
    df = pd.DataFrame({"Typ": ["A", "B"], "Ersparnis": [90.0, 10.0]})
    spec = ui.chart_savings_by_type(df).to_dict()
    assert spec["layer"][0]["encoding"]["y"]["sort"] == ["B", "A"]


# ── Betragseingabe: parse_chf ─────────────────────────────────────────────────

@pytest.mark.parametrize("text,want", [
    ("1159429",        1_159_429.0),
    ("1'159'429",      1_159_429.0),   # Schweizer Apostroph
    ("1’159’429",      1_159_429.0),   # typografischer Apostroph
    ("1 159 429",      1_159_429.0),   # Leerzeichen
    ("1 159 429", 1_159_429.0),  # geschuetztes Leerzeichen
    ("1,159,429",      1_159_429.0),   # Kommas als Tausendertrenner
    ("1'159'429.50",   1_159_429.5),
    ("1159429,50",     1_159_429.5),   # Dezimalkomma
    ("  17490  ",         17_490.0),
    ("0",                      0.0),
    ("",                       0.0),   # leer = 0, keine Fehleingabe
])
def test_parse_chf_accepts_every_realistic_spelling(text, want):
    assert ui.parse_chf(text) == pytest.approx(want)


@pytest.mark.parametrize("text", ["abc", "12.34.56", "-500", "1.2.3", "CHF 500"])
def test_parse_chf_rejects_unreadable_input(text):
    """None, nicht 0 -- eine stille 0 wuerde eine Hochrechnung loeschen."""
    assert ui.parse_chf(text) is None


def test_parse_chf_round_trips_with_chf():
    for v in (0.0, 17_490.0, 1_159_429.5, 70_323_760.25):
        assert ui.parse_chf(ui.chf(v, 2)) == pytest.approx(v)
