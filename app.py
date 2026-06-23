"""
SwiPay Worldline-Vergleichstool — Streamlit-Oberfläche (Phase 4).
Start: uv run streamlit run app.py
"""

from __future__ import annotations

import os
import sys
import tempfile
from pathlib import Path

# Make src/ importable regardless of working directory.
sys.path.insert(0, str(Path(__file__).parent / "src"))

import pandas as pd
import streamlit as st

from engine import BrandParams, ParamTable, Offer
from pipeline import run_comparison, totals as engine_totals
from ingest import ingest_files, init_db
from projection import project_tier_b, CoverageLabel
from db_groups import init_groups_db, get_groups, assign_group, unassign_group
from reporter import build_pdf, build_csv

# ── Page setup ───────────────────────────────────────────────────────────────
st.set_page_config(
    page_title="SwiPay Worldline-Vergleich",
    layout="wide",
    initial_sidebar_state="expanded",
)

# ── Paths ────────────────────────────────────────────────────────────────────
ROOT    = Path(__file__).parent
DB_PATH = str(ROOT / "data" / "swipay.db")
(ROOT / "data").mkdir(exist_ok=True)

# ── Defaults ─────────────────────────────────────────────────────────────────
_DEFAULT_CATS: dict[str, dict] = {
    "Debit":      {"asf_pct": 0.11, "min_fee": 0.10},
    "Credit":     {"asf_pct": 0.16, "min_fee": 0.12},
    "Commercial": {"asf_pct": 0.20, "min_fee": 0.12},
}
_DEFAULT_DCC_PCT = 1.40   # displayed as %, divided by 100 before engine

OFFERABLE = frozenset({
    "VisaDebit", "Debit Mastercard", "Mastercard", "Visa", "Maestro",
    "Maestro-CH", "V PAY", "Diners/Discover", "Union Pay",
})

# Transaction-size histogram (CHF) — 6 buckets
HIST_BINS   = [0, 10, 50, 100, 200, 500, float("inf")]
HIST_LABELS = ["0–10", "10–50", "50–100", "100–200", "200–500", "500+"]

# ── Helpers ───────────────────────────────────────────────────────────────────

def chf(v: float, dec: int = 2) -> str:
    """Swiss number format: apostrophe thousands, dot decimal. e.g. 1'234.56"""
    return f"{v:,.{dec}f}".replace(",", "'")


def _int_fmt(n: int) -> str:
    return f"{n:,}".replace(",", "'")


def _fmt_df(df_in: pd.DataFrame, money: list[str], counts: list[str] = []) -> pd.DataFrame:
    """Pre-format numeric columns as strings for Swiss-format display."""
    out = df_in.copy()
    for c in money:
        if c in out.columns:
            out[c] = out[c].apply(lambda x: chf(float(x)) if pd.notna(x) else "")
    for c in counts:
        if c in out.columns:
            out[c] = out[c].apply(lambda x: _int_fmt(int(x)) if pd.notna(x) else "")
    return out

# ── DB init ───────────────────────────────────────────────────────────────────
init_db(DB_PATH)
init_groups_db(DB_PATH)

# ── Session state ─────────────────────────────────────────────────────────────
for _k, _v in [("df", pd.DataFrame()), ("report", None)]:
    if _k not in st.session_state:
        st.session_state[_k] = _v

