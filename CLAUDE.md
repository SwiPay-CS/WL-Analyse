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
  verwerfen, nie still, mit Abgleichsbericht.
  - Ist eine Datei NEU (anderer Inhalts-Hash), aber jede Zeile schon
    registriert, gibt ingest_files die geladenen Zeilen zur ANZEIGE zurück
    statt eines leeren DataFrames, und registriert die Datei NICHT
    (`files_known_rows` im Report). Tritt real auf, sobald ein Export in Excel
    neu gespeichert wurde: neue Bytes, identische Zeilen. Vorher leerte das den
    Bildschirm und schrieb eine processed_files-Zeile mit rows_added=0, die
    einen Ingest behauptete, der nicht stattfand. Ein Test sichert das ab.
  - Der Datei-Fingerprint eines Falls folgt den ZEILEN (row_keys → file_hash),
    nicht den Datei-Bytes. Ein neu gespeicherter Export gilt deshalb weiter als
    passend. Der ingest-seitige Fan-out-Flag
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
- Präsentation-Ansicht: view.build_view() (src/view.py, Streamlit-frei) baut
  EIN Zahlenpaket pro Rendering (ViewNumbers), entweder komplett hochgerechnet
  oder komplett Ist. Jede Kachel, jedes Chart UND das Kunden-PDF lesen nur
  daraus — nie Ist und p.a. mischen. Umschalter «Hochrechnung p.a. /
  Ist-Zeitraum», p.a. ist Default sobald eine Hochrechnung existiert,
  Umschalter fehlt wenn keine da ist. «Zeit & Verteilung» bleibt bewusst immer
  Ist (ein Monatsverlauf lässt sich nicht hochrechnen, ohne Saisonalität zu
  erfinden) und sagt das im Untertitel.
- Kunden-PDF trägt denselben ASF-Vergleich wie der Bildschirm (Gesamtrate
  gross, «Ø ASF x % · ±y %» klein). Wer eine Kennzahl auf dem Bildschirm
  ändert, muss sie im PDF mitziehen — beide lesen aus derselben View.
- Kunden-PDF (reporter.build_pdf) bekommt DASSELBE ViewNumbers-Objekt wie der
  Bildschirm und FOLGT damit der gewählten Ansicht; `projection` liefert nur
  noch Deckungsgrad, Planungsband und Entity-Listen, alle Beträge kommen aus
  der View. Aufbau spiegelt die Präsentation: Seite 1 Rahmendaten · Vorteil
  (Hero + Acquiring/DCC/Total) · zwei Vergleiche · Kennzahlen; Seite 2
  Kartentyp · DCC · Grundlage der Hochrechnung · Datenhinweise. Zwei feste
  Seitenumbrüche, kein Auto-Break mitten in einer Sektion.
- PDF-Kopf: dunkler Anthrazit-Balken, darauf das NEGATIV-Logo
  (assets/SWIPAY_Logo_negativ_de.svg). Gefunden wird es per MUSTER, nicht per
  festem Namen: *.svg|.png in assets/, Stem enthält «logo» plus einen Marker
  aus _NEG_MARKERS (negativ/negative/weiss/white/invers/inverse), SVG vor PNG.
  Grund: Illustrator-Exporte wechseln zwischen Binde- und Unterstrich und
  hängen Sprach-Suffixe an — eine feste Namensliste hat die echte Datei
  verpasst. Fehlt die Datei, greift eine weisse Wortmarke als Type — das ist
  Satz, kein umgefärbtes Logo.
  Hinweis: die Fills der Negativ-SVG stecken in style="fill: #fff;"
  (Inline-CSS), nicht in fill-Attributen. fpdf2 verarbeitet das; ein Grep nach
  fill=" findet sie NICHT.
  Das Positiv-Logo (assets/SWIPAY-Logo.svg) darf NIE auf den dunklen Balken:
  seine Wortmarke ist selbst anthrazit, und Brand & CI v2.1 verbietet
  Farbänderungen am Logo ausdrücklich («Logo-Grundregeln»: keine
  Farbänderungen, Mindestgrösse 25 mm Print, Freiraum 8 mm — beides
  eingehalten: 34 mm breit, 8 mm Abstand). Titel: «Payment Benchmarking»
  (ohne «SwiPay», das steht schon im Logo).
- PDF-Schrift ist Saira, die Hausschrift. Die TTFs liegen IM Repo unter
  assets/fonts/ (SIL OFL 1.1), damit das PDF überall gleich rendert. _F ist die
  Textfamilie (Regular/Bold/Italic), _FXB die ExtraBold-Familie für die grossen
  Zahlen — fpdf2 führt je Familie nur "", "B", "I", "BI". Fehlen die TTFs,
  fällt reporter.py auf Helvetica zurück UND setzt core_fonts_encoding auf
  cp1252; ohne das bricht der Ersatzpfad an Gedankenstrich und «», weil
  Latin-1 sie nicht kennt (ein Test sichert das ab). _safe() ist seit Saira
  eine Identitätsfunktion — Schweizer Typografie läuft unverändert durch.
