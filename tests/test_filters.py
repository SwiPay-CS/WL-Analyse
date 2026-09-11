"""Tests for filters.py — the Transaktionen page's filter semantics.

The point of this layer is that an empty selection means "Alle". Getting that
backwards would either empty the page or silently ignore a chosen filter.
"""

import pandas as pd
import pytest

import filters


def _df() -> pd.DataFrame:
    return pd.DataFrame({
        "partner_name": ["Feusi Optik AG", "Feusi Optik AG", "Hotel Ochsen", "Hotel Ochsen"],
        "terminal_id":  ["31551001", "31551002", "40010001", "40010001"],
        "vertrag":      ["DCC Face to Face", "DCC Face to Face", "Face to Face", "Face to Face"],
        "region":       ["DOMESTIC", "WESTERN", "DOMESTIC", "INTER_REGION"],
        "brand":        ["Visa", "Mastercard", "Visa", "TWINT"],
        "category":     ["Credit", "Credit", "Debit", "Unknown"],
        "_month":       ["2025-08", "2025-12", "2026-01", "2026-07"],
        "brutto":       [10.0, 20.0, 30.0, 40.0],
    })


# ── Auswahl-Semantik ────────────────────────────────────────────────────────

def test_no_selection_keeps_every_row():
    df = _df()
    assert filters.apply_filters(df, {}).all()
    assert filters.apply_filters(df, None).all()
    # An explicitly empty list is "Alle" too, not "nichts".
    assert filters.apply_filters(df, {"brand": []}).all()


def test_several_values_in_one_field_are_or():
    df = _df()
    m = filters.apply_filters(df, {"brand": ["Visa", "TWINT"]})
    assert df[m]["brand"].tolist() == ["Visa", "Visa", "TWINT"]


def test_several_fields_are_and():
    df = _df()
    m = filters.apply_filters(df, {"brand": ["Visa"], "region": ["DOMESTIC"]})
    assert m.sum() == 2
    m2 = filters.apply_filters(df, {"brand": ["Visa"], "region": ["INTER_REGION"]})
    assert m2.sum() == 0


def test_vertriebsweg_filters_on_the_vertrag_column():
    # Etrax calls it Vertriebsweg, Worldline stores it as `vertrag`.
    df = _df()
    m = filters.apply_filters(df, {"vertrieb": ["Face to Face"]})
    assert df[m]["partner_name"].unique().tolist() == ["Hotel Ochsen"]


def test_selection_tolerates_surrounding_whitespace():
    df = _df()
    df.loc[0, "brand"] = "  Visa  "
    assert filters.apply_filters(df, {"brand": ["Visa"]}).sum() == 2


def test_a_missing_column_is_skipped_not_raised():
    # Not every Worldline export carries every column.
    df = _df().drop(columns=["terminal_id"])
    m = filters.apply_filters(df, {"terminal": ["31551001"], "brand": ["Visa"]})
    assert m.sum() == 2          # the terminal filter is simply absent


def test_empty_frame_returns_an_all_true_mask():
    m = filters.apply_filters(pd.DataFrame(), {"brand": ["Visa"]})
    assert m.empty or m.all()


# ── Zeitraum ────────────────────────────────────────────────────────────────

def test_period_is_inclusive_on_both_ends():
    df = _df()
    m = filters.apply_filters(df, {}, "2025-12", "2026-01")
    assert df[m]["_month"].tolist() == ["2025-12", "2026-01"]


def test_period_combines_with_a_field_filter():
    df = _df()
    m = filters.apply_filters(df, {"brand": ["Visa"]}, "2026-01", "2026-07")
    assert m.sum() == 1


def test_period_needs_both_ends():
    df = _df()
    assert filters.apply_filters(df, {}, "2026-01", None).all()
    assert filters.apply_filters(df, {}, None, "2026-01").all()


# ── Optionen und Jahres-Schnellwahl ─────────────────────────────────────────

def test_options_are_sorted_and_drop_nan_leftovers():
    df = _df()
    df.loc[len(df)] = {**df.iloc[0].to_dict(), "brand": "nan"}
    df.loc[len(df)] = {**df.iloc[0].to_dict(), "brand": "   "}
    assert filters.options_for(df, "brand") == ["Mastercard", "TWINT", "Visa"]
    assert filters.options_for(df, "gibt_es_nicht") == []
    assert filters.options_for(pd.DataFrame(), "brand") == []


def test_years_are_listed_newest_first():
    assert filters.years_from_months(
        ["2025-08", "2026-01", "2025-12"]) == ["2026", "2025"]
    assert filters.years_from_months([]) == []


def test_year_range_clamps_to_the_months_that_exist():
    months = ["2025-08", "2025-09", "2025-12", "2026-01", "2026-07"]
    # A rolling twelve-month export has no January 2025 -- don't invent one.
    assert filters.year_range("2025", months) == ("2025-08", "2025-12")
    assert filters.year_range("2026", months) == ("2026-01", "2026-07")
    assert filters.year_range("2024", months) is None


def test_year_range_and_apply_filters_agree():
    df = _df()
    months = sorted(df["_month"].tolist())
    frm, to = filters.year_range("2025", months)
    assert filters.apply_filters(df, {}, frm, to).sum() == 2
