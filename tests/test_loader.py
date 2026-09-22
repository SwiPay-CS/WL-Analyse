"""Tests for format detection and the SBB IC++ loader (see loader.py,
CLAUDE.md "SBB IC++ Import" for the mapping decisions behind these cases)."""

import pandas as pd
import pytest

from loader import add_keys, detect_format, load_sbb, load_export

# ---------------------------------------------------------------------------
# SBB test-file helpers
# ---------------------------------------------------------------------------

_SBB_COLS = [
    "SBB Merchant ID", "Standort", "Acquirer",
    "Transaktionsdatum", "Transaktionszeit", "Terminal", "Kartennumer",
    "Transaktionstyp", "ICF++:Akzeptanzprodukt", "Kartenprodukt",
    "ICF++:Kartentypengruppe", "ICF++:Clearing Region", "DCC Chosen",
    "Betrag der Verbrauchsposition", "ICF++:Abgerechneter Bruttobetrag",
    "Kommission", "ICF++:AcquirerServiceFee", "ICF++:CardSchemeFee",
    "ICF++:InterChangeFEE", "DCC Ertrag", "ID der Verbrauchsposition",
]


def _sbb_row(row_id: str, **overrides) -> dict:
    base = dict(zip(_SBB_COLS, [
        "SBB212200100050", "TEST STANDORT", "SIX Group Services AG",
        pd.Timestamp("2026-01-01"), "10:00:00", 12345678, "123456XXXXXX1234",
        "00 Goods", "Visa", "Visa",
        "Consumer credit card", "Domestic", 0,
        10.0, 10.0,
        -0.10, -0.02, -0.01, -0.07, 0.0, row_id,
    ]))
    base.update(overrides)
    return base


def _write_sbb_xlsx(path, rows: list[dict]) -> None:
    df = pd.DataFrame(rows, columns=_SBB_COLS)
    with pd.ExcelWriter(path, engine="openpyxl") as xw:
        df.to_excel(xw, sheet_name="SAP Document Export", index=False)
        pd.DataFrame({"foo": [1]}).to_excel(xw, sheet_name="Tabelle1", index=False)


_WL_COLS = [
    "Partner ID", "Partner Name", "Vertragsnummer", "Vertrag",
    "Datum", "Zeit", "Terminal ID", "Kartennummer", "Transaktionstyp",
    "Brand", "Karten Kategorie", "Clearing Region", "DCC",
    "Bruttobetrag", "Gebühren", "Processing Fee", "Scheme Fee",
    "Interchange", "DCC Payback",
]


def _write_wl_csv(path) -> None:
    row = ["P1", "Test AG", "V1", "Testvertrag", "01.01.2026", "10:00:00",
           "T1", "1234", "Kauf", "Visa", "Credit", "Domestic", "nein",
           10.0, -0.10, -0.05, -0.02, -0.03, 0.0]
    pd.DataFrame([row], columns=_WL_COLS).to_csv(
        path, sep=";", index=False, encoding="utf-8-sig"
    )


# ---------------------------------------------------------------------------
# Format detection
# ---------------------------------------------------------------------------

def test_detect_format_sbb(tmp_path):
    p = tmp_path / "sbb.xlsx"
    _write_sbb_xlsx(p, [_sbb_row("R1")])
    assert detect_format(str(p)) == "sbb"


def test_detect_format_worldline(tmp_path):
    p = tmp_path / "wl.csv"
    _write_wl_csv(p)
    assert detect_format(str(p)) == "worldline"


def test_detect_format_unknown_raises(tmp_path):
    p = tmp_path / "unknown.csv"
    pd.DataFrame({"Foo": [1], "Bar": [2]}).to_csv(p, sep=";", index=False)
    with pytest.raises(ValueError, match="nicht erkannt"):
        detect_format(str(p))


def test_load_export_dispatches_to_sbb(tmp_path):
    p = tmp_path / "sbb.xlsx"
    _write_sbb_xlsx(p, [_sbb_row("R1")])
    df, fmt = load_export(str(p))
    assert fmt == "sbb"
    assert len(df) == 1


# ---------------------------------------------------------------------------
# load_sbb mapping
# ---------------------------------------------------------------------------