- PDF-Fussnote: «SwiPay AG · Vertraulich - nur für autorisierte Empfänger ·
  Alle Angaben ohne Gewähr». NICHT «für den internen Gebrauch» — das Dokument
  geht an den Händler.
- Ansprache im PDF ist DU, nicht Sie (Brand & CI v2.1, Corporate Voice:
  «Anrede: immer Du — konsequent, überall»). Der Bildschirm hielt das schon
  ein, das PDF siezte.
- PDF-Grafiken sind native fpdf2-Vektoren (kein gerendertes Altair-PNG, keine
  Zusatz-Abhängigkeit). ACHTUNG: fpdf2s solid_arc() taugt NICHT für einen
  Donut — es platziert den Mittelpunkt nicht wie dokumentiert und zeichnet
  Winkel nicht proportional zur Spanne (50 % rendert als Viertelkeil). Die
  DCC-Ausschöpfung ist im PDF deshalb ein gestapelter Balken (_share_bar),
  auf dem Bildschirm bleibt der Donut (Altair rechnet korrekt).
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
- Gebührenveränderung (Kachel): zeigt die Richtung der GEBÜHREN, nicht der
  Ersparnis — Minus = Gebühren sinken = grün, Plus = rot. Die Farbe sitzt hier
  auf der ZAHL (ui.kpi_row akzeptiert value_color); sparsam einsetzen, sonst
  verliert Farbe ihre Signalwirkung. Fussnote nur «vs. Worldline».
