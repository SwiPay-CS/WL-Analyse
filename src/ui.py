"""
SwiPay corporate-identity design system for the Streamlit app.

Single source of truth for colours, CSS, layout components and Altair chart
styling. Brand reference: SwiPay Brand & CI v2.1.
  Rot #be5e48 (Primär/Akzent) · Dunkelrot #8e312b (Sekundär/Hover)
  Anthrazit #3e4b4c (Fliesstext) · Signal Blue #224f59 (Struktur/Header)
  Signal Cyan #3c8f99 · Signal Green #949f50 (positiv) · Signal Orange #ec6608
Font: Saira (Google Font). Hexagon as the recurring graphic mark.
Swiss conventions: apostrophe thousands, decimal point, «» quotes, no ß.
"""

from __future__ import annotations

import base64
from pathlib import Path

import altair as alt
import pandas as pd
import streamlit as st

# ── Brand palette ─────────────────────────────────────────────────────────────
ROT        = "#be5e48"
DUNKELROT  = "#8e312b"
ANTHRAZIT  = "#3e4b4c"
BLUE       = "#224f59"   # Signal Blue — structure / header
CYAN       = "#3c8f99"   # Signal Cyan — infographics
GREEN      = "#949f50"   # Signal Green — positive results
ORANGE     = "#ec6608"   # Signal Orange — CTA / hint
BG         = "#f4f2ef"
WHITE      = "#ffffff"
LINE       = "#e6e1da"
INK_60     = "rgba(62,75,76,0.62)"
INK_42     = "rgba(62,75,76,0.42)"

_ASSETS = Path(__file__).resolve().parent.parent / "assets"

# Swiss number format ----------------------------------------------------------

def chf(v: float, dec: int = 2) -> str:
    return f"{v:,.{dec}f}".replace(",", "'")

def num(v: float) -> str:
    return f"{int(round(v)):,}".replace(",", "'")

def pid(v) -> str:
    """Clean Partner-ID for display: pandas float-casts whole-number IDs
    (e.g. NaN-safe astype(str) on a numeric column), leaving a trailing
    '.0' that isn't a real decimal. Strip it; leave any other value as-is."""
    s = str(v).strip()
    return s[:-2] if s.endswith(".0") else s

def pct(v: float, dec: int = 2) -> str:
    return f"{v * 100:.{dec}f} %"


def chf_compact(v: float) -> str:
    """Compact CHF for KPI cards: millions as 'Mio.', thousands kept full."""
    a = abs(v)
    if a >= 1_000_000:
        return f"{v / 1_000_000:.2f} Mio."
    return chf(v, 0)


# ── Logo ───────────────────────────────────────────────────────────────────────

def _logo_data_uri() -> str | None:
    """Return a data: URI for the official logo if present in assets/.

    Case-insensitive; matches any *.svg/*.png whose name contains "logo"
    (e.g. SWIPAY-Logo.svg). SVG is preferred over PNG.
    """
    if not _ASSETS.exists():
        return None
    cands = [p for p in _ASSETS.iterdir()
             if p.suffix.lower() in (".svg", ".png") and "logo" in p.stem.lower()]
    cands.sort(key=lambda p: (p.suffix.lower() != ".svg", p.name.lower()))
    if not cands:
        return None
    p = cands[0]
    mime = "image/svg+xml" if p.suffix.lower() == ".svg" else "image/png"
    return f"data:{mime};base64,{base64.b64encode(p.read_bytes()).decode()}"


def _hexagon_mark(size: int = 34) -> str:
    """CI hexagon mark with the brand-red gradient — fallback when no logo file."""
    return f"""
    <svg width="{size}" height="{size}" viewBox="0 0 100 100" aria-hidden="true">
      <defs><linearGradient id="hx" x1="0" y1="0" x2="1" y2="1">
        <stop offset="0%" stop-color="{ROT}"/><stop offset="100%" stop-color="{DUNKELROT}"/>
      </linearGradient></defs>
      <path d="M50 4 L91 27 L91 73 L50 96 L9 73 L9 27 Z" fill="url(#hx)"/>
      <path d="M50 22 L74 36 L74 64 L50 78 L26 64 L26 36 Z" fill="none"
            stroke="rgba(255,255,255,.85)" stroke-width="5"/>
    </svg>"""


