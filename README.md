# swipay-wl-compare

Internes Analyse-Tool für das SwiPay-Team. Vergleicht Worldline-Transaktionsexporte
(XLSB/CSV) gegen ein SwiPay IC++-Angebot und zeigt die Ersparnis.

> **Achtung:** Dieses Tool verarbeitet echte Zahlungsdaten. Exportdateien gehören
> ausschliesslich in `data/` — dieser Ordner ist durch `.gitignore` geschützt
> und wird niemals ins Repository eingecheckt.

---

## Voraussetzungen

- [uv](https://docs.astral.sh/uv/) (Versions- und Paketmanager)
- macOS mit Python 3.13 (wird von uv automatisch verwaltet)

## Installation

```bash
# 1. Repository klonen
git clone <repo-url>
cd swipay-wl-compare

# 2. Virtuelle Umgebung und Abhängigkeiten einrichten
uv sync
```

## Projektstruktur

```
src/          Engine-Kern (Rechenlogik, Loader, Pipeline)
tests/        Unit-Tests
data/         Eingabedateien (gitignored — nie committen!)
validate.py   Sanity-Check gegen echte Worldline-Daten
```

## Tests ausführen

```bash
uv run pytest -v
```

Alle 8 Tests müssen grün sein.

## Validierung gegen Echtdaten

Exportdatei ins `data/`-Verzeichnis legen, dann:

```bash
uv run python validate.py
```

Erwartetes Ergebnis: Worldline-Netto `103'810.65 CHF` (113'497 Transaktionen).

## Engine-Kern

Die Kernlogik in `src/` ist bewusst framework-agnostisch gehalten:

- `engine.py` — Rechenlogik (BrandParams, swipay_fee, worldline_net)
- `loader.py` — Worldline-Import, Brand-Normalisierung, Idempotenzschlüssel
- `pipeline.py` — vektorisierter Vergleich über NumPy (>100k Zeilen)

Der DCC-Cashback-Satz ist ein **globaler Parameter** (`dcc_cashback_pct`),
der an `run_comparison()` übergeben wird — nicht je Kategorie.
