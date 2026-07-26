# WL Compare Tool — Projekt-Briefing für Claude Code

Dieses Dokument ist der dauerhafte Kontext. Lies es bei jedem Start zuerst.

## Zweck
Internes SwiPay-Analysetool. Vergleicht Worldline-Transaktionsexporte (XLSB/CSV)
transaktionsgenau gegen ein SwiPay-IC++-Angebot (IC++ gegen IC++, Variation nur
auf der ASF). Ausgabe: kundengerichteter PDF-Bericht in SwiPay-CI plus interner
CSV-Export. Kein Kunden-Selbstbedienungstool.

## Stack & Start
- macOS, uv, Python 3.13, pandas, pyxlsb, Streamlit, fpdf2, pytest
- Projektpfad: /Users/schellmo/Dokumente/Claude/Analysen/Prototyp
  (früher unter ~/Documents/Claude/…; Documents wurde auf «Dokumente» lokalisiert)
- Start: cd "/Users/schellmo/Dokumente/Claude/Analysen/Prototyp" && uv run streamlit run app.py
  (öffnet http://localhost:8501; nur EINE Instanz, nie eine zweite auf anderem Port)
- Tests: uv run pytest
- Nach einem Ordner-Umzug ist die .venv verbogen (hartkodierte Pfade → «Failed to
  spawn: streamlit»). Fix: rm -rf .venv && uv sync.
- Kundendaten liegen in data/ und sind gitignored. Nie committen.

## Arbeitsweise
- Architektur und Pläne werden zuerst im Chat mit Claude geprüft, dann baut
  Claude Code. Reihenfolge verbindlich: validierter Kern → Integrität →
  Datenqualität → Streamlit-UI → Reports. Integrität vor UI. Nie eine spätere
  Phase vorziehen.
- Prinzip: lieber eine Lücke als eine Lüge. Keine zu schönen Zahlen.

## Gelockte Entscheidungen (nicht ohne Rücksprache ändern)
- Berechnung strikt pro Transaktion, nie aggregiert.
- Worldline-Basis kommt aus der rohen, vorzeichenrichtigen Spalte «Gebühren» —
  NICHT aus abs-Komponenten rekonstruieren (verursachte Refund-Vorzeichenfehler).
- Gebühren = ASF + ICF + CSF. ASF ist der einzige variable Hebel. Floor auf
  ASF+ICF+CSF, realisiert über die ASF, je Brand/Kategorie.
- DCC: SwiPay-Satz ist eine einzelne globale Stellschraube. Default bewusst
  konservativ (1.40 %), pro Fall hochdrehbar (z. B. 1.85 %). Worldline-Cashback
  wird je Transaktion aus der echten DCC-Payback-Spalte gelesen, nicht
  angenommen. DCC-Vorteil = SP minus WL als separate KPI. Floor zuerst,
  Cashback danach.
- Nicht-offerierbare Brands (z. B. TWINT) → Delta 0, sichtbar als Null-Effekt.
- Gruppierung nach Partner-ID (Name nur Label). Fan-out-Flag bei mehreren
  Verträgen mit identischem Jahresumsatz; Default bis Auflösung: nicht summieren.
- Mehrdatei-Ingest sicher, kein blindes pd.concat. Dedup dreistufig: Dateiname
  (weich) → Inhalts-Hash (hart) → Zeilen-Schlüssel (Backstop). Duplikate
  verwerfen, nie still, mit Abgleichsbericht.
- Hochrechnung: KEINE lineare 365-Tage-Projektion. Anker ist der bekannte
  Jahresumsatz je Partner-ID. Stufe A (Jahresumsatz pro Brand bekannt) oder
  Stufe B (Mix auf Jahresumsatz skaliert). Deckungs-Labels >60 / 25–60 / <25 %.
  Planungsband ±15 %, immer bei Hochrechnung.
- Fixkosten und IC-Cap-Feinlogik = Phase 6, optional, nur bei konkretem Bedarf.

## Harter Validierungs-Anker (Davos-Datensatz)
- Worldline-Netto = 103'810.65 CHF über 113'497 Zeilen. Jede Engine-Änderung
  muss das reproduzieren.
- WL-Gebühren 116'513.10, DCC-Payback 12'702.45.
- Identität: Gebühren = Processing Fee + Scheme Fee + Interchange (null Abw.).
- validate.py prüft das.

## Offen vor Kundeneinsatz
- ASF-Defaults (Debit 0.30 %, Credit 0.35 %) sind Platzhalter. Vor jedem
  Kundenlauf die echte SwiPay-Preisliste eintragen.
- Abnahme gegen die acht Davos-Anker fahren, bevor das Tool auf eine echte
  Kundendatei losgelassen wird.

## Sicherheit
- Keine Kartennummern (PAN) oder PII im Klartext. Nur Token/Refs. Audit-Logs in
  UTC mit Correlation-ID. Code-Kommentare und Commits auf Englisch.

## Dokumentation
- Auswertungsprofil: https://swipay.atlassian.net/wiki/x/AQA4E
- Tool-Dokumentation: https://swipay.atlassian.net/wiki/x/I4BME

## Versionierung
Format:
Major.Minor.Patch.Hotfix
Beispiele:
4.x.x        = Major Release
4.1.x        = Minor Release
4.1.1        = Patch / Bugfix Release
4.1.1.1      = Hotfix
4.1.1.2      = Weiterer Hotfix