def sidebar_brand() -> None:
    """Render the SwiPay lockup at the top of the sidebar (logo file or hexagon)."""
    uri = _logo_data_uri()
    if uri:
        # Official lockup already includes wordmark + claim — show it alone.
        st.markdown(
            f'<div class="sp-brand sp-brand-logo">'
            f'<img src="{uri}" alt="SwiPay – das Bezahlnetzwerk"/></div>',
            unsafe_allow_html=True,
        )
    else:
        st.markdown(
            f"""
            <div class="sp-brand">
              {_hexagon_mark()}
              <div class="sp-brand-txt">
                <div class="sp-wordmark">SwiPay</div>
                <div class="sp-claim">DAS BEZAHLNETZWERK</div>
              </div>
            </div>
            """,
            unsafe_allow_html=True,
        )


# ── Header / page title ─────────────────────────────────────────────────────────

def page_header(title: str, subtitle: str, *, status: str = "Aktiv",
                meta: str = "", chips: list[tuple[str, str]] | None = None) -> None:
    """Dark teal header strip with greeting, status dot and meta line."""
    chip_html = ""
    if chips:
        chip_html = '<div class="sp-hd-chips">' + "".join(
            f'<span class="sp-chip"><b>{v}</b> {k}</span>' for k, v in chips
        ) + "</div>"
    st.markdown(
        f"""
        <div class="sp-header">
          <div class="sp-hd-main">
            <div class="sp-hd-title">{title}</div>
            <div class="sp-hd-sub">{subtitle}</div>
          </div>
          <div class="sp-hd-right">
            <span class="sp-status"><span class="sp-dot"></span>{status}</span>
            <div class="sp-hd-meta">{meta}</div>
          </div>
        </div>
        {chip_html}
        """,
        unsafe_allow_html=True,
    )


# ── KPI cards / hero / pills ──────────────────────────────────────────────────

def hero(label: str, value: str, *, foot: str = "", accent: str = GREEN,
         band: str = "") -> None:
    band_html = f'<div class="sp-hero-band">{band}</div>' if band else ""
    st.markdown(
        f"""
        <div class="sp-hero" style="--acc:{accent}">
          <div class="sp-hero-label">{label}</div>
          <div class="sp-hero-value">{value}</div>
          {band_html}
          <div class="sp-hero-foot">{foot}</div>
        </div>
        """,
        unsafe_allow_html=True,
    )


def kpi_row(cards: list[dict]) -> None:
    """cards: list of {label, value, foot, accent}."""
    cols = st.columns(len(cards))
    for col, c in zip(cols, cards):
        with col:
            st.markdown(
                f"""
                <div class="sp-card" style="--acc:{c.get('accent', CYAN)}">
                  <div class="sp-card-label">{c['label']}</div>
                  <div class="sp-card-value">{c['value']}</div>
                  <div class="sp-card-foot">{c.get('foot', '')}</div>
                </div>
                """,
                unsafe_allow_html=True,
            )


def section(title: str, sub: str = "") -> None:
    sub_html = f'<span class="sp-sec-sub">{sub}</span>' if sub else ""
    st.markdown(
        f'<div class="sp-sec"><span class="sp-sec-bar"></span>'
        f'<span class="sp-sec-title">{title}</span>{sub_html}</div>',
        unsafe_allow_html=True,
    )


def pill(text: str, kind: str = "neutral") -> str:
    return f'<span class="sp-pill sp-pill-{kind}">{text}</span>'


def info_banner(html: str) -> None:
    st.markdown(f'<div class="sp-banner">{html}</div>', unsafe_allow_html=True)


# ── Altair charts (CI-themed) ─────────────────────────────────────────────────

_FONT = "Saira, sans-serif"

