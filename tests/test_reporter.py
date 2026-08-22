"""Tests fuer reporter.py's build_pdf.

Kein voller Inhaltsvergleich -- geprueft wird, dass das PDF rendert, dass es
der gewaehlten ANSICHT folgt (Hochrechnung p.a. gegen Ist-Zeitraum) und dass
die Vorzeichen-Faelle und Randfaelle nicht abstuerzen.
"""

import pytest

from projection import CoverageLabel, CoverageTier
from aggregation import AggregateProjection
from reporter import build_pdf
from view import BASIS_IST, BASIS_PA, build_view

_T = {"wl_fee": 600.0, "sp_fee": 540.0, "wl_net": 590.0, "sp_net": 520.0,
      "wl_cashback": 10.0, "sp_cashback": 20.0,
      "sp_asf": 140.0, "wl_processing": 210.0}
_D = {"brutto": 100_000.0, "n_txn": 900, "n_term": 4, "avg_ticket": 111.0,
      "dcc_vol": 8_000.0, "fx_vol": 40_000.0,
      "dcc_vol_purch": 8_100.0, "fx_vol_purch": 40_400.0}

_META = dict(partner_name="Test AG", period_from="2024-01", period_to="2024-12",
             n_terminals=4, dcc_pct=0.014, generated_date="01.01.2026")


def _agg(label: CoverageLabel = CoverageLabel.LOW_COVERAGE) -> AggregateProjection:
    return AggregateProjection(
        wl_net_annual=5_900.0, sp_net_annual=5_200.0, saving_annual=700.0,
        dcc_advantage_annual=100.0, n_txn_annual=9_000.0,
        band_low=595.0, band_high=805.0,
        coverage=CoverageTier(label, 0.4), annual_volume_total=1_000_000.0,
        portfolio_coverage_pct=0.6,
        wl_fee_annual=6_000.0, sp_fee_annual=5_400.0,
        acquiring_advantage_annual=600.0,
        sp_asf_annual=140.0, wl_processing_annual=210.0,
        wl_cashback_annual=100.0, sp_cashback_annual=200.0,
        brutto_annual=1_000_000.0,
        dcc_volume_annual=80_000.0, fx_volume_annual=400_000.0,
        dcc_purchase_volume_annual=81_000.0, fx_purchase_volume_annual=404_000.0,
        used=["A", "B"], skipped=["C"], duplicate_groups=[["A", "B"]],
        scales=[10.0, 10.0, 1.0],
    )


_TYPES = [("Debit", 300.0), ("Credit M/V", 380.0), ("Credit Rest", 20.0)]


def test_ist_view_renders_without_a_projection_block():
    agg = _agg()
    v = build_view(agg, _T, _D, BASIS_IST)
    pdf = build_pdf(view=v, savings_by_type=_TYPES, **_META)
    assert pdf[:4] == b"%PDF"


def test_pa_view_carries_the_projection_block():
    agg = _agg()
    v = build_view(agg, _T, _D, BASIS_PA)
    pdf = build_pdf(view=v, savings_by_type=_TYPES, projection=agg,
                    portfolio_coverage_pct=agg.portfolio_coverage_pct,
                    n_entities_used=2, n_entities_total=3,
                    duplicate_volume_warnings=agg.duplicate_groups, **_META)
    assert pdf[:4] == b"%PDF"


@pytest.mark.parametrize("label", list(CoverageLabel))
def test_every_coverage_label_renders(label):
    agg = _agg(label)
    v = build_view(agg, _T, _D, BASIS_PA)
    pdf = build_pdf(view=v, savings_by_type=_TYPES, projection=agg,
                    portfolio_coverage_pct=0.6, n_entities_used=2,
                    n_entities_total=3, **_META)
    assert pdf[:4] == b"%PDF"


def test_negative_advantage_renders():
    """SwiPay teurer auf beiden Beinen -- die Balken kippen unter die Nulllinie."""
    t = dict(_T, sp_fee=700.0, sp_net=690.0, sp_cashback=5.0)
    v = build_view(_agg(), t, _D, BASIS_IST)
    assert v.total < 0 and v.acquiring < 0 and v.dcc < 0
    assert build_pdf(view=v, savings_by_type=[("Debit", -50.0)], **_META)[:4] == b"%PDF"


def test_renders_without_optional_blocks():
    """Ohne Kartentyp-Aufschluesselung, ohne Hinweise, ohne Terminals."""
    v = build_view(_agg(), _T, _D, BASIS_IST)
    pdf = build_pdf(view=v, partner_name="X", period_from="-", period_to="-",
                    generated_date="01.01.2026")
    assert pdf[:4] == b"%PDF"


def test_renders_with_zero_turnover():
    """Kein Umsatz -> keine Gebuehrenrate. Darf nicht durch Null teilen."""
    d = dict(_D, brutto=0.0, fx_vol=0.0, fx_vol_purch=0.0, dcc_vol=0.0)
    v = build_view(_agg(), _T, d, BASIS_IST)
    assert v.wl_rate is None
    assert build_pdf(view=v, **_META)[:4] == b"%PDF"


