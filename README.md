# swipay-wl-compare

Internes Tool zum Vergleich von Worldline-Transaktionsexporten gegen SwiPay-Konditionen (IC++ gegen IC++, Variation auf der ASF).

> **Wichtig:** Dieses Tool verarbeitet echte Zahlungsdaten. Lege Exportdateien ausschliesslich in `data/` ab – dieser Ordner ist durch `.gitignore` vom Repository ausgeschlossen und wird niemals committet.

---

## Voraussetzungen

- [uv](https://docs.astral.sh/uv/) (Versions- und Paketmanager)
- macOS, Linux oder Windows (WSL)

## Installation

```bash
# 1. Repository klonen oder entpacken
cd swipay-wl-compare

# 2. Abhängigkeiten installieren (Python 3.13 wird automatisch geladen)
uv sync
```

Das war's. `uv sync` lädt Python 3.13 (falls noch nicht vorhanden), erstellt die virtuelle Umgebung in `.venv/` und installiert alle Pakete.

## Ausführen

Lege die Worldline-Exportdatei in den `data/`-Ordner und rufe das Tool auf:

```bash
# XLSB-Export
uv run swipay-wl-compare data/MeinExport.xlsb

# CSV-Export
uv run swipay-wl-compare data/MeinExport.csv

# Mit ausführlicherem Logging
uv run swipay-wl-compare data/MeinExport.xlsb --log-level DEBUG
```

### Beispielausgabe

```
Datensatz: 113.456 Transaktionen aus 'MeinExport.xlsb'

1) IDENTITÄT  Gebühren = PF + SF + IC
   geprüft: 113.456 Zeilen | max. Abweichung: 0.00 CHF | Mismatches: 0

2) WORLDLINE IST
   Gebühren:     12.345,67 CHF | DCC-Cashback:    1.234,56 CHF | Netto:     11.111,11 CHF

3) SWIPAY (illustrative Parameter — NICHT die echte Preisliste)
   Gebühren:     11.500,00 CHF | DCC-Cashback:    1.234,56 CHF | Netto:     10.265,44 CHF
   Differenz Netto (Worldline − SwiPay):        845,67 CHF

4) MINDESTGEBÜHR-FLOOR (transaktionsgenau)
   offerierbare Transaktionen: 112.000 | davon gefloored: 3.450 (3.1 %)

5) DCC-REIHENFOLGE  (Floor zuerst, Cashback danach separat)
   DCC-Transaktionen: 8.900 | SwiPay-Cashback total: 1.234,56 CHF | Worldline-Cashback: 1.234,56 CHF
```

## Tests ausführen

```bash
uv run pytest
```

## Projektstruktur

```
src/swipay_wl_compare/
    params.py          # Parametermodell (BrandParams-Tabelle, NON_OFFERABLE)
    engine.py          # Fee-Engine: compute_swipay() + run_engine()
    loader.py          # Datei-Loader für XLSB und CSV
    logging_setup.py   # UTC-Logging mit Correlation-ID und PAN-Maskierung
    cli.py             # Konsolenbefehl swipay-wl-compare
tests/
    test_engine.py     # Unit-Tests (heikle Mechaniken)
data/                  # Exportdateien hier ablegen (gitignored)
```

## Parameter anpassen

Die Gebührenparameter in `src/swipay_wl_compare/params.py` sind **illustrative Beispielwerte**. Ersetze die Einträge in `PARAMS` durch die echte SwiPay-Preisliste, bevor du das Tool produktiv einsetzt.

## Sicherheitshinweise

- Exportdateien enthalten Kartendaten und dürfen ausschliesslich lokal verarbeitet werden.
- Der `data/`-Ordner ist gitignored – prüfe vor jedem Commit mit `git status`, dass keine Datendateien gelistet sind.
- Log-Ausgaben maskieren automatisch Kartennummern (`[PAN MASKED]`).