# d3-format renders English comma thousands; swap to the Swiss apostrophe.
_SWISS_INT = "replace(format(datum.value, ',.0f'), /,/g, \"'\")"


def _chf_axis(title: str | None) -> alt.Axis:
    return alt.Axis(title=title, labelExpr=_SWISS_INT)


def _base(ch: alt.Chart, height: int = 240) -> alt.Chart:
    return (
        ch.properties(height=height)
        .configure_view(strokeWidth=0)
        .configure_axis(
            labelFont=_FONT, titleFont=_FONT, labelColor=ANTHRAZIT,
            titleColor=INK_60, grid=False, domainColor=LINE, tickColor=LINE,
            labelFontSize=12, titleFontSize=11,
        )
        .configure_legend(labelFont=_FONT, titleFont=_FONT, labelColor=ANTHRAZIT,
                          titleColor=INK_60)
        .configure_axisY(grid=True, gridColor="#efeae3", gridDash=[2, 3])
    )


def chart_fees_compare(wl_net: float, sp_net: float) -> alt.Chart:
    """Headline: Worldline vs SwiPay net fees as two bars."""
    df = pd.DataFrame({
        "Anbieter": ["Worldline", "SwiPay"],
        "Gebühren": [wl_net, sp_net],
    })
    df["lbl"] = df["Gebühren"].map(lambda v: chf(v, 0))
    lo, hi = min(0.0, wl_net, sp_net), max(0.0, wl_net, sp_net)
    pad = (hi - lo) * 0.18 or 1.0
    bars = (
        alt.Chart(df).mark_bar(size=56, cornerRadiusEnd=6)
        .encode(
            x=alt.X("Anbieter:N", title=None, sort=["Worldline", "SwiPay"],
                    scale=alt.Scale(paddingInner=0.5, paddingOuter=0.5)),
            y=alt.Y("Gebühren:Q", axis=_chf_axis("Netto-Gebühren CHF"),
                    scale=alt.Scale(domain=[lo, hi + pad], nice=False)),
            color=alt.Color("Anbieter:N", scale=alt.Scale(
                domain=["Worldline", "SwiPay"], range=[ANTHRAZIT, ROT]), legend=None),
        )
    )
    labels = bars.mark_text(dy=-10, clip=False, font=_FONT, fontWeight="bold",
                            color=ANTHRAZIT, fontSize=13).encode(
        text=alt.Text("lbl:N"))
    return _base(bars + labels)


def chart_savings_by_type(df: pd.DataFrame) -> alt.Chart:
    """df columns: Typ, Ersparnis (wl_net - sp_net per brand type)."""
    df = df.copy().sort_values("Ersparnis")
    df["lbl"] = df["Ersparnis"].map(lambda v: chf(v, 0))
    order = df["Typ"].tolist()
    lo, hi = min(0.0, df["Ersparnis"].min()), max(0.0, df["Ersparnis"].max())
    pad = (hi - lo) * 0.24 or 1.0
    yenc = alt.Y("Typ:N", title=None, sort=order,
                 scale=alt.Scale(paddingInner=0.45, paddingOuter=0.35))
    base = alt.Chart(df)
    bars = base.mark_bar(cornerRadiusEnd=6).encode(
        y=yenc,
        x=alt.X("Ersparnis:Q", axis=_chf_axis("Ersparnis CHF"),
                scale=alt.Scale(domain=[lo - pad, hi + pad], nice=False)),
        color=alt.condition(alt.datum.Ersparnis >= 0,
                            alt.value(GREEN), alt.value(ROT)),
    )
    # Labels outside the bar end: right for positive, left for negative.
    txt = dict(font=_FONT, fontSize=12, color=ANTHRAZIT, clip=False)
    pos = base.transform_filter(alt.datum.Ersparnis >= 0).mark_text(
        align="left", dx=6, **txt).encode(y=yenc, x="Ersparnis:Q", text="lbl:N")
    neg = base.transform_filter(alt.datum.Ersparnis < 0).mark_text(
        align="right", dx=-6, **txt).encode(y=yenc, x="Ersparnis:Q", text="lbl:N")
    return _base(bars + pos + neg, height=210)