def test_all_data_hints_render_together():
    agg = _agg(CoverageLabel.INDICATIVE)
    v = build_view(agg, _T, _D, BASIS_PA)
    pdf = build_pdf(view=v, savings_by_type=_TYPES, projection=agg,
                    portfolio_coverage_pct=0.3, n_entities_used=2,
                    n_entities_total=5,
                    duplicate_volume_warnings=[["A", "B"], ["C", "D"]],
                    fanout_partner_ids=["31035", "31036"],
                    zero_effect_brands=["TWINT", "WeChat Pay"],
                    mix_hints=["Visa: share 20 % observed vs 35 % annual"],
                    **_META)
    assert pdf[:4] == b"%PDF"


# ── Schrift: Saira und der Ersatz-Pfad ────────────────────────────────────────

def test_saira_faces_are_bundled_in_the_repo():
    """Die TTFs liegen bewusst IM Repo, damit das PDF ueberall gleich rendert
    und nicht vom persoenlich installierten Font-Ordner abhaengt."""
    import reporter
    for fname in reporter._SAIRA_FACES.values():
        assert (reporter._FONTS / fname).exists(), fname


def test_renders_without_the_saira_files(monkeypatch, tmp_path):
    """Fehlen die TTFs, muss der Bericht in der Ersatzschrift entstehen statt
    zu crashen -- inklusive Gedankenstrich und «», die Latin-1 nicht kennt.
    Genau daran ist der Fallback beim Bauen zerbrochen."""
    import reporter
    monkeypatch.setattr(reporter, "_FONTS", tmp_path / "keine-fonts")
    v = build_view(_agg(), _T, _D, BASIS_IST)
    pdf = build_pdf(view=v, savings_by_type=_TYPES,
                    zero_effect_brands=["TWINT"], **_META)
    assert pdf[:4] == b"%PDF"


def test_umlauts_and_guillemets_survive_both_font_paths(monkeypatch, tmp_path):
    import reporter
    v = build_view(_agg(), _T, _D, BASIS_IST)
    meta = {k: val for k, val in _META.items() if k != "partner_name"}
    kw = dict(view=v, partner_name="Ötzi «Test» AG Zürich",
              savings_by_type=_TYPES, zero_effect_brands=["TWINT"], **meta)
    assert build_pdf(**kw)[:4] == b"%PDF"
    monkeypatch.setattr(reporter, "_FONTS", tmp_path / "keine-fonts")
    assert build_pdf(**kw)[:4] == b"%PDF"


# ── Negativ-Logo im dunklen Header ────────────────────────────────────────────

def test_negative_logo_is_present_and_found():
    """Der dunkle Balken braucht das Negativ-Logo. Das Positiv-Logo darf dort
    NIE landen: seine Wortmarke ist selbst anthrazit (Brand & CI v2.1 verbietet
    Umfärben)."""
    import reporter
    found = reporter._negative_logo()
    assert found is not None, "kein Negativ-Logo in assets/ gefunden"
    assert "logo" in found.stem.lower()
    assert any(m in found.stem.lower() for m in reporter._NEG_MARKERS)
    assert found.name != "SWIPAY-Logo.svg"


def test_negative_logo_lookup_is_pattern_based(monkeypatch, tmp_path):
    """Illustrator-Exporte variieren in der Schreibweise (Binde- vs.
    Unterstrich, Sprach-Suffix). Eine feste Namensliste hat die echte Datei
    «SWIPAY_Logo_negativ_de.svg» verpasst -- deshalb Muster."""
    import reporter
    monkeypatch.setattr(reporter, "_ASSETS", tmp_path)
    for name in ("SWIPAY_Logo_negativ_de.svg", "SWIPAY-Logo-negative.png",
                 "swipay_logo_weiss.svg", "SWIPAY-Logo-invers.png"):
        (tmp_path / name).write_bytes(b"x")
        assert reporter._negative_logo() is not None, name
        (tmp_path / name).unlink()

    # Positiv-Logo allein darf NICHT als Negativ durchgehen.
    (tmp_path / "SWIPAY-Logo.svg").write_bytes(b"x")
    assert reporter._negative_logo() is None


def test_negative_logo_lookup_prefers_svg_over_png(monkeypatch, tmp_path):
    import reporter
    monkeypatch.setattr(reporter, "_ASSETS", tmp_path)
    (tmp_path / "SWIPAY-Logo-negativ.png").write_bytes(b"x")
    (tmp_path / "SWIPAY-Logo-negativ.svg").write_bytes(b"x")
    assert reporter._negative_logo().suffix == ".svg"


def test_renders_when_no_negative_logo_is_available(monkeypatch, tmp_path):
    """Ohne Negativ-Logo faellt der Header auf eine weisse Wortmarke als Type
    zurueck -- Satz, kein umgefaerbtes Logo."""
    import reporter
    monkeypatch.setattr(reporter, "_ASSETS", tmp_path)
    monkeypatch.setattr(reporter, "_FONTS", tmp_path / "fonts")
    v = build_view(_agg(), _T, _D, BASIS_IST)
    assert build_pdf(view=v, **_META)[:4] == b"%PDF"
