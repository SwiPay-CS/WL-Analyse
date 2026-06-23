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

import pandas as pd
from fpdf import FPDF

from projection import CoverageLabel, ProjectionResult


# ── CI palette ────────────────────────────────────────────────────────────────
_ROT        = (190,  94,  72)   # #be5e48  primary brand
_DUNKELROT  = (142,  49,  43)   # #8e312b
_ANTHRAZIT  = ( 62,  75,  76)   # #3e4b4c  header / body text
_CYAN       = ( 60, 143, 153)   # #3c8f99
_GREEN_CI   = (148, 159,  80)   # #949f50
_AMBER      = (200, 150,  50)
_BG         = (244, 242, 239)   # #f4f2ef
_LINE       = (227, 221, 214)   # #e3ddd6
_WHITE      = (255, 255, 255)
_DIM        = (138, 148, 149)   # ≈ rgba(62,75,76,.62)
_FAINT      = (170, 175, 176)   # ≈ rgba(62,75,76,.42)

_BADGE_COLORS = {
    CoverageLabel.SIMULATABLE:  _GREEN_CI,
    CoverageLabel.LOW_COVERAGE: _AMBER,
    CoverageLabel.INDICATIVE:   _ROT,
}


def _chf(v: float, dec: int = 2) -> str:
    return f"{v:,.{dec}f}".replace(",", "'")


def _safe(text: str) -> str:
    """Sanitize string to Latin-1 for Helvetica (fpdf2 built-in font)."""
    return (
        text
        .replace("–", "-")   # en-dash
        .replace("—", "-")   # em-dash
        .replace("’", "'")   # right single quote
        .replace("“", '"').replace("”", '"')
        .replace("…", "...")  # ellipsis
        .replace("•", "*")   # bullet
    )


# ── PDF base class ────────────────────────────────────────────────────────────

class _SwiPayPDF(FPDF):
    """FPDF subclass with SwiPay CI header and footer."""

    generated_date: str = ""

    def header(self) -> None:
        # Dark full-width header bar
        self.set_fill_color(*_ANTHRAZIT)
        self.rect(0, 0, 210, 22, style="F")
        # "SwiPay" wordmark
        self.set_font("Helvetica", "B", 15)
        self.set_text_color(*_WHITE)
        self.set_xy(12, 5.5)
        self.cell(38, 10, "SwiPay", border=0)
        # Brand-red separator dot
        self.set_font("Helvetica", "", 14)
        self.set_text_color(*_ROT)
        self.set_xy(49, 5.5)
        self.cell(5, 10, "·", border=0)
        # Report subtitle
        self.set_font("Helvetica", "", 9)
        self.set_text_color(185, 190, 191)
        self.set_xy(55, 7)
        self.cell(90, 8, "Worldline-Konditionenvergleich", border=0)
        # Date right-aligned
        self.set_font("Helvetica", "", 7.5)
        self.set_text_color(160, 165, 165)
        self.set_xy(130, 8)
        self.cell(68, 6, self.generated_date, border=0, align="R")
        self.ln(22)

    def footer(self) -> None:
        self.set_y(-14)
        self.set_draw_color(*_LINE)
        self.set_line_width(0.25)
        self.line(12, self.get_y(), 198, self.get_y())
        self.set_font("Helvetica", "", 7)
        self.set_text_color(*_FAINT)
        self.cell(0, 8,
                  "SwiPay AG  ·  Erstellt für den internen Gebrauch  ·  "
                  "Alle Angaben ohne Gewähr",
                  border=0, align="C")
        self.set_xy(12, self.get_y() - 8)
        self.set_font("Helvetica", "", 7)
        self.cell(0, 8, f"Seite {self.page_no()}", border=0, align="R")


# ── Layout helpers ────────────────────────────────────────────────────────────

def _section_header(pdf: _SwiPayPDF, title: str) -> None:
    """Brand-red section title with rule below."""
    pdf.set_font("Helvetica", "B", 8.5)
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
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(*_DIM)
    pdf.cell(95, 6, _safe(label), border=0)
    pdf.set_font("Helvetica", "B" if bold_val else "", 9)
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
    pdf.set_font("Helvetica", "", 9)
    pdf.set_text_color(*_DIM)
    pdf.cell(95, 8, label, border=0)
    pdf.set_font("Helvetica", "B", 13)
    pdf.set_text_color(*rgb)
    pdf.cell(91, 8, value, border=0, align="R")
    pdf.ln(8)