# ─────────────────────────────────────────────────────────────────────────────
# SIDEBAR
# ─────────────────────────────────────────────────────────────────────────────
with st.sidebar:
    st.title("SwiPay Vergleich")

    # ── Upload ───────────────────────────────────────────────────────────────
    st.subheader("Daten laden")
    uploaded = st.file_uploader(
        "Worldline-Exporte (XLSB / CSV)",
        type=["xlsb", "csv"],
        accept_multiple_files=True,
    )
    sheet_val = st.text_input("Sheet-Name (XLSB, leer = erstes Sheet)", value="WL")

    if uploaded and st.button("Laden & prüfen", type="primary"):
        tmp_paths: list[str] = []
        for f in uploaded:
            fd, p = tempfile.mkstemp(suffix=Path(f.name).suffix)
            with os.fdopen(fd, "wb") as fh:
                fh.write(f.read())
            tmp_paths.append(p)
        try:
            sheet = sheet_val.strip() or None
            df_new, rpt = ingest_files(tmp_paths, DB_PATH, sheet=sheet)
            st.session_state.df     = df_new
            st.session_state.report = rpt
        except Exception as exc:
            st.error(f"Fehler beim Laden: {exc}")
        finally:
            for p in tmp_paths:
                try:
                    os.unlink(p)
                except OSError:
                    pass

    # ── Parameters ───────────────────────────────────────────────────────────
    st.subheader("Parameter")

    dcc_input = st.number_input(
        "SwiPay DCC-Satz (%)",
        min_value=0.0, max_value=5.0,
        value=_DEFAULT_DCC_PCT,
        step=0.05, format="%.2f",
    )
    dcc_pct = dcc_input / 100.0

    cat_params: dict[str, BrandParams] = {}
    for cat, defs in _DEFAULT_CATS.items():
        with st.expander(f"ASF {cat}", expanded=False):
            ap = st.number_input(
                f"ASF % ({cat})",
                min_value=0.0, max_value=2.0,
                value=defs["asf_pct"],
                step=0.01, format="%.2f",
                key=f"asf_{cat}",
            ) / 100.0
            mf = st.number_input(
                f"Mindestgebühr ({cat}) CHF",
                min_value=0.0, value=defs["min_fee"],
                step=0.01, format="%.2f",
                key=f"mf_{cat}",
            )
            cat_params[cat] = BrandParams(asf_pct=ap, min_fee=mf)

    params = ParamTable(cat_params, fallback_key="Credit")
    offer  = Offer(OFFERABLE)

    # Early stop: no data yet
    df = st.session_state.df
    if df.empty:
        st.info("Noch keine Daten geladen.")
        st.stop()

    # Compute _month helper column once (persisted in session state)
    if "datum" in df.columns and "_month" not in df.columns:
        st.session_state.df["_month"] = (
            pd.to_datetime(df["datum"], dayfirst=True, errors="coerce")
            .dt.strftime("%Y-%m")
        )
        df = st.session_state.df

    # ── Filters ──────────────────────────────────────────────────────────────
    st.subheader("Filter")

    def _ms(label: str, col: str, key: str) -> list | None:
        if col not in df.columns:
            return None
        opts = sorted(df[col].dropna().astype(str).unique().tolist())
        return st.multiselect(label, opts, default=opts, key=key)

    sel_partner  = _ms("Partner-ID",       "partner_id",     "f_pid")
    sel_vertr    = _ms("Vertragsnummer",   "vertragsnummer", "f_vertr")
    sel_terminal = _ms("Terminal-ID",      "terminal_id",    "f_term")
    sel_brand    = _ms("Brand",            "brand",          "f_brand")
    sel_cat      = _ms("Karten-Kategorie", "category",       "f_cat")
    sel_region   = _ms("IC++ Region",      "region",         "f_reg")

    from_m = to_m = None
    if "_month" in df.columns:
        months = sorted(df["_month"].dropna().unique().tolist())
        if len(months) >= 2:
            from_m, to_m = st.select_slider(
                "Zeitraum (von/bis Monat)",
                options=months,
                value=(months[0], months[-1]),
            )
        elif months:
            from_m = to_m = months[0]

    # ── Hochrechnung ─────────────────────────────────────────────────────────
    st.subheader("Hochrechnung (Stufe B)")
    annual_vol = st.number_input(
        "Jahresumsatz CHF  (0 = kein Hochrechnen)",
        min_value=0.0, value=0.0, step=1000.0, format="%.0f",
        help="Beobachteter Umsatz wird auf diesen Wert hochskaliert.",
    )

# ─────────────────────────────────────────────────────────────────────────────
# FILTER APPLICATION
# ─────────────────────────────────────────────────────────────────────────────
df = st.session_state.df
mask = pd.Series(True, index=df.index)

for col, sel in [
    ("partner_id",     sel_partner),
    ("vertragsnummer", sel_vertr),
    ("terminal_id",    sel_terminal),
    ("brand",          sel_brand),
    ("category",       sel_cat),
    ("region",         sel_region),
]:
    if sel is not None and col in df.columns:
        mask &= df[col].astype(str).isin(sel)