- Die beiden Gebühren-Total-Kacheln tragen GROSS die Gesamtrate (Disagio) und
  KLEIN die Komponente, die den Unterschied macht: BEIDE heissen «Ø ASF».
  Worldline nennt sie intern «Processing Fee» — fachlich dasselbe, im
  Kundentermin heisst beides ASF (Nutzer-Entscheid 2026-08-22). Auf der
  SwiPay-Seite folgt die Veränderung DER ASF in Prozent, Vorzeichen wie bei der
  Gebührenveränderung (Minus = günstiger) — NICHT die Gesamtersparnis.
  ViewNumbers.asf_change_pct rechnet aus den BETRÄGEN, nicht aus den
  angezeigten gerundeten Sätzen; wer 0.124/0.179 im Kopf teilt, landet 0.2 pp
  daneben. Das ist SwiPays einziger variabler Hebel
  (ICF/CSF laufen als Pass-through identisch durch), aber KEINE Zerlegung der
  Ersparnis — auf Refunds modelliert die Engine eine volle Umkehr mit
  abs-Komponenten, Worldline bucht dort gemischte Vorzeichen (Davos: CHF 175
  Differenz auf CHF 8'561 Ersparnis). Deshalb NICHT «davon» schreiben; die
  Gesamtrate ist zudem netto nach DCC-Cashback, die ASF also kein reiner
  Teilbetrag davon.
- Datenartefakte im Worldline-Export (Davos, gilt generell):
  * Die LETZTE Zeile ist eine SUMMENZEILE: kein Betrag, keine Gebühren, aber
    processing_fee = Summe aller anderen (-22'568.08). pipeline.py filtert sie
    für Processing-Kennzahlen über `brutto.notna() | gebuehren.notna()` heraus
    — ohne das ist jede Processing-Rate exakt doppelt. NICHT aus den Daten
    entfernen: der Anker zählt 113'497 Zeilen.
  * Dieselbe Zeile hat keine Partner-ID und erschien als Händler «nan (nan)».
    _merchant_rows() überspringt Zeilen ohne Partner-ID (_is_real_pid);
    _artefact_rows() zählt sie, damit das Ausblenden sichtbar bleibt.
  * 8'571 Zeilen haben KEINE Komponenten-Aufschlüsselung (processing/scheme/
    interchange alle NaN) — praktisch alles TWINT (nicht offerierbar, Delta 0)
    plus 12 Nullbetrag-Zeilen. Deshalb hält die Identität Gebühren =
    Processing + Scheme + Interchange nur auf Karten-Zeilen. Genau daher die
    gelockte Regel, die WL-Basis aus der rohen `gebuehren`-Spalte zu nehmen.
- Jahresumsatz-Eingabe (Einstellungen → Merchants → Hochrechnung): Textfeld mit
  ui.parse_chf(), Anzeige in Schweizer Schreibweise mit Apostroph und zwei
  Dezimalen. st.number_input kann keine Tausendertrenner (format ist ein
  C-Format-String). Unlesbare Eingabe behält den letzten gültigen Wert und
  sagt es — nie stillschweigend 0, das würde eine Hochrechnung löschen.
- Gruppen-Hochrechnung: tragen Mitglieder eigene Jahresumsätze, zeigt die
  Gruppe die SUMME ihrer Mitglieder schreibgeschützt an und der
  Gruppen-Lump-Sum wird auf 0 gesetzt. Grund: per Präzedenz (aggregation.py)
  schlagen Mitglieder-Werte den Lump-Sum ohnehin — ein editierbares Feld ohne
  Wirkung wäre eine stille Doppelspur.
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
- Fälle und Vorlagen (Stand 2026-08-23): ZWEI getrennte Ebenen, nie vermischt.
  * Vorlage = wiederverwendbares Preisblatt OHNE Kundenbezug
    (config/profiles/<name>.json, git-versioniert). Art = standard | verband |
    rahmenvertrag, plus Notiz und updated_at. Im Tab «ASF & DCC»: Anwenden ·
    Überschreiben · Bearbeiten · Löschen, dazu «als neue Vorlage sichern».
    Hier gehört die echte SwiPay-Preisliste hin.
    - Überschreiben = aktuelle Konditionen IN die gewählte Vorlage speichern,
      Name/Art/Notiz bleiben (save_template überschreibt gleichnamig).
      Bearbeiten = nur Name/Art/Notiz, Sätze bleiben unangetastet
      (settings.update_template_meta). Beide zeigen erst die Konsequenz:
      Überschreiben eine Änderungsliste, Löschen eine Warnung. Grund: die
      Sätze sind von Hand aus einem Preisblatt getippt, und anders als ein
      Fall hat eine Vorlage KEINEN Autosave.
    - Umbenennen auf einen existierenden Namen wird abgelehnt, nicht
      zusammengeführt. «Als neue Vorlage speichern» mit vorhandenem Namen
      ebenso — dafür ist Überschreiben da, damit nichts still ersetzt wird.
    - Vorlagen-Namen werden zu Dateinamen: _check_template_name() weist «/»,
      «\» und führende Punkte ab.
    - Der Konditionen-Vergleich ist EINE Funktion (_kondition_diff in app.py),
      genutzt von der Fall-Änderungsliste und vom Überschreiben-Dialog.
  * Fall = eine Kundensituation (data/cases/, gitignored). Der KUNDE ist der
    Ordnungsbegriff, darunter benannte Varianten («konservativ»,
    «aggressiv»), jede mit sichtbarem Datum der letzten Speicherung.
    Ablage: kunde.json · export/<datei> EINMAL pro Kunde (nicht pro Variante,
    sonst 12 MB je Variante) · varianten/<name>.json · berichte/<ts>.pdf
    (nur auf Knopfdruck, als Nachweis des präsentierten Stands).
  * KEIN cases-Tisch in swipay.db: die Variantendatei IST das Exportformat
    (.swipaycase.json). Ein Schema, ein Codepfad, nichts das driften kann.
  * Payload v1 (case_store.SCHEMA_VERSION) pinnt Konditionen, Brand-Stammliste,
    Gruppen, Hochrechnungen, Auswahl (Scope/Zeitraum/Basis) und die
    Datei-Hashes des Exports. NIE Transaktionszeilen — die Datei wird
    weitergegeben, Karteninhaberdaten reisen nicht in einer Config mit. Eine
    neuere schema_version wird abgelehnt, nicht geraten.
  * Import-Regeln (Nutzer-Entscheid): Brand-Master GEWINNT bei Widerspruch
    (config/brands.json ist geteilt und git-versioniert — ein alter Fall darf
    sie nicht für alle künftigen Kunden umschreiben); Abweichung wird als Diff
    gezeigt, nur dem Master unbekannte Brands sind additiv wählbar.
    Hochrechnungen und Gruppen werden IN swipay.db geschrieben, aber erst nach
    einer Änderungsliste. Abgeglichen werden NUR die im Payload genannten
    Gruppen — andere gehören anderen Kunden.
  * Fehlende Partner-IDs (im Fall hinterlegt, in den Daten nicht vorhanden)
    werden sichtbar ausgewiesen, nie verschluckt.
  * Autosave läuft IMMER vor Reset und vor Fallwechsel, und ZUERST: der
    Schnappschuss muss die ALTEN DB-Werte tragen, sonst ist ein versehentlicher
    Import unumkehrbar. Reservierter Kunde «_autosave», ohne Export-Kopie (die
    Originaldatei liegt noch in data/). Kein fortlaufender Autosave.
  * Löschen der letzten Variante nimmt Export-Kopie und Berichte NICHT mit;
    dafür gibt es «Kunde ganz entfernen» mit Grössenangabe.
  * Partner-ID-Vergleich muss ui.pid-Semantik folgen: der Export liefert
    partner_id als float64 («174723.0»), swipay.db speichert «174723».
    case_store._norm_pid spiegelt das — ohne diese Normalisierung galt JEDE
    hinterlegte ID als fehlend (46 Fehlalarme auf dem Davos-Datensatz).
  * Nach JEDEM programmatischen Setzen von st.session_state.profile müssen die
    Widget-Keys vergessen werden (_forget_kondition_widgets in app.py:
    asf_/trx_/mf_/dcc_in, expert_editor). Sonst schreibt der ASF-Tab im
    nächsten Render seine alten Werte zurück — er speichert bei jedem
    Durchlauf.
  * ui.pct() multipliziert selbst mit 100: in der Änderungsliste den Bruch
    übergeben, nicht das Prozent (sonst steht 140.00 % statt 1.40 %).
- Reset lässt swipay.db weiter unberührt (nur die Arbeitssitzung), sichert aber
  vorher automatisch als Fall. Die Brand-Stammliste bleibt ebenfalls.
- Tool-Version steht in src/version.py (TOOL_VERSION, Format aus dem Abschnitt
  «Versionierung»). Sie erscheint in der Sidebar und in JEDER Fall-Datei —
  eine Datei muss sagen können, womit sie entstanden ist.
- Seite Transaktionen (Stand 2026-09-11): zweispaltig nach Vorbild des
  Etrax-Dashboards (swipay_etrax_disago_dashboard_v3-1_defr.html, Ansicht
  «Auswertung → Filter & Zeitraum»). Links ein Filter-Panel, rechts Kennzahlen,
  Monatsverlauf und Verteilung. Die Tabelle «Letzte Transaktionen» ist ENTFERNT
  — Einzelzeilen gibt es weiterhin über den Detail-CSV-Export der Präsentation.
  * Filterlogik in src/filters.py, Streamlit-frei und getestet. LEERE AUSWAHL
    HEISST «ALLE» und filtert nicht (vorher waren alle Optionen vorausgewählt).
    Mehrere Werte in einem Feld sind ODER, mehrere Felder UND.
  * Feld-Mapping Etrax → Worldline: VP-Name → partner_name, Terminal-ID →
    terminal_id, Vertriebsweg → vertrag, Herkunftsland → region, Brand → brand,
    Kartentypen → category. IBAN hat keine Entsprechung und fehlt bewusst.
  * Der Filter gilt NUR für diese Seite. Die Präsentation wählt Partner und
    Gruppen für die Hochrechnung aus; ein Brand- oder Terminal-Filter dort
    würde Kennzahlen und Kunden-PDF still verfälschen.
  * Jahres-Schnellwahl klemmt auf die vorhandenen Monate (filters.year_range):
    ein rollender Zwölfmonats-Export hat keinen Januar, 2025-01..2025-12 wäre
    eine leere Seite ohne Erklärung.
  * Der Monats-Chart zeigt den Bruttoumsatz der KÄUFE (wie _monthly, Refunds
    auf 0) — dieselbe Definition wie auf der Präsentation.
  * Widget-Keys tragen eine Generation (trx_gen). «Filter zurücksetzen» zählt
    sie hoch, statt nur die session_state-Schlüssel zu löschen: sonst schickt
    das Frontend seinen alten Wert zurück, das Feld zeigt weiter «Visa» und die
    Kennzahlen rechnen ungefiltert. Zweite Streamlit-Falle dieser Art, siehe
    auch _forget_kondition_widgets.
  * Jahres-Schnellwahl als st.button, nicht st.pills: die Chips sähen besser
    aus, reagieren aber auf keine automatisierte Eingabe und wären damit nicht
    prüfbar.
- Fixkosten und IC-Cap-Feinlogik = Phase 6, optional, nur bei konkretem Bedarf.

## Harter Validierungs-Anker (Davos-Datensatz)
- Worldline-Netto = 103'810.65 CHF über 113'497 Zeilen. Jede Engine-Änderung
  muss das reproduzieren.
- WL-Gebühren 116'513.10, DCC-Payback 12'702.45.
- Identität: Gebühren = Processing Fee + Scheme Fee + Interchange (null Abw.).
- validate.py prüft das.

## Status
Kopfzeile und Sidebar zeigen «Live» (vorher «IN ABNAHME»), auf Nutzer-Entscheid
2026-08-22. Der Titel im Präsentations-Header lautet «Payment Benchmarking ·
<Partner>», gleichlautend mit dem PDF-Titel.

## Offen vor Kundeneinsatz
- ASF-Defaults (Debit 0.30 %, Credit 0.35 %) sind Platzhalter. Vor jedem
  Kundenlauf die echte SwiPay-Preisliste eintragen — jetzt als Vorlage
  (Einstellungen → ASF & DCC → Vorlagen, Art «Standardkonditionen»), damit sie
  nicht bei jedem Kunden neu getippt werden muss.
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