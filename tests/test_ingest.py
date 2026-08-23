"""Tests for the Phase-2 integrity layer (ingest.py)."""

from pathlib import Path

import pandas as pd
import pytest

from ingest import IngestReport, ingest_files

# ---------------------------------------------------------------------------
# Test-CSV helpers
# ---------------------------------------------------------------------------

# Matches the Worldline column headers that loader.COLUMN_MAP expects.
_COLS = [
    "Partner ID", "Partner Name", "Vertragsnummer", "Vertrag",
    "Datum", "Zeit", "Terminal ID", "Kartennummer", "Transaktionstyp",
    "Brand", "Karten Kategorie", "Clearing Region", "DCC",
    "Bruttobetrag", "Gebühren", "Processing Fee", "Scheme Fee",
    "Interchange", "DCC Payback",
]


def _row(i: int, brutto: float = 10.0, partner_id: str = "P1",
         vertr: str = "V1") -> list:
    """Build one minimal Worldline-style row. `i` makes the key unique."""
    return [
        partner_id, "Test AG", vertr, "Testvertrag",
        "2024-01-01", f"10:00:{i:02d}", f"T{i:03d}",
        f"****{i:04d}", "Kauf",
        "Visa", "Credit", "Domestic", "nein",
        brutto, -0.10, -0.05, -0.02, -0.03, 0.0,
    ]


def _write_csv(path: str, rows: list[list]) -> None:
    pd.DataFrame(rows, columns=_COLS).to_csv(
        path, sep=";", index=False, encoding="utf-8-sig", decimal="."
    )


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def db(tmp_path) -> str:
    return str(tmp_path / "test.db")


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_same_file_twice_adds_zero_rows(db, tmp_path):
    """Re-uploading an identical file must add 0 rows and report a level-2 block."""
    csv = str(tmp_path / "export.csv")
    _write_csv(csv, [_row(i) for i in range(5)])

    _, r1 = ingest_files([csv], db)
    assert r1.rows_new == 5
    assert r1.files_blocked_hash == []

    df2, r2 = ingest_files([csv], db)
    assert r2.rows_new == 0
    assert Path(csv).name in r2.files_blocked_hash


def test_partial_overlap_only_new_rows(db, tmp_path):
    """Two files sharing some rows: only the novel rows are accepted on second ingest."""
    csv_a = str(tmp_path / "period_jan_mar.csv")
    csv_b = str(tmp_path / "period_feb_apr.csv")
    # rows 0-3 in file A, rows 2-5 in file B → rows 2-3 overlap
    _write_csv(csv_a, [_row(i) for i in range(4)])
    _write_csv(csv_b, [_row(i) for i in range(2, 6)])

    _, r1 = ingest_files([csv_a], db)
    assert r1.rows_new == 4

    df2, r2 = ingest_files([csv_b], db)
    assert r2.rows_new == 2             # only rows 4 and 5 are genuinely new
    assert r2.rows_skipped_overlap == 2  # rows 2 and 3 were already ingested


def test_fanout_partner_flagged(db, tmp_path):
    """Partner with two contracts but identical total volume must be flagged for review."""
    csv = str(tmp_path / "fanout.csv")
    _write_csv(csv, [
        _row(0, brutto=100.0, partner_id="P1", vertr="V1"),
        _row(1, brutto=100.0, partner_id="P1", vertr="V2"),  # same total -> fanout
        _row(2, brutto=50.0,  partner_id="P2", vertr="V3"),  # single contract, clean
    ])

    _, report = ingest_files([csv], db)
    assert "P1" in report.fanout_partner_ids
    assert "P2" not in report.fanout_partner_ids


def test_a_resaved_export_returns_its_rows_instead_of_an_empty_frame(db, tmp_path):
    """New file bytes, identical rows: Excel rewrites metadata on save, so the
    content hash changes while every row stays the same. The level-3 backstop
    correctly drops all rows as known -- but handing back an empty frame reads
    as "no data loaded" and empties the screen. Return the rows for display,
    like the hash-blocked path, and do not register the file."""
    import sqlite3

    csv = str(tmp_path / "export.csv")
    _write_csv(csv, [_row(i) for i in range(5)])
    df1, r1 = ingest_files([csv], db)
    assert r1.rows_new == 5

    # Same rows, different bytes.
    resaved = tmp_path / "export_resaved.csv"
    resaved.write_text(Path(csv).read_text(encoding="utf-8-sig") + "\n",
                       encoding="utf-8-sig")
    df2, r2 = ingest_files([str(resaved)], db)

    assert r2.rows_new == 0
    assert r2.files_blocked_hash == []              # different bytes, not blocked
    assert r2.files_known_rows == ["export_resaved.csv"]
    assert r2.files_processed == []                 # never registered
    assert len(df2) == len(df1) == 5                # rows returned for display

    with sqlite3.connect(db) as con:
        # No processed_files row claiming an ingest that did not happen.
        assert con.execute("SELECT COUNT(*) FROM processed_files").fetchone()[0] == 1