if from_m and to_m and "_month" in df.columns:
    mask &= df["_month"].between(from_m, to_m, inclusive="both")

fdf = df[mask].reset_index(drop=True)

if fdf.empty:
    st.warning("Keine Transaktionen für die aktuelle Selektion.")
    st.stop()

# ─────────────────────────────────────────────────────────────────────────────
# ENGINE
# ─────────────────────────────────────────────────────────────────────────────
comp = run_comparison(fdf, params, offer, dcc_pct)
t    = engine_totals(comp)

# ─────────────────────────────────────────────────────────────────────────────
# MAIN CONTENT
# ─────────────────────────────────────────────────────────────────────────────
st.title("SwiPay Worldline-Vergleich")

# ── Abgleichsbericht ─────────────────────────────────────────────────────────
rpt = st.session_state.report
if rpt is not None:
    has_issues = bool(rpt.files_blocked_hash or rpt.fanout_partner_ids)
    with st.expander("Abgleichsbericht", expanded=has_issues):
        c1, c2, c3 = st.columns(3)
        c1.metric("Neue Zeilen",            _int_fmt(rpt.rows_new))
        c2.metric("Übersprungen (Overlap)", _int_fmt(rpt.rows_skipped_overlap))
        c3.metric("Blockierte Dateien",     len(rpt.files_blocked_hash))
        if rpt.files_warned_name:
            st.warning(
                f"Dateiname bereits bekannt: {', '.join(rpt.files_warned_name)}"
            )
        if rpt.files_blocked_hash:
            st.error(
                f"Bit-identisch blockiert (nicht importiert): "
                f"{', '.join(rpt.files_blocked_hash)}"
            )
        if rpt.fanout_partner_ids:
            st.warning(
                f"Fan-out-Verdacht bei Partner-ID(s) "
                f"{', '.join(rpt.fanout_partner_ids)} — "
                "bitte vor Auswertung manuell prüfen."
            )

# ── Kennzahlen ───────────────────────────────────────────────────────────────
st.header("Kennzahlen")

is_purch   = ~fdf["is_refund"]
purch_df   = fdf[is_purch]
brutto_sum = float(purch_df["brutto"].sum()) if not purch_df.empty else 0.0
n_txn      = len(fdf)
n_purch    = int(is_purch.sum())
n_term     = int(fdf["terminal_id"].nunique()) if "terminal_id" in fdf.columns else 0
avg_ticket = brutto_sum / n_purch if n_purch else 0.0
diff       = t["wl_net"] - t["sp_net"]
dcc_adv    = t["sp_cashback"] - t["wl_cashback"]

c1, c2, c3, c4, c5 = st.columns(5)
c1.metric("Bruttoumsatz CHF",  chf(brutto_sum))
c2.metric("Transaktionen",     _int_fmt(n_txn))
c3.metric("Käufe",             _int_fmt(n_purch))
c4.metric("Terminals",         str(n_term))
c5.metric("Ø Ticket CHF",      chf(avg_ticket))

st.divider()

c1, c2, c3 = st.columns(3)
c1.metric("WL-Gebühren CHF",         chf(t["wl_net"]))
c2.metric("SP-Gebühren CHF",         chf(t["sp_net"]))
c3.metric("Differenz / Ersparnis CHF", chf(diff))

c1, c2, c3 = st.columns(3)
c1.metric("WL DCC-Cashback CHF",  chf(t["wl_cashback"]))
c2.metric("SP DCC-Cashback CHF",  chf(t["sp_cashback"]))
c3.metric("DCC-Vorteil CHF",      chf(dcc_adv))