def test_card_row_uses_icf_brutto_not_display_amount(tmp_path):
    """DCC-chosen card row: 'Betrag der Verbrauchsposition' is the inflated
    foreign-currency display amount, not the settlement gross -- brutto must
    come from ICF++:Abgerechneter Bruttobetrag instead."""
    p = tmp_path / "sbb.xlsx"
    _write_sbb_xlsx(p, [_sbb_row(
        "R1", **{
            "DCC Chosen": 1,
            "Betrag der Verbrauchsposition": 14.66,   # display amount (EUR-ish)
            "ICF++:Abgerechneter Bruttobetrag": 13.0,  # true CHF settlement
            "Kommission": -0.35,
            "DCC Ertrag": 0.39,
        }
    )])
    df = load_sbb(str(p))
    assert df.loc[0, "brutto"] == pytest.approx(13.0)
    assert df.loc[0, "is_dcc"] == True  # noqa: E712
    # Net identity: brutto + gebuehren + dcc_payback == Nettobetrag (13.04).
    assert df.loc[0, "brutto"] + df.loc[0, "gebuehren"] + df.loc[0, "dcc_payback"] \
        == pytest.approx(13.04)


def test_twint_row_uses_generic_gross_field(tmp_path):
    """TWINT has no real IC++ pricing -- ICF++:Abgerechneter Bruttobetrag is
    unreliable for it (can be slightly off), so brutto must fall back to
    'Betrag der Verbrauchsposition'."""
    p = tmp_path / "sbb.xlsx"
    _write_sbb_xlsx(p, [_sbb_row(
        "R1", **{
            "ICF++:Akzeptanzprodukt": "Twint",
            "Kartenprodukt": "TWINT",
            "Betrag der Verbrauchsposition": 6.00,
            "ICF++:Abgerechneter Bruttobetrag": 5.96,  # wrong for TWINT
            "Kommission": -0.04,
            "ICF++:Kartentypengruppe": "Unspecified",
        }
    )])
    df = load_sbb(str(p))
    assert df.loc[0, "brutto"] == pytest.approx(6.00)
    assert df.loc[0, "brand"] == "Twint"


def test_postfinance_row_no_icf_block_and_domestic_fallback(tmp_path):
    """PostFinance/Reka rows carry no ICF++ breakdown at all (all zero) and no
    Clearing Region -- brand falls back to Kartenprodukt, brutto to the
    generic gross field, and region defaults to Domestic (never 'unknown')."""
    p = tmp_path / "sbb.xlsx"
    _write_sbb_xlsx(p, [_sbb_row(
        "R1", **{
            "Acquirer": "PostFinance AG",
            "ICF++:Akzeptanzprodukt": None,
            "Kartenprodukt": "Postcard",
            "ICF++:Kartentypengruppe": None,
            "ICF++:Clearing Region": None,
            "Betrag der Verbrauchsposition": 15.0,
            "ICF++:Abgerechneter Bruttobetrag": 0.0,
            "Kommission": -0.14,
            "ICF++:AcquirerServiceFee": 0.0,
            "ICF++:CardSchemeFee": 0.0,
            "ICF++:InterChangeFEE": 0.0,
        }
    )])
    df = load_sbb(str(p))
    assert df.loc[0, "brand"] == "Postcard"
    assert df.loc[0, "brutto"] == pytest.approx(15.0)
    assert df.loc[0, "region"] == "Domestic"


def test_refund_row_detected(tmp_path):
    p = tmp_path / "sbb.xlsx"
    _write_sbb_xlsx(p, [_sbb_row(
        "R1", **{
            "Transaktionstyp": "20 Goods (Credit)",
            "Betrag der Verbrauchsposition": -56.0,
            "ICF++:Abgerechneter Bruttobetrag": -56.0,
            "Kommission": -0.27,
        }
    )])
    df = load_sbb(str(p))
    assert bool(df.loc[0, "is_refund"]) is True


def test_missing_brand_row_is_unmapped_not_crashed(tmp_path):
    p = tmp_path / "sbb.xlsx"
    _write_sbb_xlsx(p, [_sbb_row(
        "R1", **{"ICF++:Akzeptanzprodukt": None, "Kartenprodukt": None}
    )])
    df = load_sbb(str(p))
    assert df.loc[0, "brand"] == "nan"


def test_idempotency_uses_natural_row_id(tmp_path):
    """Two rows identical on every mapped field except SBB's own unique ID
    must still get distinct idempotency keys (SAP ID is the source of truth,
    not the date/time/terminal/amount composite used for Worldline)."""
    p = tmp_path / "sbb.xlsx"
    _write_sbb_xlsx(p, [_sbb_row("R1"), _sbb_row("R2")])
    df = load_sbb(str(p))
    df = add_keys(df)
    assert df["idempotency_key"].nunique() == 2
    assert "source_row_id" not in df.columns
