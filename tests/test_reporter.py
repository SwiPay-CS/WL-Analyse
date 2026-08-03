"""Tests for reporter.py's build_pdf: focused on the Hochrechnung-related
extensions (Portfolio-Abdeckung, Fan-out-Hinweis bei doppelten Jahresumsatz-
Werten) added alongside aggregation.py -- not a full PDF content assertion,
just "it renders without crashing and returns bytes"."""

from projection import CoverageLabel, CoverageTier
from aggregation import AggregateProjection
from reporter import build_pdf

_KWARGS = dict(
    partner_name="Test AG", period_from="2024-01", period_to="2024-12",
    brutto=10_000.0, n_txn=100, n_terminals=2, avg_ticket=100.0,
    wl_net=500.0, sp_net=400.0, wl_cashback=10.0, sp_cashback=15.0,
    saving=100.0, dcc_advantage=5.0, dcc_pct=0.014,
    generated_date="01.01.2026",
)


def _agg(label: CoverageLabel) -> AggregateProjection:
    return AggregateProjection(
        wl_net_annual=5_000.0, sp_net_annual=4_000.0, saving_annual=1_000.0,
        dcc_advantage_annual=50.0, n_txn_annual=1_000.0,
        band_low=850.0, band_high=1_150.0,
        coverage=CoverageTier(label, 0.4), annual_volume_total=25_000.0,
        portfolio_coverage_pct=0.6, used=["A", "B"], skipped=["C"],
        duplicate_groups=[["A", "B"]],
    )


def test_build_pdf_without_projection_returns_bytes():
    pdf = build_pdf(**_KWARGS)
    assert isinstance(pdf, bytes)
    assert pdf[:4] == b"%PDF"


def test_build_pdf_with_aggregate_projection_and_portfolio_coverage():
    agg = _agg(CoverageLabel.LOW_COVERAGE)
    pdf = build_pdf(
        **_KWARGS,
        projection=agg, annual_volume=agg.annual_volume_total,
        portfolio_coverage_pct=agg.portfolio_coverage_pct,
        n_entities_used=len(agg.used), n_entities_total=len(agg.used) + len(agg.skipped),
        duplicate_volume_warnings=agg.duplicate_groups,
    )
    assert isinstance(pdf, bytes)
    assert pdf[:4] == b"%PDF"


def test_build_pdf_with_indicative_coverage_and_no_duplicates():
    agg = _agg(CoverageLabel.INDICATIVE)
    agg.duplicate_groups = []
    pdf = build_pdf(
        **_KWARGS,
        projection=agg, annual_volume=agg.annual_volume_total,
        portfolio_coverage_pct=agg.portfolio_coverage_pct,
        n_entities_used=len(agg.used), n_entities_total=len(agg.used) + len(agg.skipped),
        duplicate_volume_warnings=agg.duplicate_groups,
    )
    assert isinstance(pdf, bytes)
    assert pdf[:4] == b"%PDF"