# ── Hochrechnung ─────────────────────────────────────────────────────────────
if annual_vol > 0:
    st.header("Hochrechnung (Stufe B)")
    try:
        proj = project_tier_b(fdf, params, offer, dcc_pct, annual_volume=annual_vol)
        cov  = proj.coverage

        _ICONS = {
            CoverageLabel.SIMULATABLE:  "🟢",
            CoverageLabel.LOW_COVERAGE: "🟡",
            CoverageLabel.INDICATIVE:   "🔴",
        }
        icon = _ICONS.get(cov.label, "⚪")
        st.caption(
            f"{icon} Deckungsgrad: **{cov.coverage_pct:.0%}**  ·  "
            f"Stufe: **{cov.label.value}**"
        )

        if cov.label == CoverageLabel.INDICATIVE:
            st.info(
                "Deckung < 25 % — Ergebnis ist **indikativ**. "
                "Headline zeigt das konservative Ende (–15 %). "
                "Mehr Datenmonate für belastbare Aussage empfohlen."
            )
        elif cov.label == CoverageLabel.LOW_COVERAGE:
            st.warning("Deckung 25–60 % — Punktschätzung mit Einschränkung.")

        # Conservative end as headline for indicative tier
        headline = proj.band_low if cov.label == CoverageLabel.INDICATIVE else proj.saving_annual

        c1, c2, c3, c4 = st.columns(4)
        c1.metric("Headline CHF",        chf(headline))
        c2.metric("Band tief (–15 %)",   chf(proj.band_low))
        c3.metric("Punktschätzung",      chf(proj.saving_annual))
        c4.metric("Band hoch (+15 %)",   chf(proj.band_high))

        c1, c2 = st.columns(2)
        c1.metric("DCC-Vorteil p.a. CHF",  chf(proj.dcc_advantage_annual))
        c2.metric("Beobachtet / Jahresziel",
                  f"{chf(proj.observed_volume)} / {chf(annual_vol)} CHF")
    except ValueError as exc:
        st.error(str(exc))

# ── Partner-Ansicht ───────────────────────────────────────────────────────────
st.header("Partner-Ansicht")

# Attach engine columns to the filtered frame (positional, both are reset_index)
m = fdf.copy()
m["wl_net"] = comp["wl_net"].values
m["sp_net"] = comp["sp_net"].values
m["wl_cb"]  = comp["wl_cashback"].values
m["sp_cb"]  = comp["sp_cashback"].values

_MONEY = ["Umsatz", "Ø Ticket", "WL-Geb.", "SP-Geb.", "Diff.", "WL-DCC", "SP-DCC", "DCC-Vtl."]
_CNTS  = ["Txn"]


def _agg(sub: pd.DataFrame) -> dict:
    pu = sub[~sub["is_refund"]]
    n_pu = len(pu)
    return {
        "Name":     str(sub["partner_name"].iloc[0])
                    if "partner_name" in sub.columns and n_pu > 0 else "",
        "Umsatz":   round(float(pu["brutto"].sum()), 2),
        "Txn":      len(sub),
        "Ø Ticket": round(float(pu["brutto"].mean()), 2) if n_pu else 0.0,
        "WL-Geb.":  round(float(sub["wl_net"].sum()), 2),
        "SP-Geb.":  round(float(sub["sp_net"].sum()), 2),
        "Diff.":    round(float(sub["wl_net"].sum() - sub["sp_net"].sum()), 2),
        "WL-DCC":   round(float(sub["wl_cb"].sum()), 2),
        "SP-DCC":   round(float(sub["sp_cb"].sum()), 2),
        "DCC-Vtl.": round(float(sub["sp_cb"].sum() - sub["wl_cb"].sum()), 2),
    }


pid_col = "partner_id" if "partner_id" in m.columns else None

# Per-partner table
einzel_df = pd.DataFrame()
if pid_col:
    rows_einzel = {pid: _agg(sub) for pid, sub in m.groupby(pid_col)}
    einzel_df = pd.DataFrame.from_dict(rows_einzel, orient="index")
    einzel_df.index.name = "Partner-ID"

# Per-group table
groups = get_groups(DB_PATH)
grp_rows = {}
if pid_col:
    for gname, pids in groups.items():
        sub = m[m[pid_col].astype(str).isin(pids)]
        if sub.empty:
            continue
        row = _agg(sub)
        row["Partner-IDs"] = ", ".join(pids)
        grp_rows[gname] = row
grp_df = pd.DataFrame.from_dict(grp_rows, orient="index") if grp_rows else pd.DataFrame()

col_e, col_g = st.columns(2)

with col_e:
    st.subheader("Einzelansicht")
    if not einzel_df.empty:
        st.dataframe(_fmt_df(einzel_df, _MONEY, _CNTS), use_container_width=True)
    else:
        st.info("Keine Partner-ID-Spalte in den Daten.")

