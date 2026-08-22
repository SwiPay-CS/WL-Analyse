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
- Änderungen in src/ werden von der laufenden Streamlit-Instanz NICHT neu
  geladen (die Module hängen in sys.modules; nur app.py wird neu ausgeführt).
  Neue oder umbenannte Funktionen in src/ ⇒ ImportError/AttributeError, bis der
  Prozess neu startet. Also: Prozess killen und neu starten, nicht nur die
  Seite neu laden.
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
- Gruppierung nach Partner-ID (Name nur Label).
- Mehrdatei-Ingest sicher, kein blindes pd.concat. Dedup dreistufig: Dateiname
  (weich) → Inhalts-Hash (hart) → Zeilen-Schlüssel (Backstop). Duplikate
  verwerfen, nie still, mit Abgleichsbericht. Der ingest-seitige Fan-out-Flag
  (`fanout_partner_ids` in ingest.py, mehrere Vertragsnummern mit identischem
  Jahresumsatz IM EXPORT) ist ein separates Konzept von der Hochrechnung
  weiter unten — bleibt unverändert (Datenauffälligkeit, manuell zu klären).
- Hochrechnung (Stand 2026-08-03, überarbeitet): KEINE lineare
  365-Tage-Projektion. Anker ist der bekannte Jahresumsatz je Partner-ID/
  Gruppe. Aktuell nur Tier B verdrahtet (Mix auf Jahresumsatz skaliert; Tier A
  pro Brand existiert in projection.py, bewusst nicht an die UI angebunden).
  Eingabe unter Einstellungen → Merchants → Hochrechnung ("Schnellhochrechnung"),
  durable in swipay.db (hochrechnung_store.py) gespeichert, Partner-ID/
  Gruppenname-gebunden (nicht sitzungsgebunden, übersteht Reset — wie die
  Gruppen-Zuordnungen). Wirkt sich automatisch auf die Seite Präsentation aus
  (Scopes Alle / mehrere Partner / eine Gruppe; siehe aggregation.py) — kein
  separates Eingabefeld dort mehr.
  - Deckungs-Labels >60 / 25–60 / <25 %, Planungsband ±15 % (siehe
    projection.py). Bei mehreren Entities im Scope bezieht sich der
    Deckungsgrad NUR auf die tatsächlich hochgerechneten Entities.
  - Headline-Regel geändert 2026-08-21 (auf Nutzer-Entscheid): der Hero zeigt
    IMMER den errechneten Punktwert, auch bei indikativer Deckung — nicht mehr
    das konservative Bandende. Grund: oben stand sonst eine andere Zahl als in
    der Detailkachel und der Typ-Aufschlüsselung. Die Vorsicht bleibt sichtbar,
    aber explizit: Planungsband unter der Zahl plus Deckungs-Banner.
  - Portfolio-Abdeckung ist eine zweite, unabhängige Kennzahl: Anteil des
    Ist-Bruttoumsatzes im Scope, der überhaupt hochgerechnet wurde (sinkt,
    wenn viele Merchants/Gruppen im Scope keine Hochrechnung haben) — anders
    als der Deckungsgrad, der davon unberührt bleibt.
  - Fan-out bei der Hochrechnung (mehrere Merchants/Gruppen mit identischem
    Jahresumsatz-Betrag): wird IMMER summiert, nur als Hinweis markiert
    (Bildschirm-Banner + PDF-Datenhinweis) — kein Blocker, keine Persistenz
    einer Auflösungs-Entscheidung. Ersetzt den alten Default "nicht summieren".
  - Datenqualitäts-Hinweis: sichtbares Banner in der Präsentation ab
    "niedrige Deckung" (amber) und "indikativ" (rot), Farben synchron mit den
    PDF-Badges (reporter.py). Nie verstecken, auch bei schlechter Deckung —
    lieber eine Lücke als eine Lüge.
- Vorteils-Zerlegung (Stand 2026-08-21): Der geldwerte Vorteil wird IMMER in
  zwei Hebel zerlegt, nie als eine Zahl allein gezeigt:
      Acquiring-Vorteil = wl_fee - sp_fee        (gesparte Gebühren)
      DCC-Vorteil       = sp_cashback - wl_cashback  (höherer Cashback)
      Total             = Acquiring + DCC        (exakte Identität)
  Die Identität gilt algebraisch (wl_net = wl_fee - wl_cashback), nicht
  näherungsweise — Residuum 0.0 auf dem Davos-Datensatz. Nie eine Restposition
  einführen. Sprachlich getrennt halten: beim Acquiring SPART der Händler, beim
  DCC BEKOMMT er mehr. Grün/Cyan bei Vorteil, Dunkelrot auf einem Bein, auf dem
  SwiPay schlechter ist.
