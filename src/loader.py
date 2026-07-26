"""
Daten-Loader: Worldline-Export (CSV oder XLSB) auf das normalisierte Schema
bringen, das die Engine erwartet. Enthaelt Datenvertrag, Brand-Normalisierung,
Idempotenz-Schluessel und Fan-out-Erkennung.
"""

from __future__ import annotations
import hashlib
from pathlib import Path

import pandas as pd

# Normalisiertes Schema, das pipeline.run_comparison() erwartet.
NORMALIZED_COLUMNS = [
    "partner_id", "partner_name", "vertragsnummer", "vertrag",
    "datum", "zeit", "terminal_id", "kartennummer", "transaktionstyp",
    "brand", "category", "region", "is_dcc", "is_refund",
    "brutto", "gebuehren", "processing_fee", "scheme_fee", "interchange",
    "dcc_payback",
]

# Worldline-Spalte -> normalisierter Name. ASF = Processing Fee.
COLUMN_MAP = {
    "Partner ID": "partner_id",
    "Partner Name": "partner_name",
    "Vertragsnummer": "vertragsnummer",
    "Vertrag": "vertrag",
    "Datum": "datum",
    "Zeit": "zeit",
    "Terminal ID": "terminal_id",
    "Kartennummer": "kartennummer",
    "Transaktionstyp": "transaktionstyp",
    "Brand": "brand",
    "Karten Kategorie": "category",
    "Clearing Region": "region",
    "DCC": "dcc_raw",
    "Bruttobetrag": "brutto",
    "Gebühren": "gebuehren",
    "Processing Fee": "processing_fee",
    "Scheme Fee": "scheme_fee",
    "Interchange": "interchange",
    "DCC Payback": "dcc_payback",
}

# Brand-Namen aus Jahresdaten -> Schreibweise der Transaktionsdaten.
BRAND_ALIASES = {
    "DebitMasterCard": "Debit Mastercard",
    "MasterCard": "Mastercard",
    "China Union Pay": "Union Pay",
}

# Worte, die eine Gutschrift/Stornierung kennzeichnen.
REFUND_MARKERS = ("gutschrift", "refund", "rueck", "rück", "storno")

NUMERIC = ["brutto", "gebuehren", "processing_fee", "scheme_fee",
           "interchange", "dcc_payback"]


def normalize_brand(name: str) -> str:
    return BRAND_ALIASES.get(str(name).strip(), str(name).strip())


def load_worldline(path: str, sheet: str | None = None) -> pd.DataFrame:
    """CSV oder XLSB einlesen und auf NORMALIZED_COLUMNS bringen."""
    if path.lower().endswith((".xlsb", ".xlsx", ".xls")):
        raw = pd.read_excel(path, sheet_name=sheet or 0, engine="pyxlsb"
                            if path.lower().endswith(".xlsb") else None)
    else:
        raw = pd.read_csv(path, sep=";", encoding="utf-8-sig", decimal=".")

    df = raw.rename(columns=COLUMN_MAP)

    # Datenvertrag pruefen, BEVOR mit dem DataFrame weitergearbeitet wird.
    # Ohne diesen Check wuerden fehlende Spalten (z.B. andere Export-Variante
    # ohne ICF/CSF-Aufschluesselung) still in `keep` unten verschwinden und
    # erst viel spaeter in pipeline.run_comparison() als rohen KeyError
    # auffallen, weit weg von der eigentlichen Ursache.
    required = [c for c in NORMALIZED_COLUMNS if c not in ("is_dcc", "is_refund")]
    missing = [c for c in required if c not in df.columns]
    if missing:
        raise ValueError(
            f"«{Path(path).name}» hat nach dem Spalten-Mapping keine Spalte(n) "
            f"{', '.join(missing)}. Vermutlich ein anderes Export-Format "
            "(z.B. ohne Scheme Fee/Interchange-Aufschluesselung) — fuer den "
            "Vergleich wird der vollstaendige Worldline-Export benoetigt."
        )

    for c in NUMERIC:
        if c in df:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    df["brand"] = df["brand"].map(normalize_brand)
    df["is_dcc"] = df.get("dcc_raw", "nein").astype(str).str.lower().eq("ja")
    ttype = df["transaktionstyp"].astype(str).str.lower()
    df["is_refund"] = ttype.str.contains("|".join(REFUND_MARKERS), regex=True) \
        | (df["brutto"] < 0)

    keep = [c for c in NORMALIZED_COLUMNS if c in df]
    return df[keep].copy()


def idempotency_key(row: pd.Series) -> str:
    """Zusammengesetzter, kollisionsfreier Schluessel je Transaktion."""
    parts = [str(row.get(k, "")) for k in
             ("datum", "zeit", "terminal_id", "brutto",
              "kartennummer", "transaktionstyp")]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def add_keys(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["idempotency_key"] = df.apply(idempotency_key, axis=1)
    return df


def detect_fanout(year_data: pd.DataFrame,
                  pid_col: str = "partner_id",
                  kdnr_col: str = "vertragsnummer",
                  umsatz_col: str = "umsatz") -> list:
    """Partner-IDs mit mehreren Vertragsnummern, aber identischem Jahresumsatz.

    Verdacht auf Fan-out (Quelle hat denselben Wert mehrfach gestempelt).
    Default-Empfehlung: nicht summieren, manuell aufloesen.
    """
    g = year_data.groupby(pid_col).agg(
        n_kdnr=(kdnr_col, "nunique"),
        n_distinct=(umsatz_col, lambda x: x.round(0).nunique()),
    )
    suspects = g[(g["n_kdnr"] > 1) & (g["n_distinct"] == 1)]
    return list(suspects.index)
