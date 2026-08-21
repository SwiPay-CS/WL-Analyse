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


# ── Vorteils-Wasserfall ───────────────────────────────────────────────────────

def _waterfall_rows(wl: float, acq: float, dcc: float, sp: float) -> dict:
    """Bar rows of chart_advantage_waterfall(), keyed by Schritt."""
    spec = ui.chart_advantage_waterfall(wl, acq, dcc, sp).to_dict()
    for rows in spec["datasets"].values():
        if rows and "Schritt" in rows[0]:
            return {r["Schritt"]: r for r in rows}
    raise AssertionError("no bar dataset in waterfall spec")


@pytest.mark.parametrize("wl,acq,dcc", [
    (530544.0,  41025.0,  18691.0),   # beide Hebel positiv (Normalfall)
    (100000.0, -15000.0,   5000.0),   # Acquiring teurer, DCC besser
    (100000.0,  20000.0,  -8000.0),   # Acquiring besser, DCC schlechter
    (100000.0, -12000.0,  -3000.0),   # SwiPay auf beiden Beinen teurer
    (100000.0,      0.0,      0.0),   # Null-Effekt (z.B. nur TWINT)
])
def test_waterfall_closes_exactly_for_every_sign_combination(wl, acq, dcc):
    """Der Wasserfall darf keine Restposition brauchen: der Acquiring-Balken
    startet auf wl_net, der DCC-Balken endet auf sp_net. Gilt in jedem
    Vorzeichen-Fall, weil wl_net - acquiring - dcc == sp_net eine Identitaet
    ist (siehe engine.py)."""
    sp = wl - acq - dcc
    r = _waterfall_rows(wl, acq, dcc, sp)

    assert r["Worldline"]["hi"] == pytest.approx(wl)
    assert r["SwiPay"]["hi"] == pytest.approx(sp)
    # Floating bars: Reihenfolge lo/hi ist normalisiert, also beide Enden pruefen.
    assert any(e == pytest.approx(wl)
               for e in (r["Acquiring"]["lo"], r["Acquiring"]["hi"]))
    assert any(e == pytest.approx(sp) for e in (r["DCC"]["lo"], r["DCC"]["hi"]))
    # Die beiden Delta-Balken beruehren sich beim Zwischenstand.
    mid = wl - acq
    assert any(e == pytest.approx(mid)
               for e in (r["Acquiring"]["lo"], r["Acquiring"]["hi"]))
    assert any(e == pytest.approx(mid) for e in (r["DCC"]["lo"], r["DCC"]["hi"]))


def test_waterfall_colours_signal_the_direction():
    """Ein Bein, auf dem SwiPay schlechter ist, muss rot erscheinen -- nicht
    gruen/cyan wie ein Vorteil."""
    good = _waterfall_rows(100000.0, 20000.0, 5000.0, 75000.0)
    assert good["Acquiring"]["farbe"] == ui.GREEN
    assert good["DCC"]["farbe"] == ui.CYAN

    bad = _waterfall_rows(100000.0, -20000.0, -5000.0, 125000.0)
    assert bad["Acquiring"]["farbe"] == ui.DUNKELROT
    assert bad["DCC"]["farbe"] == ui.DUNKELROT


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