with col_g:
    st.subheader("Gruppenansicht")
    if not grp_df.empty:
        show_cols = ["Partner-IDs"] + [c for c in _MONEY + _CNTS if c in grp_df.columns]
        st.dataframe(
            _fmt_df(grp_df[show_cols], _MONEY, _CNTS),
            use_container_width=True,
        )
    else:
        st.info("Noch keine Gruppen definiert.")

# Group management
with st.expander("Gruppen verwalten"):
    c_new, c_del = st.columns(2)
    with c_new:
        st.markdown("**Neue Gruppe / Zuordnung**")
        g_name = st.text_input("Gruppenname", key="g_name_in")
        if pid_col:
            all_pids = sorted(df[pid_col].dropna().astype(str).unique().tolist())
            g_pids   = st.multiselect("Partner-IDs zuweisen", all_pids, key="g_pids_in")
        else:
            g_pids = []
        if st.button("Gruppe speichern") and g_name.strip() and g_pids:
            assign_group(DB_PATH, g_name.strip(), g_pids)
            st.success(f"Gruppe «{g_name.strip()}» gespeichert.")
            st.rerun()

    with c_del:
        st.markdown("**Gruppe entfernen**")
        if groups:
            del_name = st.selectbox("Gruppe", list(groups.keys()), key="g_del_sel")
            if st.button("Entfernen") and del_name:
                unassign_group(DB_PATH, groups[del_name])
                st.success(f"Gruppe «{del_name}» entfernt.")
                st.rerun()
        else:
            st.info("Keine Gruppen vorhanden.")

# ── Analysen (Charts) ─────────────────────────────────────────────────────────
st.header("Analysen")

tab_hist, tab_monthly = st.tabs(["Transaktionsgrössen", "Monatsverlauf"])

with tab_hist:
    purch_hist = fdf[~fdf["is_refund"]].copy()
    if purch_hist.empty:
        st.info("Keine Kauftransaktionen für die aktuelle Selektion.")
    else:
        purch_hist["Bucket"] = pd.cut(
            purch_hist["brutto"],
            bins=HIST_BINS,
            labels=HIST_LABELS,
            right=True,
            include_lowest=True,
        )
        hist = (
            purch_hist.groupby("Bucket", observed=True)
            .agg(Anzahl=("brutto", "count"), Umsatz=("brutto", "sum"))
            .reset_index()
        )
        hist_show = hist.copy()
        hist_show["Umsatz CHF"] = hist_show["Umsatz"].apply(chf)
        hist_show["Anzahl"]     = hist_show["Anzahl"].apply(_int_fmt)
        st.dataframe(
            hist_show[["Bucket", "Anzahl", "Umsatz CHF"]],
            use_container_width=True,
            hide_index=True,
        )
        st.bar_chart(hist.set_index("Bucket")[["Anzahl"]])

with tab_monthly:
    if "_month" not in fdf.columns:
        st.info("Keine Datumsinformation verfügbar.")
    else:
        m_chart = fdf.copy()
        m_chart["_purch_brutto"] = m_chart["brutto"].where(~m_chart["is_refund"], 0.0)
        m_chart["wl_net"]        = comp["wl_net"].values
        m_chart["sp_net"]        = comp["sp_net"].values

        monthly = (
            m_chart.groupby("_month")
            .agg(
                Umsatz=("_purch_brutto", "sum"),
                WL_Geb=("wl_net", "sum"),
                SP_Geb=("sp_net", "sum"),
                Txn=("brutto", "count"),
            )
            .sort_index()
            .rename(columns={"WL_Geb": "WL-Geb.", "SP_Geb": "SP-Geb."})
        )
        monthly.index.name = "Monat"

        monthly_show = monthly.copy()
        monthly_show["Umsatz CHF"]   = monthly_show["Umsatz"].apply(chf)
        monthly_show["WL-Geb. CHF"]  = monthly_show["WL-Geb."].apply(chf)
        monthly_show["SP-Geb. CHF"]  = monthly_show["SP-Geb."].apply(chf)
        monthly_show["Txn"]          = monthly_show["Txn"].apply(_int_fmt)
        st.dataframe(
            monthly_show[["Umsatz CHF", "WL-Geb. CHF", "SP-Geb. CHF", "Txn"]],
            use_container_width=True,
        )
        # Two separate charts: fees share the same scale, volume is much larger.
        st.caption("Gebühren je Monat")
        st.line_chart(monthly[["WL-Geb.", "SP-Geb."]])
        st.caption("Umsatz je Monat")
        st.bar_chart(monthly[["Umsatz"]])