- volume_bases() in projection.py ist die EINZIGE Definition von DCC- und
  Fremdwährungsvolumen (Bildschirm, Projektion, PDF) und liefert BEIDE Basen
  (projection.VolumeBases):
    * netto (Refunds inklusive) — so sind die Davos-Anker gelockt
      (fx 4'336'735.23, dcc 905'721.92). Trägt die Ausschöpfungsquote und die
      angezeigten Volumen. Nicht ohne Rücksprache auf «nur Käufe» umstellen.
    * nur Käufe — Basis JEDER Cashback-Satz-Rechnung, weil sp_cashback in
      pipeline.py auf ~is_refund maskiert ist. Auf der Netto-Basis ergäbe
      sp_cashback / dcc_volumen 1.8518 % statt der eingestellten 1.85 %:
      Zähler und Nenner sässen auf verschiedenen Basen.
  Die Ausschöpfung wird mit EINER Dezimale angezeigt (Donut-Labels und
  Caption synchron).
- Präsentation-Ansicht: _view() in app.py baut EIN Zahlenpaket pro Rendering,
  entweder komplett hochgerechnet oder komplett Ist. Jede Kachel und jedes
  Chart liest nur daraus — nie Ist und p.a. mischen. Umschalter «Hochrechnung
  p.a. / Ist-Zeitraum», p.a. ist Default sobald eine Hochrechnung existiert,
  Umschalter fehlt wenn keine da ist. «Zeit & Verteilung» bleibt bewusst immer
  Ist (ein Monatsverlauf lässt sich nicht hochrechnen, ohne Saisonalität zu
  erfinden) und sagt das im Untertitel.
- Aufschlüsselungen (z. B. nach Kartentyp) werden je Entity mit IHREM eigenen
  Faktor skaliert (aggregation.AggregateProjection.scales), nie mit einem
  gemischten Durchschnittsfaktor — sonst deckt sich die Summe nicht mit dem
  Hero-Wert.
- Kartentyp-Labels und -Reihenfolge sind fix. _TYPE_LABEL in app.py ist die
  EINZIGE Label-Quelle und deckt alle vier Typen aus settings.ALL_TYPES ab:
  Debit, Credit M/V (Mastercard/Visa), Credit Rest (Diners/Discover, JCB,
  UnionPay), QR-Code. Gilt überall, wo ein Typ benannt wird — Ersparnis-
  Aufschlüsselung, ASF-Eingabe und Brand-Stammliste (Einstellungen → Mapping).
  Die Stammliste zeigt Labels und mappt beim Speichern über _TYPE_KEY zurück
  auf den Schlüssel; ein roher Schlüssel wird weiterhin akzeptiert.
  _TYPE_ORDER = Debit, Credit M/V, Credit Rest, QR-Code / n/a. NIE nach Betrag
  sortieren — im Kundentermin muss dieselbe Zeile an derselben Stelle stehen.
- In der Ersparnis-Aufschlüsselung teilen «spezial» und nicht zuordenbare
  Brands EINE Sammelzeile «QR-Code / n/a» (_type_bucket() in app.py). Beide
  haben per Definition Delta 0; eine Trennung ergäbe zwei Null-Zeilen.
- Effektive Gebührenrate: als Prozent vom Bruttoumsatz, NICHT in Basispunkten,
  mit drei Dezimalen (pct_rate()/RATE_DEC in app.py). Zwei Dezimalen ergäben
  0.75 % und 0.67 %, und 0.08/0.75 = 10.7 % widerspräche der Kachel
  «Gebühren-Reduktion» (11.3 %). Labels: «Gebühren Total WL» / «Gebühren Total
  SwiPay». Eine Ratendifferenz ist in %-PUNKTEN auszuweisen (pp()), nie in %,
  weil daneben die relative Reduktion in % steht.
- «Woher der Vorteil kommt» zeigt zwei Vergleiche nebeneinander: links die
  Acquiring-Gebühren (wl_fee gegen sp_fee, was der Händler ZAHLT), rechts den
  DCC-Cashback (was er BEKOMMT). Beide Deltas sind exakt die zwei Kacheln
  darüber. Kein Wasserfall, kein erklärender Textblock daneben.
- DCC-Potenzial: «Cashback bei 100 %» ist eine theoretische Obergrenze, keine
  Prognose, und muss so benannt bleiben. Gerechnet auf dem DCC-fähigen
  KAUFvolumen, das als eigene Kachel daneben steht («DCC-Kaufvolumen»), damit
  die Obergrenze nachrechenbar ist.
  Die Kachel «DCC-Vorteil bei 100 %» wurde 2026-08-22 auf Nutzer-Entscheid
  entfernt (als hypothetischer Wert nicht relevant genug), zusammen mit dem
  erklärenden Absatz dazu. Damit fehlt der Seite der Hinweis, dass Worldline
  bei höherer Ausschöpfung ebenfalls mehr Cashback zahlt — das «unrealisierte
  Cashback» ist also NICHT der Zusatzvorteil eines Wechsels. Im Kundentermin
  mündlich einordnen; nicht unkommentiert als Vorteil verkaufen.
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