def chart_dcc_compare(wl_cb: float, sp_cb: float) -> alt.Chart:
    df = pd.DataFrame({"Anbieter": ["Worldline", "SwiPay"], "Cashback": [wl_cb, sp_cb]})
    df["lbl"] = df["Cashback"].map(lambda v: chf(v, 0))
    hi = max(0.0, wl_cb, sp_cb)
    bars = (
        alt.Chart(df).mark_bar(size=56, cornerRadiusEnd=6)
        .encode(
            x=alt.X("Anbieter:N", title=None, sort=["Worldline", "SwiPay"],
                    scale=alt.Scale(paddingInner=0.5, paddingOuter=0.5)),
            y=alt.Y("Cashback:Q", axis=_chf_axis("DCC-Cashback CHF"),
                    scale=alt.Scale(domain=[0, hi * 1.18 or 1.0], nice=False)),
            color=alt.Color("Anbieter:N", scale=alt.Scale(
                domain=["Worldline", "SwiPay"], range=[ANTHRAZIT, CYAN]), legend=None),
        )
    )
    labels = bars.mark_text(dy=-10, clip=False, font=_FONT, fontWeight="bold",
                            color=ANTHRAZIT, fontSize=13).encode(
        text=alt.Text("lbl:N"))
    return _base(bars + labels)


def chart_dcc_potential(used: float, fx_total: float) -> alt.Chart:
    """Horizontal stacked bar: genutztes DCC-Volumen vs. ungenutztes Potenzial."""
    rest = max(fx_total - used, 0.0)
    df = pd.DataFrame({
        "Segment": ["Genutzt (DCC)", "Potenzial (übrig)"],
        "Volumen": [used, rest],
        "order": [0, 1],
    })
    bar = (
        alt.Chart(df).mark_bar(height=46, cornerRadius=4)
        .encode(
            x=alt.X("Volumen:Q", stack="zero", axis=alt.Axis(
                title="Fremdwährungsvolumen CHF", tickCount=4,
                labelExpr="format(datum.value / 1000000, '.0f') + ' Mio.'")),
            color=alt.Color("Segment:N", scale=alt.Scale(
                domain=["Genutzt (DCC)", "Potenzial (übrig)"], range=[CYAN, "#d9d2c9"]),
                legend=alt.Legend(orient="bottom", title=None, labelLimit=200)),
            order=alt.Order("order:Q"),
        )
    )
    return _base(bar, height=170)


def chart_monthly(df: pd.DataFrame) -> alt.Chart:
    """df columns: Monat, WL, SP (net fees per month)."""
    long = df.melt("Monat", value_vars=["WL", "SP"], var_name="Anbieter",
                   value_name="Gebühren")
    line = (
        alt.Chart(long).mark_line(point=True, strokeWidth=3)
        .encode(
            x=alt.X("Monat:N", title=None),
            y=alt.Y("Gebühren:Q", axis=_chf_axis("Gebühren CHF")),
            color=alt.Color("Anbieter:N", scale=alt.Scale(
                domain=["WL", "SP"], range=[ANTHRAZIT, ROT]),
                legend=alt.Legend(orient="top", title=None)),
        )
    )
    return _base(line, height=240)


def chart_volume_monthly(df: pd.DataFrame) -> alt.Chart:
    """df columns: Monat, Umsatz."""
    bars = (
        alt.Chart(df).mark_bar(cornerRadiusEnd=4, color=BLUE, opacity=0.85)
        .encode(
            x=alt.X("Monat:N", title=None),
            y=alt.Y("Umsatz:Q", axis=_chf_axis("Umsatz CHF")),
        )
    )
    return _base(bars, height=200)