def _flag(pdf: _SwiPayPDF, text: str, rgb: tuple = _DIM) -> None:
    """Bulleted flag / hint line."""
    pdf.set_font("Helvetica", "", 8.5)
    pdf.set_text_color(*_DIM)
    pdf.cell(5, 6, "-", border=0)
    pdf.set_text_color(*rgb)
    pdf.multi_cell(181, 5.5, _safe(text), border=0)


# ── Public API ────────────────────────────────────────────────────────────────

def build_pdf(
    *,
    partner_name: str,
    period_from: str,
    period_to: str,
    brutto: float,
    n_txn: int,
    n_terminals: int,
    avg_ticket: float,
    wl_net: float,
    sp_net: float,
    wl_cashback: float,
    sp_cashback: float,
    saving: float,
    dcc_advantage: float,
    dcc_pct: float,
    projection: ProjectionResult | None = None,
    annual_volume: float = 0.0,
    fanout_partner_ids: list[str] | None = None,
    zero_effect_brands: list[str] | None = None,
    mix_hints: list[str] | None = None,
    generated_date: str | None = None,
) -> bytes:
    """Render the customer PDF and return raw bytes."""
    today_str = generated_date or date.today().strftime("%d.%m.%Y")

    pdf = _SwiPayPDF(format="A4")
    pdf.generated_date = today_str
    pdf.set_margins(12, 12, 12)
    pdf.set_auto_page_break(auto=True, margin=18)
    pdf.add_page()

    # ── Rahmendaten ───────────────────────────────────────────────────────────
    _section_header(pdf, "Rahmendaten")
    _kv(pdf, "Partner", partner_name)
    _kv(pdf, "Auswertungszeitraum", f"{period_from} - {period_to}")
    _kv(pdf, "Erstellt am", today_str)
    pdf.ln(5)

    # ── Zusammenfassung ───────────────────────────────────────────────────────
    _section_header(pdf, "Zusammenfassung")
    _kv(pdf, "Bruttoumsatz (Käufe)", f"CHF {_chf(brutto)}")
    _kv(pdf, "Transaktionen gesamt", f"{n_txn:,}".replace(",", "'"))
    _kv(pdf, "Aktive Terminals", str(n_terminals))
    _kv(pdf, "Ø Transaktionswert", f"CHF {_chf(avg_ticket)}")
    pdf.ln(3)

    _kv(pdf, "Worldline-Gebühren (netto)", f"CHF {_chf(wl_net)}")
    _kv(pdf, "SwiPay-Gebühren (netto)", f"CHF {_chf(sp_net)}")
    _divider(pdf)

    saving_color = _GREEN_CI if saving >= 0 else _ROT
    _big_kpi(pdf, "Ersparnis gegenüber Worldline", f"CHF {_chf(saving)}", saving_color)

    if wl_net != 0:
        pct = saving / abs(wl_net) * 100
        _kv(pdf, "Relative Gebührenreduktion", f"{pct:.1f} %")
    pdf.ln(2)

    _kv(pdf, f"DCC-Cashback Worldline", f"CHF {_chf(wl_cashback)}")
    _kv(pdf, f"DCC-Cashback SwiPay ({dcc_pct * 100:.2f} %)", f"CHF {_chf(sp_cashback)}")
    dcc_color = _CYAN if dcc_advantage >= 0 else _ROT
    _big_kpi(pdf, "DCC-Vorteil SwiPay", f"CHF {_chf(dcc_advantage)}", dcc_color)

    # ── Hochrechnung ──────────────────────────────────────────────────────────
    if projection is not None and annual_volume > 0:
        _section_header(pdf, "Hochrechnung auf Jahresbasis")
        proj = projection
        cov  = proj.coverage

        # Coverage badge (filled rect)
        bc = _BADGE_COLORS.get(cov.label, _DIM)
        pdf.set_fill_color(*bc)
        pdf.set_font("Helvetica", "B", 8)
        pdf.set_text_color(*_WHITE)
        bx, by = pdf.get_x(), pdf.get_y()
        pdf.rect(pdf.l_margin, by, 52, 7, style="F")
        pdf.set_xy(pdf.l_margin, by)
        pdf.cell(52, 7, f"{cov.label.value.upper()}  {cov.coverage_pct:.0%}", align="C")
        pdf.set_font("Helvetica", "", 8.5)
        pdf.set_text_color(*_DIM)
        pdf.set_xy(pdf.l_margin + 55, by)
        pdf.cell(0, 7, f"Jahresumsatz-Ziel: CHF {_chf(annual_volume)}")
        pdf.ln(10)
        pdf.set_text_color(*_ANTHRAZIT)

        headline = (proj.band_low if cov.label == CoverageLabel.INDICATIVE
                    else proj.saving_annual)
        if cov.label == CoverageLabel.INDICATIVE:
            _kv(pdf, "Headline (konservatives Ende)", f"CHF {_chf(headline)}", bold_val=True)
        _kv(pdf, "Punktschätzung Ersparnis p.a.", f"CHF {_chf(proj.saving_annual)}")
        _kv(pdf, "Planungsband (-15 % / +15 %)",
            f"CHF {_chf(proj.band_low)} - CHF {_chf(proj.band_high)}")
        _kv(pdf, "DCC-Vorteil p.a.", f"CHF {_chf(proj.dcc_advantage_annual)}", bold_val=True)
        pdf.ln(3)

        if cov.label == CoverageLabel.INDICATIVE:
            pdf.set_font("Helvetica", "B", 8.5)
            pdf.set_text_color(*_ROT)
            pdf.cell(6, 6, "!")
            pdf.cell(0, 6, f"Datenbasis indikativ (Deckung {cov.coverage_pct:.0%})")
            pdf.ln(5)
            pdf.set_font("Helvetica", "", 8.5)
            pdf.set_text_color(*_ANTHRAZIT)
            pdf.set_x(pdf.l_margin + 6)
            pdf.multi_cell(
                0, 5,
                "Für eine belastbare Hochrechnung werden mindestens 25 % des Jahresumsatzes "
                "als Datengrundlage empfohlen. Das konservative Ende des Planungsbands "
                f"(CHF {_chf(proj.band_low)}) dient als Headline. "
                "Empfehlung: Weitere Monatsdaten einsenden.",
                border=0,
            )
            pdf.ln(4)
        elif cov.label == CoverageLabel.LOW_COVERAGE:
            pdf.set_font("Helvetica", "I", 8)
            pdf.set_text_color(*_DIM)
            pdf.set_x(pdf.l_margin)
            pdf.multi_cell(
                0, 5,
                "Datenbasis eingeschränkt (Deckung 25-60 %): "
                "Punktschätzung plausibel, Planungsband beachten.",
                border=0,
            )
            pdf.ln(2)
        pdf.ln(3)

    # ── Kurzbericht / Datenhinweise ───────────────────────────────────────────
    flags_fanout   = fanout_partner_ids or []
    flags_zero     = zero_effect_brands or []
    flags_mix      = mix_hints or []
    cov_lbl        = projection.coverage.label if projection else None

    has_flags = bool(flags_fanout or flags_zero or flags_mix
                     or cov_lbl in (CoverageLabel.LOW_COVERAGE, CoverageLabel.INDICATIVE))

    if has_flags:
        _section_header(pdf, "Datenhinweise und Besonderheiten")

        if cov_lbl == CoverageLabel.INDICATIVE and projection:
            pct = projection.coverage.coverage_pct
            _flag(pdf,
                  f"Deckungsgrad {pct:.0%} – Ergebnisse indikativ. "
                  "Weitere Monatsdaten werden für eine vollständige Simulation benötigt.",
                  rgb=_ROT)
        elif cov_lbl == CoverageLabel.LOW_COVERAGE and projection:
            pct = projection.coverage.coverage_pct
            _flag(pdf,
                  f"Deckungsgrad {pct:.0%} – Datenbasis eingeschränkt (25–60 %).",
                  rgb=_AMBER)

        for pid in flags_fanout:
            _flag(pdf,
                  f"Datenauffälligkeit (Fan-out): Partner-ID {pid} – "
                  "Vor Angebotsstellung bitte manuell klären.",
                  rgb=_ROT)

        for brand in flags_zero:
            _flag(pdf,
                  f"«{brand}»: kein SwiPay-Angebot – Transaktionen spiegeln "
                  "Worldline-Konditionen exakt (kein Vergleichseffekt).")

        for hint in flags_mix:
            _flag(pdf, hint)

        pdf.ln(4)

    # ── Disclaimer ────────────────────────────────────────────────────────────
    _divider(pdf)
    pdf.set_font("Helvetica", "I", 7.5)
    pdf.set_text_color(*_FAINT)
    pdf.multi_cell(
        0, 4.5,
        "Alle Angaben ohne Gewähr. Dieser Vergleich basiert auf den eingereichten "
        "Worldline-Exportdaten und dem gültigen SwiPay IC++-Angebot. "
        "Massgeblich für eine Zusammenarbeit ist ausschliesslich der unterzeichnete Vertrag.",
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
