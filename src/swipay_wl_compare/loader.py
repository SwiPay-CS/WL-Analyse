"""Worldline transaction export loader (XLSB and CSV formats)."""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)

_NUMERIC_COLS = [
    "Bruttobetrag",
    "Gebühren",
    "Processing Fee",
    "Scheme Fee",
    "Interchange",
    "DCC Payback",
]


def load_xlsb(path: Path) -> pd.DataFrame:
    """Load a Worldline XLSB export from the 'WL' sheet."""
    logger.info("Loading XLSB: %s", path.name)
    df = pd.read_excel(path, sheet_name="WL", engine="pyxlsb", header=0)
    for col in _NUMERIC_COLS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    logger.info("Loaded %d rows", len(df))
    return df


def load_csv(path: Path) -> pd.DataFrame:
    """Load a Worldline CSV export (auto-detects separator; comma decimal)."""
    logger.info("Loading CSV: %s", path.name)
    df = pd.read_csv(path, sep=None, engine="python", decimal=",", thousands="'")
    for col in _NUMERIC_COLS:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    logger.info("Loaded %d rows", len(df))
    return df


def load_file(path: Path) -> pd.DataFrame:
    """Dispatch to the correct loader based on file extension."""
    ext = path.suffix.lower()
    if ext == ".xlsb":
        return load_xlsb(path)
    if ext == ".csv":
        return load_csv(path)
    raise ValueError(f"Unsupported file type: {ext!r} — expected .xlsb or .csv")