def chart_hist(df: pd.DataFrame) -> alt.Chart:
    """df columns: Bucket, Anzahl."""
    bars = (
        alt.Chart(df).mark_bar(cornerRadiusEnd=4, color=CYAN)
        .encode(
            x=alt.X("Bucket:N", title="Transaktionsgrösse CHF", sort=None),
            y=alt.Y("Anzahl:Q", axis=_chf_axis("Anzahl")),
        )
    )
    return _base(bars, height=220)


def chart_region(df: pd.DataFrame) -> alt.Chart:
    """df columns: Region, Umsatz."""
    bars = (
        alt.Chart(df).mark_bar(cornerRadiusEnd=4)
        .encode(
            y=alt.Y("Region:N", title=None, sort="-x"),
            x=alt.X("Umsatz:Q", axis=_chf_axis("Umsatz CHF")),
            color=alt.condition(alt.datum.Region == "DOMESTIC",
                                alt.value(ANTHRAZIT), alt.value(CYAN)),
        )
    )
    return _base(bars, height=200)


# ── Global CSS ────────────────────────────────────────────────────────────────

def inject_css() -> None:
    st.markdown(f"""
    <style>
    @import url('https://fonts.googleapis.com/css2?family=Saira:wght@400;500;600;700;800&display=swap');

    :root {{
      --rot:{ROT}; --dunkelrot:{DUNKELROT}; --anthrazit:{ANTHRAZIT};
      --blue:{BLUE}; --cyan:{CYAN}; --green:{GREEN}; --orange:{ORANGE};
      --bg:{BG}; --line:{LINE};
    }}

    html, body, [class*="css"], .stApp, button, input, textarea, select {{
      font-family: 'Saira', sans-serif !important;
    }}
    .stApp {{ background: var(--bg); }}

    /* Hide Streamlit chrome */
    #MainMenu, header[data-testid="stHeader"], footer {{ visibility: hidden; height:0; }}
    .block-container {{ padding-top: 1.2rem; padding-bottom: 3rem; max-width: 1300px; }}

    h1,h2,h3,h4 {{ font-family:'Saira',sans-serif !important; color:var(--anthrazit);
      font-weight:800; letter-spacing:-.01em; }}

    /* ── Sidebar ── */
    section[data-testid="stSidebar"] {{ background:{WHITE}; border-right:1px solid var(--line); }}
    section[data-testid="stSidebar"] .block-container {{ padding-top:1.1rem; }}
    .sp-brand {{ display:flex; align-items:center; gap:.7rem; padding:.2rem .2rem 1rem;
      border-bottom:1px solid var(--line); margin-bottom:1rem; }}
    .sp-brand-logo {{ padding:.4rem .2rem 1.1rem; }}
    .sp-brand-logo img {{ width:100%; max-width:188px; height:auto; }}
    .sp-wordmark {{ font-size:1.5rem; font-weight:800; color:var(--anthrazit);
      line-height:1; letter-spacing:-.02em; }}
    .sp-claim {{ font-size:.6rem; font-weight:600; letter-spacing:.18em;
      color:var(--rot); margin-top:.18rem; }}

    /* Sidebar radio -> nav list */
    section[data-testid="stSidebar"] div[role="radiogroup"] {{ gap:.18rem; }}
    section[data-testid="stSidebar"] div[role="radiogroup"] label {{
      display:flex; align-items:center; padding:.55rem .8rem; border-radius:10px;
      cursor:pointer; color:var(--anthrazit); font-weight:600; font-size:.97rem;
      transition:background .15s,color .15s; border:1px solid transparent; }}
    section[data-testid="stSidebar"] div[role="radiogroup"] label:hover {{
      background:#f4f2ef; }}
    section[data-testid="stSidebar"] div[role="radiogroup"] label > div:first-child {{
      display:none; }}  /* hide radio circle */
    section[data-testid="stSidebar"] div[role="radiogroup"] label:has(input:checked) {{
      background:rgba(60,143,153,.12); color:var(--blue); border-color:rgba(60,143,153,.25); }}

    /* ── Header strip ── */
    .sp-header {{ background:linear-gradient(100deg,{BLUE},{ANTHRAZIT});
      border-radius:16px; padding:1.15rem 1.5rem; color:{WHITE};
      display:flex; justify-content:space-between; align-items:center;
      box-shadow:0 6px 24px rgba(34,79,89,.18); margin-bottom:1rem; }}
    .sp-hd-title {{ font-size:1.5rem; font-weight:800; line-height:1.1; }}
    .sp-hd-sub {{ font-size:.92rem; opacity:.82; margin-top:.18rem; }}
    .sp-hd-right {{ text-align:right; }}
    .sp-status {{ display:inline-flex; align-items:center; gap:.4rem;
      background:rgba(255,255,255,.12); padding:.3rem .7rem; border-radius:999px;
      font-size:.82rem; font-weight:600; }}
    .sp-dot {{ width:8px; height:8px; border-radius:50%; background:{GREEN};
      box-shadow:0 0 0 3px rgba(148,159,80,.3); }}
    .sp-hd-meta {{ font-size:.72rem; opacity:.6; margin-top:.5rem; letter-spacing:.02em; }}
    .sp-hd-chips {{ display:flex; gap:.6rem; margin:-.3rem 0 1rem; flex-wrap:wrap; }}
    .sp-chip {{ background:{WHITE}; border:1px solid var(--line); border-radius:999px;
      padding:.35rem .85rem; font-size:.82rem; color:var(--anthrazit); }}
    .sp-chip b {{ color:var(--rot); }}

    /* ── Hero ── */
    .sp-hero {{ background:{WHITE}; border:1px solid var(--line); border-left:5px solid var(--acc);
      border-radius:16px; padding:1.5rem 1.7rem; box-shadow:0 2px 10px rgba(62,75,76,.04); }}
    .sp-hero-label {{ font-size:.74rem; font-weight:700; letter-spacing:.08em;
      text-transform:uppercase; color:{INK_60}; white-space:nowrap; }}
    .sp-hero-value {{ font-size:2.6rem; font-weight:800; color:var(--acc);
      line-height:1.05; margin:.3rem 0; white-space:nowrap;
      font-variant-numeric:tabular-nums; letter-spacing:-.02em; }}
    .sp-hero-band {{ font-size:.95rem; color:var(--anthrazit); font-weight:600; }}
    .sp-hero-foot {{ font-size:.82rem; color:{INK_60}; margin-top:.3rem; }}

    /* ── KPI cards ── */
    .sp-card {{ background:{WHITE}; border:1px solid var(--line); border-radius:14px;
      padding:1.05rem 1.2rem; box-shadow:0 2px 10px rgba(62,75,76,.04);
      position:relative; overflow:hidden; height:100%; }}
    .sp-card::after {{ content:""; position:absolute; top:0; right:0; width:4px;
      height:100%; background:var(--acc); opacity:.85; }}
    .sp-card-label {{ font-size:.67rem; font-weight:700; letter-spacing:.05em;
      text-transform:uppercase; color:{INK_60}; white-space:nowrap;
      overflow:hidden; text-overflow:ellipsis; }}
    .sp-card-value {{ font-size:1.45rem; font-weight:800; color:var(--anthrazit);
      line-height:1.15; margin:.25rem 0 .1rem;
      font-variant-numeric:tabular-nums; letter-spacing:-.01em; }}
    .sp-card-foot {{ font-size:.76rem; color:{INK_60}; }}

    /* ── Section heading ── */
    .sp-sec {{ display:flex; align-items:center; gap:.6rem; margin:1.6rem 0 .8rem; }}
    .sp-sec-bar {{ width:4px; height:20px; background:var(--rot); border-radius:2px; }}
    .sp-sec-title {{ font-size:1.15rem; font-weight:800; color:var(--anthrazit); }}
    .sp-sec-sub {{ font-size:.84rem; color:{INK_60}; }}

    /* ── Pills ── */
    .sp-pill {{ display:inline-block; padding:.18rem .6rem; border-radius:999px;
      font-size:.78rem; font-weight:600; }}
    .sp-pill-pos {{ background:rgba(148,159,80,.16); color:#6f7a32; }}
    .sp-pill-neg {{ background:rgba(190,94,72,.16); color:var(--dunkelrot); }}
    .sp-pill-neutral {{ background:rgba(62,75,76,.1); color:var(--anthrazit); }}
    .sp-pill-warn {{ background:rgba(236,102,8,.16); color:#b44e05; }}

    /* ── Banner ── */
    .sp-banner {{ background:rgba(60,143,153,.08); border:1px solid rgba(60,143,153,.25);
      border-radius:12px; padding:.9rem 1.1rem; color:var(--anthrazit); font-size:.9rem;
      margin-bottom:1rem; }}

    /* Cards/containers for native widgets */
    div[data-testid="stMetric"] {{ background:{WHITE}; border:1px solid var(--line);
      border-radius:14px; padding:1rem 1.1rem; }}
    div[data-testid="stMetricValue"] {{ color:var(--anthrazit); font-weight:800; }}
    .stButton button[kind="primary"] {{ background:var(--rot); border:none;
      border-radius:10px; font-weight:700; }}
    .stButton button[kind="primary"]:hover {{ background:var(--dunkelrot); }}
    div[data-testid="stDataFrame"] {{ border:1px solid var(--line); border-radius:12px; }}

    /* ── Tabs styled as a button row; last tab pinned right + red (Reset) ── */
    .stTabs [data-baseweb="tab-list"] {{ gap:.4rem; border-bottom:1px solid var(--line);
      padding-bottom:.6rem; }}
    .stTabs [data-baseweb="tab-highlight"], .stTabs [data-baseweb="tab-border"] {{
      display:none; }}
    .stTabs [data-baseweb="tab"] {{ height:auto; background:{WHITE};
      border:1px solid var(--line); border-radius:10px; padding:.55rem 1.1rem;
      font-weight:700; font-size:.92rem; color:var(--anthrazit); transition:background .15s; }}
    .stTabs [data-baseweb="tab"]:hover {{ background:#f4f2ef; }}
    .stTabs [data-baseweb="tab"][aria-selected="true"] {{
      background:rgba(60,143,153,.12); color:var(--blue); border-color:rgba(60,143,153,.25); }}
    /* :last-of-type, not :last-child — baseweb appends a hidden tab-highlight
       <div> after the last <button>, which would otherwise win :last-child.
       Scoped to .st-key-settings_tabs so it hits only the Einstellungen top
       tabs (Reset), not every nested st.tabs() elsewhere on the page. */
    .st-key-settings_tabs [data-baseweb="tab-list"] button[data-baseweb="tab"]:last-of-type {{
      margin-left:auto; background:var(--rot); color:{WHITE}; border-color:var(--rot); }}
    .st-key-settings_tabs [data-baseweb="tab-list"] button[data-baseweb="tab"]:last-of-type:hover {{
      background:var(--dunkelrot); }}
    .st-key-settings_tabs [data-baseweb="tab-list"] button[data-baseweb="tab"]:last-of-type[aria-selected="true"] {{
      color:{WHITE}; }}
    /* Undo the red/pinned rule for the nested Merchants sub-tabs (Gruppieren /
       Hochrechnung) — they inherit the .st-key-settings_tabs ancestor match
       but "Hochrechnung" is not a reset action. */
    .st-key-merchant_subtabs [data-baseweb="tab-list"] button[data-baseweb="tab"]:last-of-type {{
      margin-left:0; background:{WHITE}; color:var(--anthrazit); border-color:var(--line); }}
    .st-key-merchant_subtabs [data-baseweb="tab-list"] button[data-baseweb="tab"]:last-of-type:hover {{
      background:#f4f2ef; }}
    .st-key-merchant_subtabs [data-baseweb="tab-list"] button[data-baseweb="tab"]:last-of-type[aria-selected="true"] {{
      background:rgba(60,143,153,.12); color:var(--blue); }}
    </style>
    """, unsafe_allow_html=True)
