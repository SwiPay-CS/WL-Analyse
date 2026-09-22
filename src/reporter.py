"""
Phase-5 report generation.

build_pdf(**kwargs) -> bytes   Customer PDF in SwiPay CI.
build_csv(fdf, comp) -> str    Internal CSV with all engine dimensions.

CI source: extracted from production SwiPay dashboards.
Palette: --rot #be5e48, --dunkelrot #8e312b, --anthrazit #3e4b4c,
         --cyan #3c8f99, --green #949f50, --bg #f4f2ef.
Font: Helvetica (PDF built-in; matches Saira weight/structure closely enough).
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd
from fpdf import FPDF

from projection import CoverageLabel
from view import RATE_DEC, ViewNumbers


# ── CI palette ────────────────────────────────────────────────────────────────
_ROT        = (190,  94,  72)   # #be5e48  primary brand
_DUNKELROT  = (142,  49,  43)   # #8e312b
_ANTHRAZIT  = ( 62,  75,  76)   # #3e4b4c  header / body text
_CYAN       = ( 60, 143, 153)   # #3c8f99
_GREEN_CI   = (148, 159,  80)   # #949f50
_AMBER      = (200, 150,  50)
_ORANGE     = (236, 102,   8)   # #ec6608 -- Cashback Aktuell, spiegelt ui.ORANGE
_BG         = (244, 242, 239)   # #f4f2ef
_BLUE       = ( 34,  79,  89)   # #224f59
_LINE       = (227, 221, 214)   # #e3ddd6
_WHITE      = (255, 255, 255)
_DIM        = (138, 148, 149)   # ≈ rgba(62,75,76,.62)
_FAINT      = (170, 175, 176)   # ≈ rgba(62,75,76,.42)

_ASSETS = Path(__file__).resolve().parent.parent / "assets"
_FONTS  = _ASSETS / "fonts"

# Saira ist die Hausschrift (Brand & CI v2.1). Die TTFs liegen im Repo, damit
# das PDF ueberall gleich rendert. _F ist die Textfamilie (Regular/Bold/
# Italic), _FXB die ExtraBold-Familie fuer die grossen Zahlen -- fpdf2 fuehrt
# je Familie nur "", "B", "I", "BI", ExtraBold braucht deshalb eine eigene.
_F   = "Saira"
_FXB = "SairaXB"
_FALLBACK = "Helvetica"   # nur falls die TTFs fehlen

_SAIRA_FACES = {
    (_F, ""):   "Saira-Regular.ttf",
    (_F, "B"):  "Saira-Bold.ttf",
    (_F, "I"):  "Saira-Italic.ttf",
    (_FXB, ""): "Saira-ExtraBold.ttf",
}

_BADGE_COLORS = {
    CoverageLabel.SIMULATABLE:  _GREEN_CI,
    CoverageLabel.LOW_COVERAGE: _AMBER,
    CoverageLabel.INDICATIVE:   _ROT,
}


def _chf(v: float, dec: int = 2) -> str:
    return f"{v:,.{dec}f}".replace(",", "'")


def _num(v: float) -> str:
    return f"{int(round(v)):,}".replace(",", "'")


def _pct_rate(frac: float) -> str:
    """Gebuehrenrate als Prozent vom Umsatz. RATE_DEC kommt aus view.py, damit
    Bildschirm und PDF dieselbe Praezision zeigen."""
    return f"{frac * 100:.{RATE_DEC}f} %"


def _safe(text: str) -> str:
    """Identity since the report embeds Saira (a Unicode TTF).

    Bleibt als Funktion bestehen, weil sie an vielen Stellen aufgerufen wird
    und die Absicht dokumentiert: hier stand die Latin-1-Bereinigung fuer
    Helvetica, die Gedankenstriche und «» verstuemmelt hat. Mit Saira ist das
    unnoetig -- Schweizer Typografie laeuft unveraendert durch.
    """
    return text


def _register_fonts(pdf: FPDF) -> tuple[str, str]:
    """Saira-Schnitte registrieren. Gibt (Textfamilie, ExtraBold-Familie)
    zurueck -- bei fehlenden TTFs beide als Helvetica, damit ein Bericht
    lieber in der Ersatzschrift entsteht als gar nicht."""
    # cp1252 statt latin-1 fuer die Kernschriften: es deckt Gedankenstrich,
    # «», Bullet und Auslassungspunkte ab. Ohne das bricht der Fallback-Pfad
    # an genau der Typografie, die mit Saira korrekt ist.
    pdf.core_fonts_encoding = "cp1252"
    missing = [f for f in _SAIRA_FACES.values() if not (_FONTS / f).exists()]
    if missing:
        return _FALLBACK, _FALLBACK
    for (family, style), fname in _SAIRA_FACES.items():
        pdf.add_font(family, style, str(_FONTS / fname))
    return _F, _FXB


# ── PDF base class ────────────────────────────────────────────────────────────

# Negativ-Logo (weisse Wortmarke) fuer den dunklen Header-Balken.
# WICHTIG: nie das Positiv-Logo umfaerben -- Brand & CI v2.1, "Logo-
# Grundregeln": keine Farbaenderungen. Deshalb eine eigene Datei.
#
# Gesucht wird nach MUSTER, nicht nach festen Namen: die Assets kommen aus
# Illustrator-Exporten mit wechselnder Schreibweise (Binde- vs. Unterstrich,
# Sprach-Suffix wie "_de"). SVG gewinnt gegen PNG, weil es als Vektor skaliert.
_NEG_MARKERS = ("negativ", "negative", "weiss", "white", "invers", "inverse")


def _negative_logo() -> Path | None:
    if not _ASSETS.exists():
        return None
    cands = [
        f for f in _ASSETS.iterdir()
        if f.suffix.lower() in (".svg", ".png")
        and "logo" in f.stem.lower()
        and any(m in f.stem.lower() for m in _NEG_MARKERS)
    ]
    if not cands:
        return None
    cands.sort(key=lambda f: (f.suffix.lower() != ".svg", f.name.lower()))
    return cands[0]


class _SwiPayPDF(FPDF):
    """FPDF subclass with the SwiPay CI header and footer."""

    generated_date: str = ""
    # Von _register_fonts() gesetzt; Default haelt header()/footer() lauffaehig,
    # falls die Saira-TTFs fehlen.
    fam: str = _FALLBACK
    fam_xb: str = _FALLBACK

    # Brand & CI v2.1, "Logo-Grundregeln": Mindestgroesse 25 mm Print, Freiraum
    # mindestens 8 mm zu anderen Elementen, keine Farbaenderungen.
    _LOGO_W = 34.0
    _LOGO_CLEARANCE = 8.0
    _BAR_H = 24.0

    def header(self) -> None:
        # Dunkler Balken ueber die volle Breite (CI der Produktions-Dashboards).
        self.set_fill_color(*_ANTHRAZIT)
        self.rect(0, 0, 210, self._BAR_H, style="F")

        logo = _negative_logo()
        title_x = self.l_margin
        if logo is not None:
            # Vertikal zentriert: Logo-Seitenverhaeltnis 453.54 : 170.08.
            h = self._LOGO_W * 170.079 / 453.54
            self.image(str(logo), x=self.l_margin,
                       y=(self._BAR_H - h) / 2, w=self._LOGO_W)
            title_x = self.l_margin + self._LOGO_W + self._LOGO_CLEARANCE
        else:
            # Kein Negativ-Logo hinterlegt: Wortmarke als weisse Type. Das ist
            # kein umgefaerbtes Logo, sondern Satz -- CI-konform.
            self.set_font(self.fam_xb, "", 15)
            self.set_text_color(*_WHITE)
            self.set_xy(self.l_margin, 6.5)
            self.cell(32, 10, "SwiPay", border=0)
            title_x = self.l_margin + 32 + self._LOGO_CLEARANCE

        self.set_font(self.fam, "", 11)
        self.set_text_color(215, 219, 219)
        self.set_xy(title_x, 8)
        self.cell(95, 8, "Payment Benchmarking", border=0)

        self.set_font(self.fam, "", 7.5)
        self.set_text_color(160, 168, 168)
        self.set_xy(130, 9.5)
        self.cell(68, 6, self.generated_date, border=0, align="R")

        self.set_y(self._BAR_H + 6)

    def footer(self) -> None:
        self.set_y(-14)
        self.set_draw_color(*_LINE)
        self.set_line_width(0.25)
        self.line(12, self.get_y(), 198, self.get_y())
        self.set_font(self.fam, "", 7)
        self.set_text_color(*_FAINT)
        self.cell(0, 8,
                  "SwiPay AG  ·  Vertraulich – nur für autorisierte Empfänger"
                  "  ·  Alle Angaben ohne Gewähr",
                  border=0, align="C")
        self.set_xy(12, self.get_y() - 8)
        self.set_font(self.fam, "", 7)
        self.cell(0, 8, f"Seite {self.page_no()}", border=0, align="R")


# ── Layout helpers ────────────────────────────────────────────────────────────

def _section_header(pdf: _SwiPayPDF, title: str) -> None:
    """Brand-red section title with rule below."""
    pdf.set_font(pdf.fam, "B", 8.5)
    pdf.set_text_color(*_ROT)
    pdf.cell(0, 5, title.upper(), border=0)
    y = pdf.get_y() + 5
    pdf.set_draw_color(*_ROT)
    pdf.set_line_width(0.35)
    pdf.line(pdf.l_margin, y, pdf.l_margin + 186, y)
    pdf.ln(8)
    pdf.set_text_color(*_ANTHRAZIT)
    pdf.set_draw_color(*_LINE)
    pdf.set_line_width(0.2)


def _kv(pdf: _SwiPayPDF, label: str, value: str, bold_val: bool = False) -> None:
    """Key - Value row, label left dimmed, value right."""
    pdf.set_font(pdf.fam, "", 9)
    pdf.set_text_color(*_DIM)
    pdf.cell(95, 6, _safe(label), border=0)
    pdf.set_font(pdf.fam, "B" if bold_val else "", 9)
    pdf.set_text_color(*_ANTHRAZIT)
    pdf.cell(91, 6, _safe(value), border=0, align="R")
    pdf.ln()


def _divider(pdf: _SwiPayPDF) -> None:
    pdf.set_draw_color(*_LINE)
    pdf.set_line_width(0.2)
    y = pdf.get_y()
    pdf.line(pdf.l_margin, y, pdf.l_margin + 186, y)
    pdf.ln(3)


def _big_kpi(pdf: _SwiPayPDF, label: str, value: str, rgb: tuple) -> None:
    """Highlighted key metric row."""
    pdf.set_font(pdf.fam, "", 9)
    pdf.set_text_color(*_DIM)
    pdf.cell(95, 8, label, border=0)
    pdf.set_font(pdf.fam_xb, "", 14)
    pdf.set_text_color(*rgb)
    pdf.cell(91, 8, value, border=0, align="R")
    pdf.ln(8)


def _flag(pdf: _SwiPayPDF, text: str, rgb: tuple = _DIM) -> None:
    """Bulleted flag / hint line. Resets x explicitly: after a multi_cell the
    cursor sits at the right edge, which pushed the next flag off the page."""
    pdf.set_x(pdf.l_margin)
    pdf.set_font(pdf.fam, "", 8.5)
    pdf.set_text_color(*_DIM)
    pdf.cell(5, 6, "-", border=0)
    pdf.set_text_color(*rgb)
    pdf.multi_cell(181, 5.5, _safe(text), border=0)


# ── Vektor-Charts (dieselben Bilder wie auf dem Bildschirm) ───────────────────
# Bewusst native fpdf2-Primitive statt gerenderter Altair-PNGs: keine
# Zusatz-Abhaengigkeit, scharfe Vektoren im Druck, und die CI-Farben kommen aus
# derselben Palette wie der Rest des Berichts.

def _bar_pair(pdf: _SwiPayPDF, x: float, y: float, w: float, h: float,
              title: str, wl: float, sp: float, sp_rgb: tuple) -> None:
    """Worldline gegen SwiPay als zwei Balken -- das PDF-Gegenstueck zu
    ui.chart_compare(). Nulllinie immer bei 0, damit kein abgeschnittener
    Achsenausschnitt den Unterschied groesser aussehen laesst."""
    pdf.set_font(pdf.fam, "B", 7.5)
    pdf.set_text_color(*_DIM)
    pdf.set_xy(x, y)
    pdf.cell(w, 4, _safe(title.upper()), border=0)

    top = y + 6.5
    plot_h = h - 12          # Platz fuer Titel oben, Beschriftung unten
    lo = min(0.0, wl, sp)
    hi = max(0.0, wl, sp)
    span = (hi - lo) or 1.0
    zero_y = top + plot_h * (hi / span)

    bar_w = 15.0
    gap = (w - 2 * bar_w) / 3.0
    for i, (label, val, rgb) in enumerate(
            [("Aktuell", wl, _ANTHRAZIT), ("SwiPay", sp, sp_rgb)]):
        bx = x + gap * (i + 1) + bar_w * i
        bh = abs(val) / span * plot_h
        by = zero_y - bh if val >= 0 else zero_y
        pdf.set_fill_color(*rgb)
        pdf.rect(bx, by, bar_w, max(bh, 0.4), style="F")
        # Betrag ueber (bzw. unter) dem Balken.
        pdf.set_font(pdf.fam, "B", 7.5)
        pdf.set_text_color(*_ANTHRAZIT)
        pdf.set_xy(bx - 6, (by - 4.4) if val >= 0 else (by + bh + 0.4))
        pdf.cell(bar_w + 12, 4, _chf(val, 0), border=0, align="C")
        # Anbieter darunter.
        pdf.set_font(pdf.fam, "", 7)
        pdf.set_text_color(*_DIM)
        pdf.set_xy(bx - 6, top + plot_h + 1)
        pdf.cell(bar_w + 12, 4, label, border=0, align="C")

    # Nulllinie nur zeichnen, wenn sie im Bild liegt (negative Werte).
    if lo < 0:
        pdf.set_draw_color(*_LINE)
        pdf.set_line_width(0.2)
        pdf.line(x, zero_y, x + w, zero_y)


def _hbars(pdf: _SwiPayPDF, x: float, y: float, w: float,
           rows: list[tuple[str, float]], row_h: float = 6.2) -> float:
    """Waagrechte Balken in der VORGEGEBENEN Reihenfolge (nicht nach Betrag) --
    das PDF-Gegenstueck zu ui.chart_savings_by_type(). Gibt die neue y-Position
    zurueck."""
    if not rows:
        return y
    label_w, val_w = 30.0, 26.0
    track = w - label_w - val_w
    span = max((abs(v) for _, v in rows), default=1.0) or 1.0
    for label, val in rows:
        pdf.set_font(pdf.fam, "", 8)
        pdf.set_text_color(*_ANTHRAZIT)
        pdf.set_xy(x, y)
        pdf.cell(label_w, row_h, _safe(label), border=0)
        # Balkenlaenge proportional zum groessten Betrag der Gruppe.
        bl = abs(val) / span * track
        pdf.set_fill_color(*(_GREEN_CI if val >= 0 else _ROT))
        pdf.rect(x + label_w, y + 1.5, max(bl, 0.4), row_h - 3, style="F")
        pdf.set_font(pdf.fam, "B", 8)
        pdf.set_text_color(*_ANTHRAZIT)
        pdf.set_xy(x + w - val_w, y)
        pdf.cell(val_w, row_h, f"CHF {_chf(val, 0)}", border=0, align="R")
        y += row_h
    return y


def _share_bar(pdf: _SwiPayPDF, x: float, y: float, w: float, share: float,
               used_lbl: str, rest_lbl: str, h: float = 9.0) -> float:
    """Ausschoepfung als gestapelter Balken: genutzter Anteil cyan, Rest grau.

    Bewusst KEIN Donut wie auf dem Bildschirm: fpdf2s solid_arc() zeichnet
    weder den Mittelpunkt an der dokumentierten Stelle noch Winkel proportional
    zur Spanne (50 % rendert als Viertelkeil). Ein falsch proportionierter
    Kuchen im Kundenbericht waere schlimmer als keiner -- zwei Rechtecke sind
    exakt. Gibt die neue y-Position zurueck.
    """
    share = min(max(share, 0.0), 1.0)
    pdf.set_fill_color(217, 210, 201)
    pdf.rect(x, y, w, h, style="F")
    if share > 0.0005:
        pdf.set_fill_color(*_CYAN)
        pdf.rect(x, y, w * share, h, style="F")

    # Prozentwert in den Balken, wenn er passt -- sonst rechts daneben.
    txt = f"{share * 100:.1f} %"
    pdf.set_font(pdf.fam, "B", 8)
    if w * share > 18:
        pdf.set_text_color(*_WHITE)
        pdf.set_xy(x + 2, y + 1.4)
        pdf.cell(w * share - 4, h - 3, txt, border=0)
    else:
        pdf.set_text_color(*_ANTHRAZIT)
        pdf.set_xy(x + w * share + 2, y + 1.4)
        pdf.cell(30, h - 3, txt, border=0)

    y += h + 1.5
    pdf.set_font(pdf.fam, "", 7)
    pdf.set_text_color(*_DIM)
    pdf.set_xy(x, y)
    pdf.cell(w / 2, 4, _safe(used_lbl), border=0)
    pdf.set_xy(x + w / 2, y)
    pdf.cell(w / 2, 4, _safe(rest_lbl), border=0, align="R")
    return y + 5


def _tiles(pdf: _SwiPayPDF, y: float, tiles: list[tuple], w_total: float = 186.0,
           h: float = 17.0, x0: float | None = None) -> float:
    """Kachelzeile: Label klein oben, Wert gross darunter, Akzentbalken links.
    Spiegelt ui.kpi_row(). Jede Kachel ist (label, value, rgb) oder
    (label, value, rgb, foot) -- die Fussnote traegt Zusaetze wie eine
    Ratendifferenz, die im Label abgeschnitten wuerden. x0 setzt den linken
    Rand. Gibt die neue y-Position zurueck."""
    n = len(tiles)
    gap = 3.0
    w = (w_total - gap * (n - 1)) / n
    left = pdf.l_margin if x0 is None else x0
    has_foot = any(len(t) > 3 and t[3] for t in tiles)
    box_h = h + (3.5 if has_foot else 0.0)
    for i, t in enumerate(tiles):
        label, value, rgb = t[0], t[1], t[2]
        foot = t[3] if len(t) > 3 else ""
        x = left + i * (w + gap)
        pdf.set_fill_color(250, 249, 247)
        pdf.rect(x, y, w, box_h, style="F")
        pdf.set_fill_color(*rgb)
        pdf.rect(x, y, 1.4, box_h, style="F")
        pdf.set_font(pdf.fam, "B", 6.5)
        pdf.set_text_color(*_DIM)
        pdf.set_xy(x + 3.5, y + 2)
        pdf.cell(w - 5, 3.5, _safe(label.upper()), border=0)
        pdf.set_font(pdf.fam_xb, "", 12)
        pdf.set_text_color(*rgb)
        pdf.set_xy(x + 3.5, y + 6.5)
        pdf.cell(w - 5, 7, _safe(value), border=0)
        if foot:
            pdf.set_font(pdf.fam, "", 6.5)
            pdf.set_text_color(*_DIM)
            pdf.set_xy(x + 3.5, y + 13.4)
            pdf.cell(w - 5, 3.5, _safe(foot), border=0)
    return y + box_h + 3


# ── Public API ────────────────────────────────────────────────────────────────

def build_pdf(
    *,
    view: ViewNumbers,
    partner_name: str,
    period_from: str,
    period_to: str,
    n_terminals: int = 0,
    dcc_pct: float = 0.0,
    savings_by_type: list[tuple[str, float]] | None = None,
    projection=None,                  # AggregateProjection | ProjectionResult
    portfolio_coverage_pct: float | None = None,
    n_entities_used: int = 0,
    n_entities_total: int = 0,
    duplicate_volume_warnings: list[list[str]] | None = None,
    fanout_partner_ids: list[str] | None = None,
    zero_effect_brands: list[str] | None = None,
    mix_hints: list[str] | None = None,
    generated_date: str | None = None,
) -> bytes:
    """Kunden-PDF als Bytes.

    `view` ist dasselbe Zahlenpaket, das der Bildschirm rendert (view.py). Das
    PDF FOLGT damit der gewaehlten Ansicht -- Hochrechnung p.a. oder
    Ist-Zeitraum -- und kann nicht von der Praesentation abweichen.

    `projection` liefert nur noch Deckungsgrad, Planungsband und die
    Entity-Listen; alle Betraege kommen aus `view`.
    """
    today_str = generated_date or date.today().strftime("%d.%m.%Y")
    v = view
    proj_mode = v.is_projected and projection is not None

    pdf = _SwiPayPDF(format="A4")
    pdf.generated_date = today_str
    # Fonts VOR add_page() registrieren -- header() zeichnet sofort mit.
    pdf.fam, pdf.fam_xb = _register_fonts(pdf)
    pdf.set_margins(12, 12, 12)
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()
    W = 186.0  # nutzbare Breite

    # ── Rahmendaten ───────────────────────────────────────────────────────────
    _section_header(pdf, "Rahmendaten")
    _kv(pdf, "Partner", partner_name)
    _kv(pdf, "Auswertungszeitraum", f"{period_from} – {period_to}")
    basis_txt = ("Hochrechnung auf Jahresbasis (p.a.)" if v.is_projected
                 else "Ist-Werte des Auswertungszeitraums")
    _kv(pdf, "Darstellungsbasis", basis_txt, bold_val=True)
    _kv(pdf, "Erstellt am", today_str)
    pdf.ln(4)

    # ── Der Vorteil ───────────────────────────────────────────────────────────
    _section_header(pdf, "Dein geldwerter Vorteil")

    # Hero: immer der errechnete Punktwert, wie auf dem Bildschirm. Die
    # Vorsicht steckt sichtbar im Planungsband und im Deckungs-Badge, nicht in
    # einer stillen Ersetzung der Headline.
    hero_lbl = ("Geldwerter Vorteil pro Jahr" if v.is_projected
                else "Geldwerter Vorteil im Zeitraum")
    _big_kpi(pdf, hero_lbl, f"CHF {_chf(v.total, 0)}",
             _GREEN_CI if v.total >= 0 else _ROT)

    if proj_mode:
        cov = projection.coverage
        bc = _BADGE_COLORS.get(cov.label, _DIM)
        by = pdf.get_y()
        pdf.set_fill_color(*bc)
        pdf.rect(pdf.l_margin, by, 52, 7, style="F")
        pdf.set_font(pdf.fam, "B", 8)
        pdf.set_text_color(*_WHITE)
        pdf.set_xy(pdf.l_margin, by)
        pdf.cell(52, 7, f"{cov.label.value.upper()}  {cov.coverage_pct:.0%}",
                 align="C")
        pdf.set_font(pdf.fam, "", 8.5)
        pdf.set_text_color(*_DIM)
        pdf.set_xy(pdf.l_margin + 55, by)
        band = ""
        if v.band_low is not None:
            band = (f"Planungsband CHF {_chf(v.band_low, 0)} – "
                    f"CHF {_chf(v.band_high, 0)}")
        pdf.cell(0, 7, _safe(band))
        pdf.ln(11)
        pdf.set_text_color(*_ANTHRAZIT)

    # Zerlegung: beim Acquiring SPART der Haendler, beim DCC BEKOMMT er mehr.
    # Die Summe ist exakt der Hero-Wert (Identitaet, siehe engine.py).
    y = _tiles(pdf, pdf.get_y(), [
        (f"Acquiring-Ersparnis {v.suffix}", f"CHF {_chf(v.acquiring, 0)}",
         _GREEN_CI if v.acquiring >= 0 else _ROT),
        (f"DCC-Mehrertrag {v.suffix}", f"CHF {_chf(v.dcc, 0)}",
         _CYAN if v.dcc >= 0 else _ROT),
        (f"Geldwerter Vorteil {v.suffix}", f"CHF {_chf(v.total, 0)}",
         _GREEN_CI if v.total >= 0 else _ROT),
    ], w_total=W)
    pdf.set_xy(pdf.l_margin, y)
    pdf.set_font(pdf.fam, "", 7.5)
    pdf.set_text_color(*_DIM)
    pdf.cell(0, 4, _safe("Acquiring + DCC = geldwerter Vorteil. Beim Acquiring "
                         "sparst du Gebühren, beim DCC bekommst du mehr "
                         "Cashback."))
    pdf.ln(7)

    # ── Woher der Vorteil kommt: zwei Vergleiche ───────────────────────────────
    _section_header(pdf, "Woher der Vorteil kommt")
    y0 = pdf.get_y()
    half = (W - 8) / 2
    _bar_pair(pdf, pdf.l_margin, y0, half, 44,
              "Acquiring-Gebühren (vor Cashback)", v.wl_fee, v.sp_fee, _ROT)
    _bar_pair(pdf, pdf.l_margin + half + 8, y0, half, 44,
              "DCC-Cashback", v.wl_cb, v.sp_cb, _CYAN)
    pdf.set_xy(pdf.l_margin, y0 + 45)
    pdf.set_font(pdf.fam, "", 7.5)
    pdf.set_text_color(*_DIM)
    acq_txt = (f"{_chf(v.acquiring, 0)} gespart" if v.acquiring >= 0
               else f"{_chf(-v.acquiring, 0)} teurer")
    dcc_txt = (f"{_chf(v.dcc, 0)} mehr Cashback" if v.dcc >= 0
               else f"{_chf(-v.dcc, 0)} weniger Cashback")
    pdf.cell(half, 4, _safe(f"CHF {acq_txt}"), border=0)
    pdf.set_x(pdf.l_margin + half + 8)
    pdf.cell(half, 4, _safe(f"CHF {dcc_txt}"), border=0)
    pdf.ln(8)

    # ── Kennzahlen ────────────────────────────────────────────────────────────
    _section_header(pdf, "Kennzahlen")
    chg = v.fee_change_pct
    chg_txt = "0.0 %" if abs(chg) < 0.05 else f"{chg:+.1f} %"
    y = _tiles(pdf, pdf.get_y(), [
        (f"Bruttoumsatz {v.suffix}", f"CHF {_chf(v.brutto, 0)}", _BLUE),
        ("Gebührenveränderung", chg_txt,
         _GREEN_CI if v.rel_pct >= 0 else _ROT, "vs. Aktuell"),
        (f"Transaktionen {v.suffix}", _num(v.n_txn), _CYAN, v.note),
    ], w_total=W)
    if v.wl_rate is not None:
        # Wie auf dem Bildschirm: gross die Gesamtrate (Disagio), klein die
        # ASF -- die einzige Komponente, die SwiPay veraendert (ICF/CSF laufen
        # als Pass-through identisch durch). Worldline nennt sie "Processing
        # Fee"; hier heisst beides ASF, damit der Vergleich lesbar bleibt.
        dlt = v.rate_delta
        wl_foot = (f"Ø ASF {_pct_rate(v.wl_processing_rate)}"
                   if v.wl_processing_rate is not None else "vom Bruttoumsatz")
        sp_foot = "vom Bruttoumsatz"
        if v.asf_rate is not None:
            sp_foot = f"Ø ASF {_pct_rate(v.asf_rate)}"
            achg = v.asf_change_pct
            if achg is not None:
                sp_foot += (" · 0.0 %" if abs(achg) < 0.05
                            else f" · {achg:+.1f} %")
        y = _tiles(pdf, y, [
            ("Gebühren Total Aktuell", _pct_rate(v.wl_rate), _ANTHRAZIT,
             wl_foot),
            ("Gebühren Total SwiPay", _pct_rate(v.sp_rate),
             _GREEN_CI if dlt >= 0 else _ROT, sp_foot),
            ("Ø Transaktionswert", f"CHF {_chf(v.avg_ticket)}", _CYAN,
             "aus dem Ist-Mix"),
        ], w_total=W)
    pdf.set_xy(pdf.l_margin, y)
    pdf.set_font(pdf.fam, "", 7.5)
    pdf.set_text_color(*_DIM)
    extra = f"Aktive Terminals: {n_terminals}. " if n_terminals else ""
    pdf.multi_cell(0, 4, _safe(
        f"{extra}Gebührenraten als Anteil vom Bruttoumsatz, netto nach "
        "DCC-Cashback. Die ASF ist die einzige Komponente, die SwiPay "
        "verändert – Interchange und Scheme Fees laufen unverändert durch. "
        "Die Prozentangabe daneben ist die Veränderung der ASF."))
    pdf.ln(3)

    # ── Seite 2: Aufschluesselung, DCC, Grundlagen ────────────────────────────
    # Fester Umbruch statt Auto-Break: sonst reisst der Umbruch eine Sektion
    # mitten auseinander (die Caption landete allein auf einer leeren Seite).
    pdf.add_page()

    if savings_by_type:
        _section_header(pdf, "Ersparnis nach Kartentyp")
        y = _hbars(pdf, pdf.l_margin, pdf.get_y(), W, savings_by_type)
        pdf.set_draw_color(*_LINE)
        pdf.set_line_width(0.2)
        pdf.line(pdf.l_margin, y + 1, pdf.l_margin + W, y + 1)
        pdf.set_xy(pdf.l_margin, y + 2)
        pdf.set_font(pdf.fam, "B", 8)
        pdf.set_text_color(*_ANTHRAZIT)
        pdf.cell(W - 26, 6, "Summe", border=0)
        pdf.cell(26, 6, f"CHF {_chf(sum(x for _, x in savings_by_type), 0)}",
                 border=0, align="R")
        pdf.ln(8)
        pdf.set_font(pdf.fam, "", 7.5)
        pdf.set_text_color(*_DIM)
        pdf.multi_cell(0, 4, _safe(
            "Nicht offerierbare Brands erscheinen bewusst mit Null-Effekt – "
            "dort ändert SwiPay nichts an deinen Konditionen."))
        pdf.ln(4)

    # ── DCC ───────────────────────────────────────────────────────────────────
    _section_header(pdf, "DCC – Cashback und Ausschöpfung")
    pdf.set_font(pdf.fam, "B", 7.5)
    pdf.set_text_color(*_DIM)
    pdf.cell(0, 4, _safe("AUSSCHÖPFUNG DES DCC-FÄHIGEN FREMDWÄHRUNGSVOLUMENS"))
    pdf.ln(5)
    y = _share_bar(
        pdf, pdf.l_margin, pdf.get_y(), W, v.dcc_share,
        f"Als DCC genutzt: CHF {_chf(v.dcc_vol, 0)}",
        f"Nicht genutzt: CHF {_chf(v.fx_vol - v.dcc_vol, 0)}")

    cb_max = v.cashback_ceiling(dcc_pct)
    # Vierte Kachel spiegelt den Bildschirm (Nutzer-Entscheid 2026-09-17,
    # siehe CLAUDE.md "DCC-Potenzial"): "Unrealisiert" (Obergrenze minus
    # sp_cb) wurde durch "Cashback Aktuell" ersetzt -- wl_cb plus der daraus
    # errechnete Satz auf derselben Kauf-Basis (dcc_vol_purch) wie die
    # SwiPay-Kachel, sonst waeren Zaehler und Nenner nicht vergleichbar.
    wl_rate = (v.wl_cb / v.dcc_vol_purch) if v.dcc_vol_purch else None
    wl_foot = (f"{wl_rate * 100:.2f} % effektiv" if wl_rate is not None
               else "kein DCC-Volumen")
    y = _tiles(pdf, y + 2, [
        (f"Cashback SwiPay {v.suffix}", f"CHF {_chf(v.sp_cb, 0)}", _CYAN,
         f"{dcc_pct * 100:.2f} % auf {v.dcc_share * 100:.1f} % Ausschöpfung"),
        (f"Cashback bei 100 % {v.suffix}", f"CHF {_chf(cb_max, 0)}", _BLUE,
         "theoretische Obergrenze"),
        (f"Cashback Aktuell {v.suffix}", f"CHF {_chf(v.wl_cb, 0)}", _ORANGE,
         wl_foot),
        (f"DCC-Kaufvolumen {v.suffix}", f"CHF {_chf(v.fx_vol_purch, 0)}",
         _ANTHRAZIT, "DCC-fähig, Basis der Obergrenze"),
    ], w_total=W)
    pdf.set_xy(pdf.l_margin, y)
    pdf.set_font(pdf.fam, "", 7.5)
    pdf.set_text_color(*_DIM)
    pdf.multi_cell(0, 4, _safe(
        f"{v.dcc_share * 100:.1f} % des DCC-fähigen Fremdwährungsvolumens "
        f"laufen als DCC: CHF {_chf(v.dcc_vol, 0)} von CHF {_chf(v.fx_vol, 0)} "
        f"({v.note}, {v.suffix}). «Cashback bei 100 %» rechnet den "
        f"SwiPay-Satz von {dcc_pct * 100:.2f} % auf das gesamte DCC-fähige "
        "Kaufvolumen – eine Obergrenze, keine Prognose: volle Ausschöpfung "
        "setzt voraus, dass jeder Karteninhaber DCC annimmt."))
    pdf.ln(4)

    # ── Hochrechnungs-Details ─────────────────────────────────────────────────
    if proj_mode:
        _section_header(pdf, "Grundlage der Hochrechnung")
        cov = projection.coverage
        _kv(pdf, "Hinterlegter Jahresumsatz",
            f"CHF {_chf(getattr(projection, 'annual_volume_total', 0.0), 0)}")
        _kv(pdf, "Deckungsgrad der Datenbasis",
            f"{cov.coverage_pct:.0%} ({cov.label.value})")
        if v.band_low is not None:
            _kv(pdf, "Planungsband (-15 % / +15 %)",
                f"CHF {_chf(v.band_low, 0)} – CHF {_chf(v.band_high, 0)}")
        if portfolio_coverage_pct is not None:
            n_note = (f" ({n_entities_used} von {n_entities_total} "
                      "Merchants/Gruppen)" if n_entities_total else "")
            _kv(pdf, "Portfolio-Abdeckung",
                f"{portfolio_coverage_pct:.0%} des Ist-Umsatzes "
                f"hochgerechnet{n_note}")
        pdf.ln(2)
        if cov.label == CoverageLabel.INDICATIVE:
            pdf.set_font(pdf.fam, "B", 8.5)
            pdf.set_text_color(*_ROT)
            pdf.cell(6, 6, "!")
            pdf.cell(0, 6, f"Datenbasis indikativ (Deckung {cov.coverage_pct:.0%})")
            pdf.ln(5)
            pdf.set_font(pdf.fam, "", 8.5)
            pdf.set_text_color(*_ANTHRAZIT)
            pdf.set_x(pdf.l_margin + 6)
            pdf.multi_cell(0, 5, _safe(
                "Für eine belastbare Hochrechnung werden mindestens 25 % des "
                "Jahresumsatzes als Datengrundlage empfohlen. Der ausgewiesene "
                "Vorteil ist die Punktschätzung; das Planungsband zeigt die "
                "Streuung. Empfehlung: Weitere Monatsdaten einsenden."))
            pdf.ln(3)
        elif cov.label == CoverageLabel.LOW_COVERAGE:
            pdf.set_font(pdf.fam, "I", 8)
            pdf.set_text_color(*_DIM)
            pdf.multi_cell(0, 5, _safe(
                "Datenbasis eingeschränkt (Deckung 25–60 %): Punktschätzung "
                "plausibel, Planungsband beachten."))
            pdf.ln(2)
        pdf.ln(2)

    # ── Kurzbericht / Datenhinweise ───────────────────────────────────────────
    flags_fanout  = fanout_partner_ids or []
    flags_dup_vol = duplicate_volume_warnings or []
    flags_zero    = zero_effect_brands or []
    flags_mix     = mix_hints or []
    cov_lbl = projection.coverage.label if projection else None

    has_flags = bool(flags_fanout or flags_dup_vol or flags_zero or flags_mix)
    if has_flags:
        _section_header(pdf, "Datenhinweise und Besonderheiten")

        for pid in flags_fanout:
            _flag(pdf,
                  f"Datenauffälligkeit (Fan-out): Partner-ID {pid} – "
                  "Vor Angebotsstellung bitte manuell klären.",
                  rgb=_ROT)

        for cluster in flags_dup_vol:
            _flag(pdf,
                  f"Möglicher Fan-out (Hochrechnung): {', '.join(cluster)} haben "
                  "denselben Jahresumsatz hinterlegt – wird dennoch summiert, "
                  "bitte prüfen.",
                  rgb=_AMBER)

        for brand in flags_zero:
            _flag(pdf,
                  f"«{brand}»: kein SwiPay-Angebot – Transaktionen spiegeln "
                  "die Konditionen des aktuellen Anbieters exakt (kein "
                  "Vergleichseffekt).")

        for hint in flags_mix:
            _flag(pdf, hint)

        pdf.ln(3)

    # ── Disclaimer ────────────────────────────────────────────────────────────
    _divider(pdf)
    pdf.set_font(pdf.fam, "I", 7.5)
    pdf.set_text_color(*_FAINT)
    pdf.multi_cell(
        0, 4.5,
        _safe("Alle Angaben ohne Gewähr. Dieser Vergleich basiert auf den "
              "eingereichten Exportdaten und dem gültigen SwiPay "
              "IC++-Angebot. Massgeblich für eine Zusammenarbeit ist "
              "ausschliesslich der unterzeichnete Vertrag."),
        border=0,
    )

    return bytes(pdf.output())


def build_csv(fdf: pd.DataFrame, comp: pd.DataFrame) -> str:
    """Internal detail export: all original Worldline fields plus all engine KPIs."""
    out = fdf.copy()
    for col in comp.columns:
        out[col] = comp[col].values
    # Drop ephemeral helper columns that don't belong in the export
    drop = {"idempotency_key", "_month", "_purch_brutto"}
    out = out.drop(columns=[c for c in drop if c in out.columns], errors="ignore")
    return out.to_csv(index=False, sep=";", decimal=".", encoding="utf-8-sig")
