"""
SwiPay Worldline-Vergleichstool — Streamlit-Oberfläche im SwiPay-CI.
Start: uv run streamlit run app.py

Navigierte App: Präsentation (Kundentermin), Transaktionen, Partner & Gruppen,
Einstellungen (Upload · Mapping · ASF). Design-System in src/ui.py.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

import pandas as pd
import streamlit as st

import ui
from pipeline import run_comparison, totals as engine_totals
from ingest import ingest_files, init_db
from projection import project_tier_b, CoverageLabel
from db_groups import init_groups_db, get_groups, assign_group, unassign_group
from reporter import build_pdf, build_csv
from settings import (
    OFFERABLE_TYPES,
    BrandRecord,
    BrandMaster,
    RateProfile,
    TypeRate,
    build_param_table,
    conservative_collapse,
    default_rate_profile,
    load_brand_master,
    prefill_brand_overrides,
)

st.set_page_config(page_title="SwiPay · Worldline-Vergleich", layout="wide",
                   initial_sidebar_state="expanded")
ui.inject_css()

ROOT    = Path(__file__).parent
DB_PATH = str(ROOT / "data" / "swipay.db")
(ROOT / "data").mkdir(exist_ok=True)

_TYPE_LABEL = {"debit": "Debit", "credit": "Credit", "credit2": "Credit 2"}
HIST_BINS   = [0, 10, 50, 100, 200, 500, float("inf")]
HIST_LABELS = ["0–10", "10–50", "50–100", "100–200", "200–500", "500+"]

chf, num, pct, chf_c = ui.chf, ui.num, ui.pct, ui.chf_compact

init_db(DB_PATH)
init_groups_db(DB_PATH)

# ── Session state ─────────────────────────────────────────────────────────────
for _k, _v in [("df", pd.DataFrame()), ("report", None), ("nav", "Präsentation")]:
    if _k not in st.session_state:
        st.session_state[_k] = _v
if "master" not in st.session_state:
    st.session_state.master = load_brand_master()
if "profile" not in st.session_state:
    st.session_state.profile = default_rate_profile()

master: BrandMaster = st.session_state.master
profile: RateProfile = st.session_state.profile


# ── Helpers ───────────────────────────────────────────────────────────────────

def _to_datetime(s: pd.Series) -> pd.Series:
    """Parse the WL date column. XLSB delivers Excel serial numbers (days since
    1899-12-30); CSV exports deliver date strings. Handle both."""
    if pd.api.types.is_numeric_dtype(s):
        return pd.to_datetime(s, unit="D", origin="1899-12-30", errors="coerce")
    return pd.to_datetime(s, dayfirst=True, errors="coerce")


def _ensure_month(df: pd.DataFrame) -> pd.DataFrame:
    if "datum" in df.columns and "_month" not in df.columns:
        df["_month"] = _to_datetime(df["datum"]).dt.strftime("%Y-%m")
    return df


def _months(df: pd.DataFrame) -> list[str]:
    return sorted(df["_month"].dropna().unique().tolist()) if "_month" in df else []


def _derive(fdf: pd.DataFrame, comp: pd.DataFrame, t: dict) -> dict:
    b = pd.to_numeric(fdf["brutto"], errors="coerce")
    is_p = ~fdf["is_refund"]
    brutto = float(b[is_p].sum())
    n_p = int(is_p.sum())
    dcc_vol = float(b[fdf["is_dcc"].to_numpy(bool)].sum()) if "is_dcc" in fdf else 0.0
    if "region" in fdf.columns:
        r = fdf["region"].astype(str).str.strip().str.lower()
        fx_vol = float(b[(r != "domestic") & r.ne("nan")].sum())
    else:
        fx_vol = 0.0
    return {
        "brutto": brutto, "n_txn": len(fdf), "n_purch": n_p,
        "n_term": int(fdf["terminal_id"].nunique()) if "terminal_id" in fdf else 0,
        "avg_ticket": brutto / n_p if n_p else 0.0,
        "diff": t["wl_net"] - t["sp_net"],
        "dcc_adv": t["sp_cashback"] - t["wl_cashback"],
        "dcc_vol": dcc_vol, "fx_vol": fx_vol,
    }


def _savings_by_type(fdf: pd.DataFrame, comp: pd.DataFrame) -> pd.DataFrame:
    types = [master.type_of(b) for b in fdf["brand"].astype(str)]
    tmp = pd.DataFrame({
        "Typ": [_TYPE_LABEL.get(x, "Spezial / n/a") for x in types],
        "Ersparnis": (comp["wl_net"] - comp["sp_net"]).values,
    })
    g = tmp.groupby("Typ", as_index=False)["Ersparnis"].sum()
    return g[g["Ersparnis"].abs() > 0.005]


def _monthly(fdf: pd.DataFrame, comp: pd.DataFrame) -> pd.DataFrame:
    if "_month" not in fdf.columns:
        return pd.DataFrame()
    m = fdf.copy()
    m["WL"] = comp["wl_net"].values
    m["SP"] = comp["sp_net"].values
    m["_pb"] = m["brutto"].where(~m["is_refund"], 0.0)
    out = (m.groupby("_month").agg(WL=("WL", "sum"), SP=("SP", "sum"),
                                   Umsatz=("_pb", "sum")).reset_index()
           .rename(columns={"_month": "Monat"}).sort_values("Monat"))
    return out


def _hist(fdf: pd.DataFrame) -> pd.DataFrame:
    p = fdf[~fdf["is_refund"]].copy()
    if p.empty:
        return pd.DataFrame()
    p["Bucket"] = pd.cut(p["brutto"], bins=HIST_BINS, labels=HIST_LABELS,
                         right=True, include_lowest=True)
    return (p.groupby("Bucket", observed=True).agg(Anzahl=("brutto", "count"))
            .reset_index())


def _region(fdf: pd.DataFrame) -> pd.DataFrame:
    if "region" not in fdf.columns:
        return pd.DataFrame()
    p = fdf[~fdf["is_refund"]].copy()
    g = (p.groupby(p["region"].astype(str).str.strip().str.upper())
         .agg(Umsatz=("brutto", "sum")).reset_index()
         .rename(columns={"region": "Region"}))
    g.columns = ["Region", "Umsatz"]
    return g[g["Region"].ne("NAN")].sort_values("Umsatz", ascending=False)


def _apply_period(df: pd.DataFrame, frm, to) -> pd.Series:
    if frm and to and "_month" in df.columns:
        return df["_month"].between(frm, to, inclusive="both")
    return pd.Series(True, index=df.index)


# ── Build engine params (all pages) ───────────────────────────────────────────
params, offer = build_param_table(master, profile, mode=profile.mode)

# ── SIDEBAR ────────────────────────────────────────────────────────────────────
with st.sidebar:
    ui.sidebar_brand()
    NAV = ["📊  Präsentation", "⇄  Transaktionen", "👥  Partner & Gruppen", "⚙  Einstellungen"]
    _CLEAN = {n: n.split("  ", 1)[1] for n in NAV}
    sel = st.radio("Navigation", NAV,
                   index=[_CLEAN[n] for n in NAV].index(st.session_state.nav),
                   label_visibility="collapsed")
    st.session_state.nav = _CLEAN[sel]
    page = st.session_state.nav
    st.markdown(
        '<div style="margin-top:1.4rem;font-size:.7rem;color:#8a9495;'
        'letter-spacing:.04em">WL Compare · IN ABNAHME<br>IC++ gegen IC++</div>',
        unsafe_allow_html=True)

df = _ensure_month(st.session_state.df)

# Gate: without data, only Einstellungen is useful.
if df.empty and page != "Einstellungen":
    ui.page_header("Willkommen", "Lade zuerst einen Worldline-Export, dann geht's los.",
                   status="Bereit", meta="WL Compare Tool")
    ui.info_banner("Noch keine Daten geladen. Wechsle zu <b>⚙ Einstellungen → Daten "
                   "laden</b> und lade einen Worldline-Export (XLSB/CSV).")
    st.stop()


# ════════════════════════════════════════════════════════════════════════════
# PAGE: PRÄSENTATION
# ════════════════════════════════════════════════════════════════════════════
def page_praesentation() -> None:
    months = _months(df)
    pid_col = "partner_id" if "partner_id" in df.columns else None

    # Selection row
    c1, c2, c3 = st.columns([1.4, 1.4, 1])
    with c1:
        scope = st.radio("Auswahl", ["Alle", "Partner", "Gruppe"], horizontal=True,
                         label_visibility="collapsed")
    groups = get_groups(DB_PATH)
    sel_pids = None
    label = "Alle Partner"
    with c2:
        if scope == "Partner" and pid_col:
            opts = sorted(df[pid_col].dropna().astype(str).unique().tolist())
            sel_pids = st.multiselect("Partner-ID", opts, default=opts[:1] or opts,
                                      label_visibility="collapsed",
                                      placeholder="Partner-ID wählen")
            label = ", ".join(sel_pids) if sel_pids else "Alle Partner"
        elif scope == "Gruppe" and groups:
            gname = st.selectbox("Gruppe", list(groups.keys()),
                                 label_visibility="collapsed")
            sel_pids = groups.get(gname, [])
            label = f"Gruppe «{gname}»"
        elif scope == "Gruppe":
            st.caption("Noch keine Gruppen — unter «Partner & Gruppen» anlegen.")
    with c3:
        annual_vol = st.number_input("Jahresumsatz CHF", min_value=0.0, value=0.0,
                                     step=10000.0, format="%.0f",
                                     help="Für die Jahres-Hochrechnung. 0 = Zeitraum-Ist.")
    frm = to = None
    if len(months) >= 2:
        frm, to = st.select_slider("Zeitraum", options=months,
                                   value=(months[0], months[-1]))
    elif months:
        frm = to = months[0]

    # Filter
    mask = pd.Series(True, index=df.index)
    if sel_pids is not None and pid_col:
        mask &= df[pid_col].astype(str).isin(sel_pids)
    mask &= _apply_period(df, frm, to)
    fdf = df[mask].reset_index(drop=True)

    partner_disp = label
    if scope == "Partner" and sel_pids and "partner_name" in fdf.columns and not fdf.empty:
        names = fdf["partner_name"].dropna().unique().tolist()
        if len(names) == 1:
            partner_disp = names[0]

    ui.page_header(
        f"Auswertung · {partner_disp}",
        "Dein Konditionenvergleich Worldline gegen SwiPay auf einen Blick.",
        status="IN ABNAHME",
        meta=f"Zeitraum {frm or '–'} bis {to or '–'} · IC++ gegen IC++",
    )

    if fdf.empty:
        ui.info_banner("Keine Transaktionen für diese Auswahl.")
        return

    comp = run_comparison(fdf, params, offer, profile.dcc_pct)
    t = engine_totals(comp)
    d = _derive(fdf, comp, t)

    # ── HERO: Jahres-Ersparnis (Hochrechnung) ─────────────────────────────────
    proj = None
    if annual_vol > 0:
        try:
            proj = project_tier_b(fdf, params, offer, profile.dcc_pct,
                                  annual_volume=annual_vol)
        except ValueError:
            proj = None
    hcol, kcol = st.columns([1.15, 1])
    with hcol:
        if proj is not None:
            cov = proj.coverage
            headline = (proj.band_low if cov.label == CoverageLabel.INDICATIVE
                        else proj.saving_annual)
            acc = ui.GREEN if headline >= 0 else ui.ROT
            cov_txt = {CoverageLabel.SIMULATABLE: "hohe Deckung",
                       CoverageLabel.LOW_COVERAGE: "mittlere Deckung",
                       CoverageLabel.INDICATIVE: "indikativ"}[cov.label]
            ui.hero("Ersparnis pro Jahr (Hochrechnung)", f"CHF {chf(headline, 0)}",
                    band=f"Planungsband CHF {chf(proj.band_low, 0)} – {chf(proj.band_high, 0)}",
                    foot=f"Deckungsgrad {cov.coverage_pct:.0%} · {cov_txt} · "
                         f"DCC-Vorteil p.a. CHF {chf(proj.dcc_advantage_annual, 0)}",
                    accent=acc)
        else:
            acc = ui.GREEN if d["diff"] >= 0 else ui.ROT
            ui.hero("Ersparnis im Zeitraum", f"CHF {chf(d['diff'], 0)}",
                    foot="Jahresumsatz oben eingeben für die Hochrechnung auf 12 Monate.",
                    accent=acc)
    with kcol:
        rel = (d["diff"] / abs(t["wl_net"]) * 100) if t["wl_net"] else 0.0
        ui.kpi_row([
            {"label": "Bruttoumsatz", "value": f"CHF {chf_c(d['brutto'])}", "accent": ui.BLUE},
            {"label": "Gebühren-Red.", "value": f"{rel:.1f} %",
             "foot": "vs. Worldline", "accent": ui.GREEN if rel >= 0 else ui.ROT},
        ])
        ui.kpi_row([
            {"label": "Transaktionen", "value": num(d["n_txn"]),
             "foot": f"{num(d['n_purch'])} Käufe", "accent": ui.CYAN},
            {"label": "DCC-Vorteil", "value": f"CHF {chf(d['dcc_adv'])}", "accent": ui.CYAN},
        ])

    # ── Ersparnis-Vergleich ───────────────────────────────────────────────────
    ui.section("Ersparnis-Vergleich", "Worldline gegen SwiPay (netto)")
    a, b = st.columns(2)
    with a:
        st.altair_chart(ui.chart_fees_compare(t["wl_net"], t["sp_net"]),
                        use_container_width=True)
    with b:
        sbt = _savings_by_type(fdf, comp)
        if not sbt.empty:
            st.altair_chart(ui.chart_savings_by_type(sbt), use_container_width=True)
        else:
            st.caption("Keine offerierbaren Brands mit Effekt in dieser Auswahl.")

    # ── DCC-Visualisierung ─────────────────────────────────────────────────────
    ui.section("DCC", "Cashback & Fremdwährungs-Potenzial")
    a, b = st.columns(2)
    with a:
        st.altair_chart(ui.chart_dcc_compare(t["wl_cashback"], t["sp_cashback"]),
                        use_container_width=True)
        st.caption(f"WL-DCC-Ø { (t['wl_cashback']/d['dcc_vol']*100) if d['dcc_vol'] else 0:.2f} %"
                   f" · SP-Satz {profile.dcc_pct*100:.2f} % vom genutzten DCC-Volumen "
                   f"(CHF {chf(d['dcc_vol'])}).")
    with b:
        st.altair_chart(ui.chart_dcc_potential(d["dcc_vol"], d["fx_vol"]),
                        use_container_width=True)
        share = d["dcc_vol"] / d["fx_vol"] if d["fx_vol"] else 0.0
        st.caption(f"Genutzt CHF {chf(d['dcc_vol'])} von DCC-fähigem Fremdwährungsvolumen "
                   f"CHF {chf(d['fx_vol'])} ({share:.0%}).")

    # ── Zeit & Verteilung ──────────────────────────────────────────────────────
    ui.section("Zeit & Verteilung", "Monatsverlauf, Transaktionsgrössen, Regionen")
    mdf = _monthly(fdf, comp)
    if not mdf.empty and len(mdf) >= 2:
        a, b = st.columns([1.4, 1])
        with a:
            st.altair_chart(ui.chart_monthly(mdf[["Monat", "WL", "SP"]]),
                            use_container_width=True)
        with b:
            st.altair_chart(ui.chart_volume_monthly(mdf[["Monat", "Umsatz"]]),
                            use_container_width=True)
    a, b = st.columns(2)
    with a:
        h = _hist(fdf)
        if not h.empty:
            st.altair_chart(ui.chart_hist(h), use_container_width=True)
    with b:
        r = _region(fdf)
        if not r.empty:
            st.altair_chart(ui.chart_region(r), use_container_width=True)

    # ── Export ─────────────────────────────────────────────────────────────────
    ui.section("Export")
    zero_brands = sorted(fdf.loc[~comp["offerable"], "brand"].dropna().unique().tolist()) \
        if "brand" in fdf.columns else []
    a, b = st.columns(2)
    with a:
        if st.button("Kunden-PDF generieren", type="primary"):
            try:
                pdf_bytes = build_pdf(
                    partner_name=partner_disp, period_from=frm or "–", period_to=to or "–",
                    brutto=d["brutto"], n_txn=d["n_txn"], n_terminals=d["n_term"],
                    avg_ticket=d["avg_ticket"], wl_net=t["wl_net"], sp_net=t["sp_net"],
                    wl_cashback=t["wl_cashback"], sp_cashback=t["sp_cashback"],
                    saving=d["diff"], dcc_advantage=d["dcc_adv"], dcc_pct=profile.dcc_pct,
                    projection=proj, annual_volume=annual_vol,
                    fanout_partner_ids=(st.session_state.report.fanout_partner_ids
                                        if st.session_state.report else []),
                    zero_effect_brands=zero_brands,
                    mix_hints=proj.mix_hints if proj else [])
                st.download_button("PDF herunterladen", data=pdf_bytes,
                    file_name=f"SwiPay_Analyse_{partner_disp}_{frm}_{to}.pdf"
                    .replace(" ", "_").replace(",", "").replace("/", "-"),
                    mime="application/pdf")
            except Exception as exc:
                st.error(f"PDF-Fehler: {exc}")
    with b:
        st.download_button("Detail-CSV herunterladen",
                           data=build_csv(fdf, comp).encode("utf-8-sig"),
                           file_name=f"SwiPay_Detail_{frm}_{to}.csv", mime="text/csv")


# ════════════════════════════════════════════════════════════════════════════
# PAGE: TRANSAKTIONEN
# ════════════════════════════════════════════════════════════════════════════
_BRAND_ICON = {"Visa": "VISA", "VisaDebit": "VISA", "Mastercard": "MC",
               "Debit Mastercard": "MC", "Maestro": "MAE", "TWINT": "TW"}


def page_transaktionen() -> None:
    ui.page_header("Transaktionen", "Filtern, prüfen, veranschaulichen.",
                   status="Live", meta="Detailansicht je Transaktion")
    months = _months(df)

    with st.expander("Filter", expanded=True):
        c1, c2, c3 = st.columns(3)
        def ms(label, col, c):
            if col not in df.columns:
                return None
            opts = sorted(df[col].dropna().astype(str).unique().tolist())
            return c.multiselect(label, opts, default=opts)
        sel_brand = ms("Brand", "brand", c1)
        sel_cat   = ms("Kategorie", "category", c2)
        sel_reg   = ms("Clearing Region", "region", c3)
        frm = to = None
        if len(months) >= 2:
            frm, to = st.select_slider("Zeitraum", options=months,
                                       value=(months[0], months[-1]))
        elif months:
            frm = to = months[0]

    mask = _apply_period(df, frm, to)
    for col, sel in [("brand", sel_brand), ("category", sel_cat), ("region", sel_reg)]:
        if sel is not None and col in df.columns:
            mask &= df[col].astype(str).isin(sel)
    fdf = df[mask].reset_index(drop=True)
    if fdf.empty:
        ui.info_banner("Keine Transaktionen für diese Auswahl.")
        return

    comp = run_comparison(fdf, params, offer, profile.dcc_pct)
    t = engine_totals(comp)
    d = _derive(fdf, comp, t)
    ui.kpi_row([
        {"label": "Transaktionen", "value": num(d["n_txn"]), "accent": ui.CYAN},
        {"label": "Bruttoumsatz", "value": f"CHF {chf_c(d['brutto'])}", "accent": ui.BLUE},
        {"label": "Ø Ticket", "value": f"CHF {chf(d['avg_ticket'])}", "accent": ui.CYAN},
        {"label": "Ersparnis", "value": f"CHF {chf(d['diff'])}",
         "accent": ui.GREEN if d["diff"] >= 0 else ui.ROT},
    ])

    ui.section("Letzte Transaktionen", f"{num(min(len(fdf), 200))} von {num(len(fdf))}")
    show = fdf.copy()
    show["wl"] = comp["wl_net"].values
    show["sp"] = comp["sp_net"].values
    cols = [c for c in ["datum", "zeit", "brand", "category", "terminal_id",
                        "region", "brutto", "wl", "sp"] if c in show.columns]
    disp = show[cols].head(200).rename(columns={
        "datum": "Datum", "zeit": "Zeit", "brand": "Brand", "category": "Kategorie",
        "terminal_id": "Terminal", "region": "Region", "brutto": "Betrag",
        "wl": "WL-Geb.", "sp": "SP-Geb."})
    if "Datum" in disp:
        disp["Datum"] = _to_datetime(disp["Datum"]).dt.strftime("%d.%m.%Y")
    if "Zeit" in disp:
        secs = pd.to_numeric(disp["Zeit"], errors="coerce") * 86400
        disp["Zeit"] = secs.apply(
            lambda x: f"{int(x // 3600):02d}:{int((x % 3600) // 60):02d}"
            if pd.notna(x) else "")
    for c in ["Betrag", "WL-Geb.", "SP-Geb."]:
        if c in disp:
            disp[c] = disp[c].apply(lambda x: chf(float(x)) if pd.notna(x) else "")
    st.dataframe(disp, use_container_width=True, hide_index=True)

    ui.section("Verteilung")
    a, b = st.columns(2)
    with a:
        h = _hist(fdf)
        if not h.empty:
            st.altair_chart(ui.chart_hist(h), use_container_width=True)
    with b:
        r = _region(fdf)
        if not r.empty:
            st.altair_chart(ui.chart_region(r), use_container_width=True)


# ════════════════════════════════════════════════════════════════════════════
# PAGE: PARTNER & GRUPPEN
# ════════════════════════════════════════════════════════════════════════════
_MONEY = ["Umsatz", "Ø Ticket", "WL-Geb.", "SP-Geb.", "Diff.", "DCC-Vtl."]


def page_partner() -> None:
    ui.page_header("Partner & Gruppen", "Einzelne Partner vergleichen und bündeln.",
                   status="Aktiv", meta="Gruppierung nach Partner-ID")
    pid_col = "partner_id" if "partner_id" in df.columns else None
    if not pid_col:
        ui.info_banner("Keine Partner-ID-Spalte in den Daten.")
        return

    comp = run_comparison(df, params, offer, profile.dcc_pct)
    m = df.copy()
    m["wl_net"], m["sp_net"] = comp["wl_net"].values, comp["sp_net"].values
    m["wl_cb"], m["sp_cb"] = comp["wl_cashback"].values, comp["sp_cashback"].values

    def agg(sub: pd.DataFrame) -> dict:
        pu = sub[~sub["is_refund"]]
        n = len(pu)
        return {
            "Name": str(sub["partner_name"].iloc[0]) if "partner_name" in sub and n else "",
            "Umsatz": round(float(pu["brutto"].sum()), 2), "Txn": len(sub),
            "Ø Ticket": round(float(pu["brutto"].mean()), 2) if n else 0.0,
            "WL-Geb.": round(float(sub["wl_net"].sum()), 2),
            "SP-Geb.": round(float(sub["sp_net"].sum()), 2),
            "Diff.": round(float(sub["wl_net"].sum() - sub["sp_net"].sum()), 2),
            "DCC-Vtl.": round(float(sub["sp_cb"].sum() - sub["wl_cb"].sum()), 2),
        }

    def fmt(d_in: pd.DataFrame) -> pd.DataFrame:
        o = d_in.copy()
        for c in _MONEY:
            if c in o:
                o[c] = o[c].apply(lambda x: chf(float(x)) if pd.notna(x) else "")
        if "Txn" in o:
            o["Txn"] = o["Txn"].apply(lambda x: num(int(x)))
        return o

    ui.section("Einzelansicht")
    rows = {pid: agg(sub) for pid, sub in m.groupby(pid_col)}
    edf = pd.DataFrame.from_dict(rows, orient="index"); edf.index.name = "Partner-ID"
    st.dataframe(fmt(edf), use_container_width=True)

    groups = get_groups(DB_PATH)
    ui.section("Gruppenansicht")
    grows = {}
    for g, pids in groups.items():
        sub = m[m[pid_col].astype(str).isin(pids)]
        if sub.empty:
            continue
        row = agg(sub); row["Partner-IDs"] = ", ".join(pids); grows[g] = row
    if grows:
        gdf = pd.DataFrame.from_dict(grows, orient="index")
        st.dataframe(fmt(gdf[["Partner-IDs"] + _MONEY + ["Txn"]]), use_container_width=True)
    else:
        st.caption("Noch keine Gruppen definiert.")

    ui.section("Gruppen verwalten")
    c1, c2 = st.columns(2)
    with c1:
        gname = st.text_input("Gruppenname")
        all_pids = sorted(df[pid_col].dropna().astype(str).unique().tolist())
        gpids = st.multiselect("Partner-IDs zuweisen", all_pids)
        if st.button("Gruppe speichern", type="primary") and gname.strip() and gpids:
            assign_group(DB_PATH, gname.strip(), gpids)
            st.success(f"Gruppe «{gname.strip()}» gespeichert."); st.rerun()
    with c2:
        if groups:
            dname = st.selectbox("Gruppe entfernen", list(groups.keys()))
            if st.button("Entfernen") and dname:
                unassign_group(DB_PATH, groups[dname])
                st.success(f"Gruppe «{dname}» entfernt."); st.rerun()
        else:
            st.caption("Keine Gruppen vorhanden.")


# ════════════════════════════════════════════════════════════════════════════
# PAGE: EINSTELLUNGEN  (Upload · Mapping · ASF)
# ════════════════════════════════════════════════════════════════════════════
def page_einstellungen() -> None:
    ui.page_header("Einstellungen", "Daten laden, Brands mappen, ASF-Konditionen pflegen.",
                   status="Konfiguration", meta="Upload · Mapping · ASF")
    tab_up, tab_map, tab_asf = st.tabs(["Daten laden", "Mapping (Brands)", "ASF & DCC"])

    # ── Upload ──
    with tab_up:
        ui.section("Worldline-Export laden")
        uploaded = st.file_uploader("Worldline-Exporte (XLSB / CSV)",
                                    type=["xlsb", "csv"], accept_multiple_files=True)
        sheet_val = st.text_input("Sheet-Name (XLSB, leer = erstes Sheet)", value="WL")
        if uploaded and st.button("Laden & prüfen", type="primary"):
            tmp_dir = tempfile.mkdtemp(); paths = []
            for f in uploaded:
                p = str(Path(tmp_dir) / f.name)
                with open(p, "wb") as fh:
                    fh.write(f.read())
                paths.append(p)
            try:
                dfn, rpt = ingest_files(paths, DB_PATH, sheet=sheet_val.strip() or None)
                st.session_state.df = dfn; st.session_state.report = rpt
                st.success(f"{num(rpt.rows_new)} Zeilen geladen.")
                st.rerun()
            except Exception as exc:
                st.error(f"Fehler beim Laden: {exc}")
            finally:
                import shutil
                shutil.rmtree(tmp_dir, ignore_errors=True)

        # Convenience: load an export already present in data/ (no re-upload).
        existing = sorted(p.name for ext in ("*.xlsb", "*.csv")
                          for p in (ROOT / "data").glob(ext))
        if existing:
            pick = st.selectbox("Oder vorhandene Datei aus data/ laden",
                                ["—"] + existing)
            if st.button("Aus data/ laden") and pick != "—":
                try:
                    dfn, rpt = ingest_files([str(ROOT / "data" / pick)], DB_PATH,
                                            sheet=sheet_val.strip() or None)
                    st.session_state.df = dfn; st.session_state.report = rpt
                    st.success(f"«{pick}» geladen.")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Fehler beim Laden: {exc}")

        rpt = st.session_state.report
        if rpt is not None:
            ui.section("Abgleichsbericht")
            ui.kpi_row([
                {"label": "Neue Zeilen", "value": num(rpt.rows_new), "accent": ui.GREEN},
                {"label": "Übersprungen", "value": num(rpt.rows_skipped_overlap),
                 "accent": ui.CYAN},
                {"label": "Blockierte Dateien", "value": str(len(rpt.files_blocked_hash)),
                 "accent": ui.ORANGE},
            ])
            if rpt.files_blocked_hash:
                st.warning(f"Bit-identisch blockiert: {', '.join(rpt.files_blocked_hash)}")
            if rpt.fanout_partner_ids:
                st.warning(f"Fan-out-Verdacht: {', '.join(rpt.fanout_partner_ids)} — "
                           "vor Auswertung manuell prüfen.")
        if not df.empty:
            data_brands = df["brand"].dropna().astype(str).unique().tolist() \
                if "brand" in df else []
            _, unmapped = master.reconcile(data_brands)
            if unmapped:
                st.error("Unbekannte Brands (nicht in der Stammliste): "
                         + ", ".join(f"«{b}»" for b in unmapped)
                         + " — im Tab «Mapping» pflegen. Werden sonst wie nicht-"
                         "anbietbar behandelt (Worldline 1:1).")
            else:
                st.success("Alle Brands im Export sind der Stammliste zugeordnet.")

    # ── Mapping ──
    with tab_map:
        ui.section("Brand-Stammliste", "git-versioniert · config/brands.json")
        st.caption("Logisches Brand = ein/mehrere Such-Codes (Aliase). Typ steuert die "
                   "ASF. Spezial-Brands sind nie anbietbar (Worldline 1:1).")
        rows = [{"Anzeigename": r.display_name, "Typ": r.type, "Anbietbar": r.offerable,
                 "Reihenfolge": r.order, "Such-Codes": ", ".join(r.search_codes)}
                for r in master.sorted_brands()]
        edit = st.data_editor(pd.DataFrame(rows), hide_index=True, num_rows="dynamic",
            use_container_width=True, column_config={
                "Typ": st.column_config.SelectboxColumn(
                    options=["debit", "credit", "credit2", "spezial"]),
                "Anbietbar": st.column_config.CheckboxColumn()}, key="master_editor")
        if st.button("Stammliste speichern", type="primary"):
            try:
                recs = []
                for _, r in edit.iterrows():
                    name = str(r["Anzeigename"]).strip()
                    if not name:
                        continue
                    codes = [c.strip() for c in str(r["Such-Codes"]).split(",") if c.strip()]
                    recs.append(BrandRecord(name, str(r["Typ"]).strip(),
                                            bool(r["Anbietbar"]), int(r["Reihenfolge"]),
                                            codes or [name]))
                nm = BrandMaster(recs); nm.save(); st.session_state.master = nm
                st.success("Stammliste gespeichert (config/brands.json)."); st.rerun()
            except Exception as exc:
                st.error(f"Konnte nicht speichern: {exc}")

    # ── ASF & DCC ──
    with tab_asf:
        ui.section("Konditionen", "ASF · Trx-Fee · Mindestgebühr · DCC")
        st.caption("ASF-Sätze sind Platzhalter (bewusst hoch). Vor jedem Kundenlauf das "
                   "echte SwiPay-Preisblatt eintragen.")
        # Seed the widget keys from the durable profile in the SAME run the
        # widgets render (setdefault preserves edits; avoids the cross-run
        # cleanup that would otherwise reset them to 0).
        for _t in OFFERABLE_TYPES:
            st.session_state.setdefault(f"asf_{_t}", round(profile.type_rates[_t].asf_pct * 100, 4))
            st.session_state.setdefault(f"trx_{_t}", round(profile.type_rates[_t].trx_fee * 100, 4))
            st.session_state.setdefault(f"mf_{_t}",  round(profile.type_rates[_t].min_fee, 2))
        st.session_state.setdefault("dcc_in", round(profile.dcc_pct * 100, 2))

        new_mode = st.radio("Eingabemodus", ["schnell", "experte"],
            format_func=lambda m: "Schnellmodus" if m == "schnell"
            else "Expertenmodus (pro Brand)", horizontal=True,
            index=0 if profile.mode == "schnell" else 1)
        if new_mode != profile.mode:
            if new_mode == "experte":
                profile.brand_overrides = prefill_brand_overrides(master, profile)
            else:
                coll = conservative_collapse(master, profile)
                st.session_state.profile = coll
                for _t in OFFERABLE_TYPES:
                    st.session_state[f"asf_{_t}"] = round(coll.type_rates[_t].asf_pct*100, 4)
                    st.session_state[f"trx_{_t}"] = round(coll.type_rates[_t].trx_fee*100, 4)
                    st.session_state[f"mf_{_t}"]  = round(coll.type_rates[_t].min_fee, 2)
                st.info("Expertenwerte konservativ zusammengefasst (höchster Wert je Typ "
                        "und Variable). Brand-Werte bleiben erhalten.")
            profile.mode = new_mode
            st.session_state.profile = profile

        profile.dcc_pct = st.number_input("SwiPay DCC-Satz (%)", min_value=0.0,
                                          max_value=5.0, step=0.05, format="%.2f",
                                          key="dcc_in") / 100.0

        if profile.mode == "schnell":
            cols = st.columns(3)
            for col, tkey in zip(cols, OFFERABLE_TYPES):
                with col:
                    st.markdown(f"**{_TYPE_LABEL[tkey]}**")
                    ap = st.number_input("ASF %", min_value=0.0, max_value=2.0, step=0.01,
                        format="%.3f", key=f"asf_{tkey}") / 100.0
                    tr = st.number_input("Trx-Fee (Rp.)", min_value=0.0, max_value=50.0,
                        step=0.5, format="%.2f", key=f"trx_{tkey}") / 100.0
                    mf = st.number_input("Mindestgeb. CHF", min_value=0.0, step=0.01,
                        format="%.2f", key=f"mf_{tkey}")
                    profile.type_rates[tkey] = TypeRate(ap, tr, mf)
        else:
            ov = profile.brand_overrides or prefill_brand_overrides(master, profile)
            rows = []
            for rec in master.offerable_brands():
                tr = ov.get(rec.display_name) or profile.type_rates[rec.type]
                rows.append({"Brand": rec.display_name, "Typ": _TYPE_LABEL.get(rec.type, rec.type),
                             "ASF %": round(tr.asf_pct*100, 4),
                             "Trx-Fee Rp.": round(tr.trx_fee*100, 2),
                             "Min CHF": round(tr.min_fee, 2)})
            ed = st.data_editor(pd.DataFrame(rows), hide_index=True,
                use_container_width=True, disabled=["Brand", "Typ"], key="expert_editor")
            profile.brand_overrides = {
                str(r["Brand"]): TypeRate(float(r["ASF %"])/100.0,
                                          float(r["Trx-Fee Rp."])/100.0, float(r["Min CHF"]))
                for _, r in ed.iterrows()}
        st.session_state.profile = profile


# ── Router ──────────────────────────────────────────────────────────────────
if page == "Präsentation":
    page_praesentation()
elif page == "Transaktionen":
    page_transaktionen()
elif page == "Partner & Gruppen":
    page_partner()
else:
    page_einstellungen()