# ── Export ────────────────────────────────────────────────────────────────────
st.header("Export")

# Metadata for PDF
def _period_bounds(df_in: pd.DataFrame) -> tuple[str, str]:
    if "_month" in df_in.columns:
        months = sorted(df_in["_month"].dropna().unique().tolist())
        if months:
            return months[0], months[-1]
    if "datum" in df_in.columns:
        dates = pd.to_datetime(df_in["datum"], dayfirst=True, errors="coerce").dropna()
        if not dates.empty:
            return dates.min().strftime("%Y-%m"), dates.max().strftime("%Y-%m")
    return "–", "–"

def _partner_display(df_in: pd.DataFrame) -> str:
    if "partner_name" in df_in.columns:
        names = df_in["partner_name"].dropna().unique().tolist()
        if names:
            return names[0] if len(names) == 1 else ", ".join(names[:3])
    if "partner_id" in df_in.columns:
        pids = df_in["partner_id"].dropna().unique().tolist()
        if pids:
            return pids[0] if len(pids) == 1 else f"{pids[0]} (+{len(pids)-1})"
    return "–"

zero_effect_brands = sorted(
    fdf.loc[~comp["offerable"], "brand"].dropna().unique().tolist()
) if "brand" in fdf.columns else []

pf, pt      = _period_bounds(fdf)
pname       = _partner_display(fdf)
fanout_ids  = (rpt.fanout_partner_ids if rpt else [])
proj_ref    = None
try:
    if annual_vol > 0:
        proj_ref = project_tier_b(fdf, params, offer, dcc_pct, annual_volume=annual_vol)
except ValueError:
    pass

col_pdf, col_csv = st.columns(2)

with col_pdf:
    st.subheader("Kunden-PDF")
    st.caption(
        "Zusammenfassung in SwiPay-CI: Ersparnis, DCC-Vorteil, "
        "Hochrechnung und Datenhinweise."
    )
    if st.button("PDF generieren", type="primary"):
        try:
            mix_h = proj_ref.mix_hints if proj_ref else []
            pdf_bytes = build_pdf(
                partner_name       = pname,
                period_from        = pf,
                period_to          = pt,
                brutto             = brutto_sum,
                n_txn              = n_txn,
                n_terminals        = n_term,
                avg_ticket         = avg_ticket,
                wl_net             = t["wl_net"],
                sp_net             = t["sp_net"],
                wl_cashback        = t["wl_cashback"],
                sp_cashback        = t["sp_cashback"],
                saving             = diff,
                dcc_advantage      = dcc_adv,
                dcc_pct            = dcc_pct,
                projection         = proj_ref,
                annual_volume      = annual_vol,
                fanout_partner_ids = fanout_ids,
                zero_effect_brands = zero_effect_brands,
                mix_hints          = mix_h,
            )
            fname = (
                f"SwiPay_Analyse_{pname.replace(' ','_')}_{pf}_{pt}.pdf"
                .replace(",", "").replace("/", "-")
            )
            st.download_button(
                "PDF herunterladen",
                data=pdf_bytes,
                file_name=fname,
                mime="application/pdf",
            )
        except Exception as exc:
            st.error(f"PDF-Fehler: {exc}")

with col_csv:
    st.subheader("Interner Detail-Export")
    st.caption(
        "Alle Worldline-Felder plus Engine-KPIs (wl_net, sp_net, "
        "wl_cashback, sp_cashback, floored, offerable), ungefiltert aus der Selektion."
    )
    csv_str = build_csv(fdf, comp)
    st.download_button(
        "CSV herunterladen",
        data=csv_str.encode("utf-8-sig"),
        file_name=f"SwiPay_Detail_{pf}_{pt}.csv",
        mime="text/csv",
    )
