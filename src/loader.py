"""
Daten-Loader: Worldline- oder SBB-Export (CSV/XLSB/XLSX) auf das normalisierte
Schema bringen, das die Engine erwartet. Enthaelt Format-Erkennung,
Datenvertrag, Brand-Normalisierung, Idempotenz-Schluessel und
Fan-out-Erkennung.
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

# ---------------------------------------------------------------------------
# SBB IC++ (SAP-Export, siehe CLAUDE.md "SBB IC++ Import")
# ---------------------------------------------------------------------------

# Kartenprodukte ohne echte IC++-Bepreisung (kein Interchange/Scheme-Fee-
# Splitting, ICF++:Abgerechneter Bruttobetrag ist fuer sie NICHT der wahre
# Bruttobetrag -- bei TWINT weicht er sogar leicht vom richtigen Wert ab).
# Fuer diese Zeilen -- und fuer die vereinzelte Zeile ganz ohne Kartenprodukt
# -- gilt "Betrag der Verbrauchsposition" als Bruttobetrag, sonst
# "ICF++:Abgerechneter Bruttobetrag" (siehe load_sbb).
SBB_NO_ICF_BRANDS = {"TWINT", "Postcard", "Reka-Pay", "Reka Rail"}


def normalize_brand(name: str) -> str:
    return BRAND_ALIASES.get(str(name).strip(), str(name).strip())


# ---------------------------------------------------------------------------
# Format-Erkennung
# ---------------------------------------------------------------------------

# Je Format eine kleine Spalten-Signatur -- reicht, um Worldline von SBB zu
# unterscheiden, ohne die Datei zweimal vollstaendig einzulesen.
_WL_SIGNATURE = {"Partner ID", "Gebühren"}
_SBB_SIGNATURE = {"SBB Merchant ID", "Kommission"}
_SBB_SHEET = "SAP Document Export"


def _peek_columns(path: str, sheet: str | None) -> tuple[set[str], list[str] | None]:
    """Nur die Kopfzeile lesen (nrows=0) -- billig, auch bei grossen Exporten."""
    low = path.lower()
    if low.endswith(".xlsb"):
        cols = pd.read_excel(path, sheet_name=sheet or 0, engine="pyxlsb", nrows=0).columns
        return set(cols), None
    if low.endswith((".xlsx", ".xls")):
        xls = pd.ExcelFile(path)
        sheet_names = xls.sheet_names
        pick = _SBB_SHEET if _SBB_SHEET in sheet_names else (sheet or 0)
        cols = pd.read_excel(xls, sheet_name=pick, nrows=0).columns
        return set(cols), sheet_names
    cols = pd.read_csv(path, sep=";", encoding="utf-8-sig", nrows=0).columns
    return set(cols), None


def detect_format(path: str, sheet: str | None = None) -> str:
    """"worldline" oder "sbb", anhand der Kopfzeile/Sheet-Struktur -- nie an der
    Dateiendung allein, die trennt heute zufaellig, ist aber kein Vertrag."""
    cols, sheet_names = _peek_columns(path, sheet)
    if (sheet_names and _SBB_SHEET in sheet_names) or _SBB_SIGNATURE.issubset(cols):
        return "sbb"
    if _WL_SIGNATURE.issubset(cols):
        return "worldline"
    raise ValueError(
        f"«{Path(path).name}»: Format nicht erkannt (weder Worldline- noch "
        "SBB-Spalten in der Kopfzeile gefunden)."
    )


def load_export(path: str, sheet: str | None = None) -> tuple[pd.DataFrame, str]:
    """Laden + Format-Erkennung in einem Schritt. Gibt (df, format) zurueck,
    format ist "worldline" oder "sbb" -- fuers Anzeigen im Upload-Bericht."""
    fmt = detect_format(path, sheet=sheet)
    if fmt == "sbb":
        return load_sbb(path), fmt
    return load_worldline(path, sheet=sheet), fmt


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


def load_sbb(path: str) -> pd.DataFrame:
    """SBB-IC++-Export (SAP, XLSX, Sheet "SAP Document Export") auf
    NORMALIZED_COLUMNS bringen. Mapping-Entscheide siehe CLAUDE.md
    "SBB IC++ Import" -- hier nur die Kurzfassung je Zeile."""
    raw = pd.read_excel(path, sheet_name=_SBB_SHEET, engine="openpyxl")

    df = pd.DataFrame(index=raw.index)
    df["partner_id"] = raw["SBB Merchant ID"].astype(str)
    df["partner_name"] = raw["Standort"]
    # Kein eigenes Vertragskonzept bei SBB -- Merchant ID ist bereits die
    # atomare Gruppierungsebene, Fan-out-Erkennung greift hier bewusst nicht.
    df["vertragsnummer"] = df["partner_id"]
    df["vertrag"] = raw["Acquirer"]  # informativ: SIX / PostFinance / Reka
    df["datum"] = raw["Transaktionsdatum"]
    df["zeit"] = raw["Transaktionszeit"]
    df["terminal_id"] = raw["Terminal"]
    df["kartennummer"] = raw["Kartennumer"]
    df["transaktionstyp"] = raw["Transaktionstyp"]

    # Akzeptanzprodukt unterscheidet Debit/Credit Mastercard und fasst
    # Maestro CH/International bereits zu "Maestro" zusammen (Nutzer-
    # Entscheid); nur fuer PostFinance/Reka-Zeilen (kein IC++) leer --
    # dort auf die groebere SAP-Spalte zurueckfallen.
    brand = raw["ICF++:Akzeptanzprodukt"]
    brand = brand.where(brand.notna(), raw["Kartenprodukt"])

    # Anders als bei Mastercard meldet Akzeptanzprodukt fuer Visa IMMER nur
    # "Visa", nie "Visa Debit" -- Credit- und Debitkarten sind sonst nicht zu
    # unterscheiden (68% der "Visa"-Zeilen im Testexport sind tatsaechlich
    # Debit). Card Accepted Funding Source traegt die Unterscheidung separat.
    # Nur "Debit" loest Visa Debit aus; Credit/Prepaid/Deferred/NaN bleiben
    # Visa (Nutzer-Entscheid, spiegelt wie Akzeptanzprodukt Mastercard-Prepaid
    # bereits selbst unter "Mastercard" statt "Debit Mastercard" fuehrt).
    # V PAY ist unbetroffen -- Akzeptanzprodukt meldet es bereits eindeutig,
    # auch wenn Kartenprodukt fälschlich "Visa" zeigt (4 Zeilen im Testexport).
    funding = raw["ICF++:Card Accepted Funding Source"]
    is_visa_debit = brand.eq("Visa") & funding.eq("Debit")
    brand = brand.where(~is_visa_debit, "Visa Debit")

    df["brand"] = brand.map(normalize_brand)
    df["category"] = raw["ICF++:Kartentypengruppe"]

    # DCC-Potenzial (Fremdwaehrungskarte) vs. genutzt sind zwei verschiedene
    # Kennzahlen (siehe projection.volume_bases): Clearing Region traegt das
    # Potenzial, DCC Chosen die tatsaechliche Nutzung. TWINT/Postcard/Reka
    # gibt es nur in CH -> fehlende Clearing Region zaehlt als Domestic,
    # nicht als "unbekannt".
    region = raw["ICF++:Clearing Region"]
    df["region"] = region.where(region.notna(), "Domestic")
    df["is_dcc"] = raw["DCC Chosen"].fillna(0).astype(int).eq(1)

    # Bruttobetrag: fuer echte IC++-Kartenzahlungen ist "Betrag der
    # Verbrauchsposition" bei DCC der aufgewertete Fremdwaehrungsbetrag, den
    # der Karteninhaber sieht -- NICHT der Abrechnungsbetrag. Fuer TWINT/
    # Postcard/Reka (kein IC++-Splitting) ist umgekehrt "ICF++:Abgerechneter
    # Bruttobetrag" unzuverlaessig (bei TWINT z.B. leicht falsch). Verifiziert
    # gegen Nettobetrag = Brutto + Kommission + DCC Ertrag: Residuum 0 auf
    # dem SBB-Testexport (23'213 Zeilen) mit dieser Fallunterscheidung.
    kartenprodukt = raw["Kartenprodukt"]
    no_icf = kartenprodukt.isna() | kartenprodukt.isin(SBB_NO_ICF_BRANDS)
    df["brutto"] = raw["Betrag der Verbrauchsposition"].where(
        no_icf, raw["ICF++:Abgerechneter Bruttobetrag"]
    )

    # Gebuehren-Basis roh aus "Kommission" (SAP-generisch, IMMER befuellt) --
    # nicht aus den ICF++-Komponenten rekonstruiert, analog zur
    # Worldline-Regel (dort "Gebühren"). "ICF++:Kommission gesamt" ist bei
    # TWINT/Postcard/Reka 0 und bei vereinzelten Zeilen sogar bei echten
    # Kartenzahlungen unsynchronisiert (Datenfehler im Export).
    df["gebuehren"] = raw["Kommission"]
    df["processing_fee"] = raw["ICF++:AcquirerServiceFee"]
    df["scheme_fee"] = raw["ICF++:CardSchemeFee"]
    df["interchange"] = raw["ICF++:InterChangeFEE"]
    df["dcc_payback"] = raw["DCC Ertrag"]

    for c in NUMERIC:
        if c in df:
            df[c] = pd.to_numeric(df[c], errors="coerce")

    ttype = df["transaktionstyp"].astype(str).str.lower()
    df["is_refund"] = ttype.str.contains("credit") | (df["brutto"] < 0)

    # SAP liefert pro Zeile eine garantiert eindeutige ID -- zuverlaessiger
    # als der aus Datum/Zeit/Terminal/Betrag zusammengesetzte Worldline-
    # Schluessel (siehe add_keys/idempotency_key).
    df["source_row_id"] = raw["ID der Verbrauchsposition"].astype(str)

    keep = [c for c in NORMALIZED_COLUMNS if c in df] + ["source_row_id"]
    return df[keep].copy()


def idempotency_key(row: pd.Series) -> str:
    """Zusammengesetzter, kollisionsfreier Schluessel je Transaktion.

    Traegt die Quelle einen eigenen, garantiert eindeutigen Zeilen-Schluessel
    (source_row_id, z.B. SBBs "ID der Verbrauchsposition"), wird DER gehasht
    -- zuverlaessiger als die aus Datum/Zeit/Terminal/Betrag zusammengesetzte
    Notloesung, die fuer Worldline (kein solcher Schluessel im Export) bleibt.
    """
    natural_id = row.get("source_row_id")
    if natural_id and str(natural_id).strip() and str(natural_id).lower() != "nan":
        return hashlib.sha256(str(natural_id).encode()).hexdigest()
    parts = [str(row.get(k, "")) for k in
             ("datum", "zeit", "terminal_id", "brutto",
              "kartennummer", "transaktionstyp")]
    return hashlib.sha256("|".join(parts).encode()).hexdigest()


def add_keys(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    df["idempotency_key"] = df.apply(idempotency_key, axis=1)
    return df.drop(columns=["source_row_id"], errors="ignore")


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
