"""
SwiPay Worldline-Vergleichstool — Streamlit-Oberfläche im SwiPay-CI.
Start: uv run streamlit run app.py

Navigierte App: Präsentation (Kundentermin), Transaktionen, Merchants,
Einstellungen (Import · Mapping · ASF · Merchants · Reset). Design-System in
src/ui.py.
"""

from __future__ import annotations

import sys
import tempfile
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

import pandas as pd
import streamlit as st

import ui
import session_store
import case_store as cases
import filters
import hochrechnung_store as hoch_store
from pipeline import run_comparison, totals as engine_totals
from ingest import ingest_files, init_db
from projection import CoverageLabel, volume_bases
from aggregation import EntityInput, aggregate
from view import BASIS_IST, BASIS_PA, RATE_DEC, build_view
from db_groups import init_groups_db, get_groups, assign_group, unassign_group
from reporter import build_pdf, build_csv
from version import TOOL_VERSION
from settings import (
    ALL_TYPES,
    OFFERABLE_TYPES,
    TEMPLATE_ARTEN,
    TEMPLATE_ART_LABEL,
    BrandRecord,
    BrandMaster,
    RateProfile,
    TypeRate,
    build_param_table,
    conservative_collapse,
    default_rate_profile,
    delete_template,
    list_templates,
    load_brand_master,
    load_template,
    prefill_brand_overrides,
    update_template_meta,
    save_template,
)

st.set_page_config(page_title="SwiPay · Payment Benchmarking", layout="wide",
                   initial_sidebar_state="expanded")
ui.inject_css()

ROOT    = Path(__file__).parent
DB_PATH = str(ROOT / "data" / "swipay.db")
(ROOT / "data").mkdir(exist_ok=True)

# Einzige Quelle fuer Kartentyp-Namen: Ersparnis-Aufschluesselung, ASF-Eingabe
# und Brand-Stammliste. Keys sind die Typ-Schluessel aus settings.ALL_TYPES.
# Der Schluessel bleibt "spezial" (settings.ALL_TYPES, config/brands.json) --
# nur die Anzeige heisst QR-Code. Ein Schluessel-Rename waere eine Migration
# der git-versionierten Stammliste ohne funktionalen Gewinn.
_TYPE_LABEL = {"debit": "Debit", "credit": "Credit M/V",
               "credit2": "Credit Rest", "spezial": "QR-Code"}
_TYPE_KEY = {v: k for k, v in _TYPE_LABEL.items()}   # Label -> Schluessel

# Sammelzeile der Aufschluesselung: "spezial" UND nicht zuordenbare Brands
# landen hier zusammen. Beide haben per Definition Delta 0 (SwiPay spiegelt
# Worldline), eine Trennung ergaebe nur zwei Null-Zeilen.
_TYPE_SPECIAL = "QR-Code / n/a"

# Feste Anzeigereihenfolge fuer JEDE Kartentyp-Aufschluesselung -- nie nach
# Betrag sortiert, damit im Kundentermin immer dieselbe Zeile an derselben
# Stelle steht.
_TYPE_ORDER = ["Debit", "Credit M/V", "Credit Rest", _TYPE_SPECIAL]


def _type_bucket(t: str | None) -> str:
    """Label fuer die Ersparnis-Aufschluesselung (siehe _TYPE_SPECIAL)."""
    return _TYPE_LABEL[t] if t in OFFERABLE_TYPES else _TYPE_SPECIAL
HIST_BINS   = [0, 10, 50, 100, 200, 500, float("inf")]
HIST_LABELS = ["0–10", "10–50", "50–100", "100–200", "200–500", "500+"]

chf, num, pct, chf_c = ui.chf, ui.num, ui.pct, ui.chf_compact

# RATE_DEC comes from view.py -- the same precision is used in the PDF.
def pct_rate(frac: float) -> str:
    """A fee rate as a percentage of turnover, e.g. 0.007544 -> "0.754 %"."""
    return f"{frac * 100:.{RATE_DEC}f} %"


init_db(DB_PATH)
init_groups_db(DB_PATH)
hoch_store.init_hochrechnung_db(DB_PATH)

# One-time migration: the old session-scoped Hochrechnung snapshot
# (data/session/hochrechnung.json) becomes durable, Partner-ID/Gruppenname-
# keyed rows in swipay.db (see hochrechnung_store.py). Renaming the legacy
# file after a successful migration makes this a no-op on every later start.
_LEGACY_HOCH_JSON = ROOT / "data" / "session" / "hochrechnung.json"
if _LEGACY_HOCH_JSON.exists():
    import json as _json
    _legacy_vals = _json.loads(_LEGACY_HOCH_JSON.read_text(encoding="utf-8"))
    hoch_store.migrate_from_session_json(DB_PATH, _legacy_vals)
    _LEGACY_HOCH_JSON.rename(_LEGACY_HOCH_JSON.with_suffix(".json.migrated"))

# ── Session state ─────────────────────────────────────────────────────────────
# df and profile survive an app restart: loaded from data/session/ on first
# access, cleared only by the explicit reset button in Einstellungen.
for _k, _v in [("report", None), ("nav", "Präsentation")]:
    if _k not in st.session_state:
        st.session_state[_k] = _v
if "df" not in st.session_state:
    st.session_state.df = session_store.load_df()
    if st.session_state.df is None:
        st.session_state.df = pd.DataFrame()
if "master" not in st.session_state:
    st.session_state.master = load_brand_master()
if "profile" not in st.session_state:
    st.session_state.profile = session_store.load_profile() or default_rate_profile()

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
    vb = volume_bases(fdf)
    return {
        "brutto": brutto, "n_txn": len(fdf), "n_purch": n_p,
        "n_term": int(fdf["terminal_id"].nunique()) if "terminal_id" in fdf else 0,
        "avg_ticket": brutto / n_p if n_p else 0.0,
        "diff": t["wl_net"] - t["sp_net"],
        "dcc_adv": t["sp_cashback"] - t["wl_cashback"],
        "dcc_vol": vb.dcc_net, "fx_vol": vb.fx_net,
        "dcc_vol_purch": vb.dcc_purchase, "fx_vol_purch": vb.fx_purchase,
    }


def _order_types(g: pd.DataFrame) -> pd.DataFrame:
    """Bring a Typ/Ersparnis-Frame in die feste Reihenfolge _TYPE_ORDER.
    Unbekannte Typen laufen hinten mit, statt still zu verschwinden."""
    rank = {t: i for i, t in enumerate(_TYPE_ORDER)}
    return (g.assign(_o=g["Typ"].map(lambda t: rank.get(t, len(rank))))
            .sort_values("_o", kind="stable").drop(columns="_o")
            .reset_index(drop=True))


def _savings_by_type_scaled(entities, scales: list[float]) -> pd.DataFrame:
    """Ersparnis nach Kartentyp, je Entity mit IHREM eigenen Hochrechnungs-
    faktor skaliert (scales ist positionsgleich zur entities-Liste, siehe
    aggregation.AggregateProjection.scales). Kein gemischter Durchschnitts-
    faktor -- die Summe deckt sich mit agg.saving_annual."""
    acc: dict[str, float] = {}
    for e, scale in zip(entities, scales):
        if e.df.empty:
            continue
        comp = run_comparison(e.df, params, offer, profile.dcc_pct)
        types = [master.type_of(b) for b in e.df["brand"].astype(str)]
        saving = (comp["wl_net"] - comp["sp_net"]).to_numpy(float) * scale
        for typ, val in zip(types, saving):
            lbl = _type_bucket(typ)
            acc[lbl] = acc.get(lbl, 0.0) + float(val)
    g = pd.DataFrame({"Typ": list(acc), "Ersparnis": list(acc.values())})
    return g[g["Ersparnis"].abs() > 0.005] if not g.empty else g


def _savings_by_type(fdf: pd.DataFrame, comp: pd.DataFrame) -> pd.DataFrame:
    types = [master.type_of(b) for b in fdf["brand"].astype(str)]
    tmp = pd.DataFrame({
        "Typ": [_type_bucket(x) for x in types],
        "Ersparnis": (comp["wl_net"] - comp["sp_net"]).values,
    })
    g = tmp.groupby("Typ", as_index=False)["Ersparnis"].sum()
    return g[g["Ersparnis"].abs() > 0.005]


def _monthly(fdf: pd.DataFrame, comp: pd.DataFrame) -> pd.DataFrame:
    if "_month" not in fdf.columns:
        return pd.DataFrame()
    m = fdf.copy()
    m["Aktuell"] = comp["wl_net"].values
    m["SP"] = comp["sp_net"].values
    m["_pb"] = m["brutto"].where(~m["is_refund"], 0.0)
    out = (m.groupby("_month").agg(Aktuell=("Aktuell", "sum"), SP=("SP", "sum"),
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


def _fees_by_brand(fdf: pd.DataFrame) -> pd.DataFrame:
    """Ist-Gebühren pro Brand (nur Käufe), in Reihenfolge der Brand-Stammliste
    (config/brands.json, order-Feld). Gebühren Total ist die rohe, vorzeichen-
    richtige Basis (wie pipeline.wl_fee) -- vor Abzug des DCC-Cashbacks, der
    separat in dcc_payback steht. ASF/ICF/CSF fehlen bei Zeilen ohne
    Komponenten-Aufschlüsselung (v. a. TWINT, siehe CLAUDE.md) -- dort bleibt
    die Summe auf 0, _has_* markiert das für die Anzeige (leer statt 0)."""
    p = fdf[~fdf["is_refund"]].copy()
    if p.empty:
        return pd.DataFrame()
    is_txn = p["brutto"].notna() | p["gebuehren"].notna()
    p = p[is_txn]
    if p.empty:
        return pd.DataFrame()

    p["_geb"] = -p["gebuehren"].fillna(0.0)
    has_comp = p[["processing_fee", "interchange", "scheme_fee"]].notna().any(axis=1)
    p["_asf"] = p["processing_fee"].abs().where(has_comp)
    p["_icf"] = p["interchange"].abs().where(has_comp)
    p["_csf"] = p["scheme_fee"].abs().where(has_comp)

    codes = p["brand"].astype(str)
    recs = {c: master.match(c) for c in codes.unique()}

    def _label(c: str) -> str:
        if recs[c]:
            return recs[c].display_name
        return "n/a" if c.strip().lower() in ("nan", "none", "") else c

    p["_brand"] = codes.map(_label)
    p["_order"] = codes.map(lambda c: recs[c].order if recs[c] else 10_000)

    g = p.groupby("_brand", as_index=False).agg(
        _order=("_order", "min"), Umsatz=("brutto", "sum"),
        Anzahl=("brutto", "count"), GebTotal=("_geb", "sum"),
        ASF=("_asf", "sum"), ICF=("_icf", "sum"), CSF=("_csf", "sum"),
        _has_asf=("_asf", lambda s: bool(s.notna().any())),
        _has_icf=("_icf", lambda s: bool(s.notna().any())),
        _has_csf=("_csf", lambda s: bool(s.notna().any())),
    )
    return g.sort_values(["_order", "_brand"], kind="stable").reset_index(drop=True)


def _render_fees_by_brand(fdf: pd.DataFrame) -> None:
    g = _fees_by_brand(fdf)
    if g.empty:
        st.caption("Keine Käufe in dieser Auswahl.")
        return

    def _rate(fee: float, base: float) -> str:
        return pct_rate(fee / base) if base else "–"

    rows = []
    for _, r in g.iterrows():
        rows.append({
            "Brand": r["_brand"], "Umsatz": chf(r["Umsatz"]),
            "Anzahl Trx": num(r["Anzahl"]),
            "Gebühren Total": chf(r["GebTotal"]),
            "Ø-Satz": _rate(r["GebTotal"], r["Umsatz"]),
            "ASF-Satz": _rate(r["ASF"], r["Umsatz"]) if r["_has_asf"] else "–",
            "ICF-Satz": _rate(r["ICF"], r["Umsatz"]) if r["_has_icf"] else "–",
            "CSF-Satz": _rate(r["CSF"], r["Umsatz"]) if r["_has_csf"] else "–",
        })

    tot = g[["Umsatz", "Anzahl", "GebTotal", "ASF", "ICF", "CSF"]].sum()
    any_asf, any_icf, any_csf = g["_has_asf"].any(), g["_has_icf"].any(), g["_has_csf"].any()
    rows.append({
        "Brand": "Total", "Umsatz": chf(tot["Umsatz"]),
        "Anzahl Trx": num(tot["Anzahl"]),
        "Gebühren Total": chf(tot["GebTotal"]),
        "Ø-Satz": _rate(tot["GebTotal"], tot["Umsatz"]),
        "ASF-Satz": _rate(tot["ASF"], tot["Umsatz"]) if any_asf else "–",
        "ICF-Satz": _rate(tot["ICF"], tot["Umsatz"]) if any_icf else "–",
        "CSF-Satz": _rate(tot["CSF"], tot["Umsatz"]) if any_csf else "–",
    })

    st.dataframe(pd.DataFrame(rows), hide_index=True, use_container_width=True)
    if not (g["_has_asf"].all() and g["_has_icf"].all() and g["_has_csf"].all()):
        st.caption("«–» = keine Komponenten-Aufschlüsselung in den Rohdaten für "
                   "diesen Brand (v. a. TWINT).")


def _apply_period(df: pd.DataFrame, frm, to) -> pd.Series:
    if frm and to and "_month" in df.columns:
        return df["_month"].between(frm, to, inclusive="both")
    return pd.Series(True, index=df.index)


# ── Build engine params (all pages) ───────────────────────────────────────────
params, offer = build_param_table(master, profile, mode=profile.mode)

# ── SIDEBAR ────────────────────────────────────────────────────────────────────
with st.sidebar:
    ui.sidebar_brand()
    NAV = ["📊  Präsentation", "⇄  Transaktionen", "👥  Merchants", "⚙  Einstellungen"]
    _CLEAN = {n: n.split("  ", 1)[1] for n in NAV}
    sel = st.radio("Navigation", NAV,
                   index=[_CLEAN[n] for n in NAV].index(st.session_state.nav),
                   label_visibility="collapsed")
    st.session_state.nav = _CLEAN[sel]
    page = st.session_state.nav
    st.markdown(
        '<div style="margin-top:1.4rem;font-size:.7rem;color:#8a9495;'
        f'letter-spacing:.04em">Payment Benchmarking {TOOL_VERSION} · Staging<br>'
        'IC++ gegen IC++</div>',
        unsafe_allow_html=True)

df = _ensure_month(st.session_state.df)


def _case_name_guess(daten: list[dict]) -> str:
    """Customer name from the export file name ("Analyse_Davos-Klosters.xlsb"
    -> "Davos Klosters"). Only used to seed a name the user can rename."""
    for entry in daten:
        stem = Path(str(entry.get("file_name", ""))).stem
        if not stem:
            continue
        for prefix in ("Analyse_", "Analyse-", "Export_", "Export-"):
            if stem.startswith(prefix):
                stem = stem[len(prefix):]
        cleaned = " ".join(stem.replace("_", " ").replace("-", " ").split())
        if cleaned:
            return cleaned
    return "Übernommener Stand"


# One-time takeover: the state that exists on this machine today (Konditionen,
# Gruppen, Hochrechnungen, loaded export) becomes a real case, so nothing that
# was built by hand lives only in the working session. Runs once -- guarded by
# the cases directory not existing yet.
if not cases.CASES_DIR.exists():
    try:
        _daten = cases.resolve_data_files(DB_PATH, df)
        _kunde = _case_name_guess(_daten)
        _payload = cases.build_payload(
            variant_name=f"Stand {pd.Timestamp.today().strftime('%Y-%m-%d')}",
            variant_note="Automatisch übernommen beim Einführen der Fall-Speicherung.",
            profile=profile, master=master,
            groups=get_groups(DB_PATH),
            partner_vols=hoch_store.get_partner_volumes(DB_PATH),
            group_vols=hoch_store.get_group_volumes(DB_PATH),
            auswahl={}, daten=_daten)
        cases.save_variant(_kunde, _payload,
                           kunde_notiz="Beim Einführen der Fall-Speicherung übernommen.",
                           export_src=cases.find_export_source(_daten))
    except Exception:
        # Never let the takeover block the app; the Fälle tab can save manually.
        cases.CASES_DIR.mkdir(parents=True, exist_ok=True)

# Gate: without data, only Einstellungen is useful.
if df.empty and page != "Einstellungen":
    ui.page_header("Willkommen", "Lade zuerst einen Worldline-Export, dann geht's los.",
                   status="Bereit", meta="Payment Benchmarking")
    ui.info_banner("Noch keine Daten geladen. Wechsle zu <b>⚙ Einstellungen → Daten "
                   "laden</b> und lade einen Worldline-Export (XLSB/CSV).")
    st.stop()


# ════════════════════════════════════════════════════════════════════════════
# PAGE: PRÄSENTATION
# ════════════════════════════════════════════════════════════════════════════
def page_praesentation() -> None:
    months = _months(df)
    pid_col = "partner_id" if "partner_id" in df.columns else None

    # Auswahl aus einem geladenen Fall wiederherstellen. Bewusst hier und nicht
    # beim Laden: erst hier sind Optionen und Monate bekannt, und was die Daten
    # nicht hergeben (Zeitraum ausserhalb) wird gesagt statt still verschoben.
    _res = st.session_state.pop("auswahl_restore", None)
    _res_notes: list[str] = []
    _res_sel: list[str] = []
    _res_range = None
    if _res:
        _res_sel = [s for s in _res.get("partner_gruppen") or []]
        _z = _res.get("zeitraum") or []
        if len(_z) == 2 and _z[0] in months and _z[1] in months:
            _res_range = (_z[0], _z[1])
        elif _z:
            _res_notes.append(
                f"Zeitraum des Falls ({_z[0]} bis {_z[1]}) liegt nicht in den "
                "geladenen Daten — es gilt der vollständige Zeitraum.")
        if _res.get("basis"):
            st.session_state["praes_basis"] = (
                "Hochrechnung p.a." if _res["basis"] == BASIS_PA else "Ist-Zeitraum")

    # Selection row
    c1, c2, c3 = st.columns([1.15, 1.75, 1.1])
    with c1:
        scope = st.radio("Auswahl", ["Alle", "Auswahl"], horizontal=True,
                         label_visibility="collapsed",
                         index=1 if (_res and _res.get("scope") == "Auswahl") else 0)
    groups = get_groups(DB_PATH)
    sel_display: list[str] = []
    sel_group_names: list[str] = []
    sel_partner_pids: list[str] = []
    label = "Alle Partner"
    with c2:
        if scope == "Auswahl":
            partner_opts = _merchant_options(df, pid_col) if pid_col else []
            group_opts = [f"👥 {g}" for g in groups]
            combined_opts = sorted(partner_opts + group_opts, key=str.lower)
            _valid = [s for s in _res_sel if s in combined_opts]
            if _res_sel and len(_valid) < len(_res_sel):
                _res_notes.append(
                    "Nicht mehr vorhanden: "
                    + ", ".join(f"«{s}»" for s in _res_sel if s not in combined_opts))
            sel_display = st.multiselect(
                "Partner/Gruppe", combined_opts, default=_valid,
                label_visibility="collapsed",
                placeholder="Partner oder Gruppe suchen (Name oder Partner-ID)")
            sel_group_names = [s[2:] for s in sel_display if s.startswith("👥 ")]
            sel_partner_pids = [_pid_from_option(s) for s in sel_display
                                if not s.startswith("👥 ")]
            if sel_display:
                label = ", ".join(sel_display)
            if not groups:
                st.caption("Noch keine Gruppen — unter «Einstellungen → Merchants» anlegen.")
    frm = to = None
    if len(months) >= 2:
        frm, to = st.select_slider("Zeitraum", options=months,
                                   value=_res_range or (months[0], months[-1]))
    elif months:
        frm = to = months[0]

    # Filter -- eine Auswahl ist die Vereinigung aller ausgewaehlten Partner-IDs
    # und der Mitglieder aller ausgewaehlten Gruppen (Partner, die in mehreren
    # Auswahl-Elementen vorkommen, werden dedupliziert, nicht doppelt gezaehlt).
    sel_pids = None
    if scope == "Auswahl":
        combined = set(sel_partner_pids)
        for gn in sel_group_names:
            combined.update(ui.pid(p) for p in groups.get(gn, []))
        sel_pids = sorted(combined)

    mask = pd.Series(True, index=df.index)
    if sel_pids is not None and pid_col:
        mask &= df[pid_col].astype(str).map(ui.pid).isin(sel_pids)
    mask &= _apply_period(df, frm, to)
    fdf = df[mask].reset_index(drop=True)

    partner_disp = label
    is_single_partner = (len(sel_display) == 1 and not sel_display[0].startswith("👥 "))
    if scope == "Auswahl" and is_single_partner and "partner_name" in fdf.columns and not fdf.empty:
        names = fdf["partner_name"].dropna().unique().tolist()
        if len(names) == 1:
            partner_disp = names[0]

    ui.page_header(
        f"Payment Benchmarking · {partner_disp}",
        "Dein Konditionenvergleich mit SwiPay auf einen Blick.",
        status="Staging",
        meta=f"Zeitraum {frm or '–'} bis {to or '–'} · IC++ gegen IC++",
    )

    if fdf.empty:
        ui.info_banner("Keine Transaktionen für diese Auswahl.")
        return

    comp = run_comparison(fdf, params, offer, profile.dcc_pct)
    t = engine_totals(comp)
    d = _derive(fdf, comp, t)

    # ── Hochrechnung aufbauen (aus Einstellungen übernommen) ──────────────────
    partner_vols = hoch_store.get_partner_volumes(DB_PATH)
    group_vols = hoch_store.get_group_volumes(DB_PATH)
    if scope == "Alle":
        entities = _entities_for_rows(_merchant_rows(df, pid_col, groups),
                                      partner_vols, group_vols, frm, to)
    elif scope == "Auswahl" and (sel_group_names or sel_partner_pids):
        entities = _entities_for_selection(
            df, pid_col, groups, sel_group_names, sel_partner_pids,
            partner_vols, group_vols, frm, to)
    else:
        entities = []
    agg = aggregate(entities, params, offer, profile.dcc_pct)
    has_proj = agg is not None and agg.has_projection

    # Ansicht: p.a. ist der Standard, sobald eine Hochrechnung existiert. Der
    # Umschalter bleibt erreichbar, damit die Rohbasis im Kundentermin
    # nachvollziehbar ist -- ohne Hochrechnung gibt es nichts umzuschalten.
    with c3:
        if has_proj:
            basis_lbl = st.radio(
                "Ansicht", ["Hochrechnung p.a.", "Ist-Zeitraum"], horizontal=True,
                label_visibility="collapsed", key="praes_basis")
            basis = BASIS_PA if basis_lbl.startswith("Hochrechnung") else BASIS_IST
        else:
            basis = BASIS_IST
            st.caption("Keine Hochrechnung hinterlegt — **Einstellungen → "
                       "Merchants → Hochrechnung**.")

    # Was gerade gezeigt wird -- der Fälle-Tab liest das beim Speichern. Ein
    # Fall ohne Auswahl liesse dieselbe Datenbasis mit anderem Scope zu, und
    # das ergäbe eine andere Zahl im Hero.
    st.session_state["auswahl_snapshot"] = {
        "scope": scope, "partner_gruppen": list(sel_display),
        "zeitraum": [frm, to], "basis": basis,
    }
    if _res_notes:
        ui.info_banner("Aus dem Fall übernommen — mit Abweichungen: "
                       + " · ".join(_res_notes))

    v = build_view(agg, t, d, basis)
    acc_total = ui.GREEN if v.total >= 0 else ui.ROT

    # ── HERO: geldwerter Vorteil ──────────────────────────────────────────────
    hcol, kcol = st.columns([1.15, 1])
    with hcol:
        if basis == BASIS_PA:
            cov = agg.coverage
            # Der Hero zeigt IMMER den errechneten Punktwert -- denselben, den
            # die Detailkachel "Geldwerter Vorteil" und die Typ-Aufschlüsselung
            # tragen. Frueher fuehrte bei indikativer Deckung das konservative
            # Bandende, was oben und unten zwei verschiedene Zahlen ergab. Die
            # Vorsicht steckt jetzt sichtbar im Planungsband und im
            # Deckungs-Banner, nicht in einer stillen Ersetzung der Headline.
            cov_txt = {CoverageLabel.SIMULATABLE: "hohe Deckung",
                       CoverageLabel.LOW_COVERAGE: "mittlere Deckung",
                       CoverageLabel.INDICATIVE: "indikativ"}[cov.label]
            ui.hero("Geldwerter Vorteil pro Jahr", f"CHF {chf(v.total, 0)}",
                    band=f"Planungsband CHF {chf(v.band_low, 0)} – "
                         f"{chf(v.band_high, 0)}",
                    foot=f"Deckungsgrad {cov.coverage_pct:.0%} · {cov_txt} · "
                         f"Basis Jahresumsatz CHF {chf(v.brutto, 0)}",
                    accent=acc_total)
            ui.coverage_banner(cov.label, cov.coverage_pct)
            n_total = len(agg.used) + len(agg.skipped)
            st.caption(
                f"Portfolio-Abdeckung: {agg.portfolio_coverage_pct:.0%} des "
                f"Ist-Bruttoumsatzes hochgerechnet ({len(agg.used)} von {n_total} "
                "Merchants/Gruppen).")
            for cluster in agg.duplicate_groups:
                st.warning(
                    f"Möglicher Fan-out: {', '.join(cluster)} haben denselben "
                    "Jahresumsatz hinterlegt — wird dennoch summiert. Bitte prüfen.")
        else:
            ui.hero("Geldwerter Vorteil im Zeitraum", f"CHF {chf(v.total, 0)}",
                    foot=(f"Ist-Werte {frm or '–'} bis {to or '–'} · "
                          f"Bruttoumsatz CHF {chf(v.brutto, 0)}"
                          + ("" if has_proj else
                             " · keine Hochrechnung hinterlegt")),
                    accent=acc_total)
    with kcol:
        # Veraenderung, nicht Reduktion: das Vorzeichen zeigt die Richtung der
        # Gebuehren -- sinken sie, steht ein Minus (gruen). Der Nullfall wird
        # abgefangen, sonst formatiert Python die negative Null als "-0.0 %".
        chg = v.fee_change_pct
        chg_txt = "0.0 %" if abs(chg) < 0.05 else f"{chg:+.1f} %"
        # Minus = Gebühren sinken = grün. Die Farbe sitzt hier auf der ZAHL,
        # weil das Vorzeichen die eigentliche Aussage der Kachel ist.
        tone_chg = ui.GREEN if v.rel_pct >= 0 else ui.ROT
        ui.kpi_row([
            {"label": f"Bruttoumsatz {v.suffix}",
             "value": f"CHF {chf_c(v.brutto)}",
             "foot": v.note, "accent": ui.BLUE},
            {"label": "Gebührenveränderung", "value": chg_txt,
             "foot": "vs. Aktuell", "accent": tone_chg,
             "value_color": tone_chg},
        ])
        # Effektive Gebührenrate in % vom Umsatz -- vergleichbar mit jedem
        # anderen Angebot, unabhängig von der Umsatzgrösse.
        if v.wl_rate is not None:
            dlt = v.rate_delta
            # Richtung ausschreiben: ein nacktes "+0.085" liest sich, als wäre
            # SwiPay teurer, obwohl es die Ersparnis ist.
            # Gross die Gesamtrate (Disagio), klein darunter die Komponente,
            # die den Unterschied macht: SwiPays ASF gegen Worldlines
            # Processing Fee. ICF/CSF laufen als Pass-through identisch durch,
            # deshalb ist das der einzige variable Hebel. Bewusst NICHT «davon»
            # genannt -- die Gesamtrate ist netto nach DCC-Cashback, die ASF
            # ist also kein reiner Teilbetrag davon.
            # Worldline nennt die Komponente "Processing Fee" -- fachlich
            # dasselbe wie die ASF, deshalb heisst hier beides ASF.
            # Die kleine Zahl auf der SwiPay-Seite ist die Veränderung DER ASF
            # (nicht die Gesamtersparnis), Vorzeichen wie bei der
            # Gebührenveränderung: Minus = günstiger.
            wl_asf_foot = (f"Ø ASF {pct_rate(v.wl_processing_rate)}"
                           if v.wl_processing_rate is not None
                           else "vom Bruttoumsatz")
            sp_asf_foot = "vom Bruttoumsatz"
            if v.asf_rate is not None:
                sp_asf_foot = f"Ø ASF {pct_rate(v.asf_rate)}"
                achg = v.asf_change_pct
                if achg is not None:
                    achg_txt = ("0.0 %" if abs(achg) < 0.05
                                else f"{achg:+.1f} %")
                    sp_asf_foot += f" · {achg_txt}"
            ui.kpi_row([
                {"label": "Gebühren Total Aktuell", "value": pct_rate(v.wl_rate),
                 "foot": wl_asf_foot, "accent": ui.ANTHRAZIT},
                {"label": "Gebühren Total SwiPay",
                 "value": pct_rate(v.sp_rate), "foot": sp_asf_foot,
                 "accent": ui.GREEN if dlt >= 0 else ui.ROT},
            ])
        ui.kpi_row([
            {"label": f"Transaktionen {v.suffix}", "value": num(v.n_txn),
             "foot": v.note, "accent": ui.CYAN},
            {"label": "Ø Ticket", "value": f"CHF {chf(d['avg_ticket'])}",
             "foot": "aus dem Ist-Mix", "accent": ui.CYAN},
        ])

    # ── Woher der Vorteil kommt ───────────────────────────────────────────────
    # Zwei getrennte Hebel, bewusst unterschiedlich benannt: beim Acquiring
    # SPART der Händler Gebühren, beim DCC BEKOMMT er mehr Cashback. Erst die
    # Summe ist der geldwerte Vorteil.
    ui.section("Woher der Vorteil kommt",
               f"Acquiring + DCC = geldwerter Vorteil · {v.note} "
               f"({v.suffix})")
    acq, dccv, tot = v.acquiring, v.dcc, v.total
    share = (lambda x: f"{x / tot:.0%} des Vorteils") if tot else (lambda x: "")
    ui.kpi_row([
        {"label": f"Acquiring-Ersparnis {v.suffix}",
         "value": f"CHF {chf(acq, 0)}",
         "foot": ("gesparte Gebühren · " + share(acq)) if acq >= 0
                 else "höhere Gebühren als aktuell",
         "accent": ui.GREEN if acq >= 0 else ui.ROT},
        {"label": f"DCC-Mehrertrag {v.suffix}",
         "value": f"CHF {chf(dccv, 0)}",
         "foot": ("höherer Cashback · " + share(dccv)) if dccv >= 0
                 else "geringerer Cashback als aktuell",
         "accent": ui.CYAN if dccv >= 0 else ui.ROT},
        {"label": f"Geldwerter Vorteil {v.suffix}",
         "value": f"CHF {chf(tot, 0)}",
         "foot": ("Acquiring + DCC" if tot >= 0
                  else "SwiPay wäre teurer — kein Vorteil"),
         "accent": acc_total},
    ])
    # Zwei Vergleiche statt eines Wasserfalls: links die Acquiring-Gebühren
    # (was der Händler ZAHLT), rechts der DCC-Cashback (was er BEKOMMT). Beide
    # Deltas sind exakt die zwei Kacheln darüber.
    a, b = st.columns(2)
    with a:
        st.altair_chart(
            ui.chart_compare(v.wl_fee, v.sp_fee,
                             "Acquiring-Gebühren CHF", ui.ROT),
            use_container_width=True)
        st.caption(
            f"Gebühren vor DCC-Cashback: Aktuell CHF {chf(v.wl_fee, 0)} "
            f"gegen SwiPay CHF {chf(v.sp_fee, 0)} — "
            + (f"**CHF {chf(acq, 0)} gespart**." if acq >= 0
               else f"**CHF {chf(-acq, 0)} teurer**."))
    with b:
        st.altair_chart(
            ui.chart_compare(v.wl_cb, v.sp_cb, "DCC-Cashback CHF",
                             ui.CYAN),
            use_container_width=True)
        st.caption(
            f"Cashback aus DCC: Aktuell CHF {chf(v.wl_cb, 0)} gegen "
            f"SwiPay CHF {chf(v.sp_cb, 0)} — "
            + (f"**CHF {chf(dccv, 0)} mehr**." if dccv >= 0
               else f"**CHF {chf(-dccv, 0)} weniger**."))

    # ── Ersparnis nach Kartentyp ───────────────────────────────────────────────
    # Kein zweites WL-gegen-SwiPay-Balkenpaar mehr: der Wasserfall oben zeigt
    # dieselben zwei Aussenwerte bereits. Hier nur die Aufschlüsselung.
    ui.section("Ersparnis nach Kartentyp",
               f"Wo der Vorteil entsteht · {v.note} ({v.suffix})")
    # Bei p.a. je Entity mit IHREM Faktor skaliert, damit die Summe exakt dem
    # Wert im Hero entspricht.
    sbt = _order_types(_savings_by_type_scaled(entities, agg.scales)
                       if basis == BASIS_PA else _savings_by_type(fdf, comp))
    if not sbt.empty:
        a, b = st.columns([1.6, 1])
        with a:
            st.altair_chart(
                ui.chart_savings_by_type(sbt, order=_TYPE_ORDER),
                use_container_width=True)
        with b:
            # Dieselbe feste Reihenfolge wie im Chart, nicht nach Betrag.
            rows = sbt
            items = "".join(
                f"<div style='display:flex;justify-content:space-between;"
                f"gap:1rem;padding:.3rem 0;border-bottom:1px solid "
                f"rgba(62,75,76,.09)'><span>{r.Typ}</span>"
                f"<b>CHF {chf(r.Ersparnis, 0)}</b></div>"
                for r in rows.itertuples())
            st.markdown(
                f"<div class='sp-banner'>{items}"
                f"<div style='display:flex;justify-content:space-between;"
                f"gap:1rem;padding:.5rem 0 0'><span><b>Summe</b></span>"
                f"<b>CHF {chf(float(sbt['Ersparnis'].sum()), 0)}</b></div>"
                "</div>", unsafe_allow_html=True)
        st.caption("Nicht offerierbare Brands (z. B. TWINT) erscheinen bewusst "
                   "mit Null-Effekt — dort ändert SwiPay nichts.")
    else:
        st.caption("Keine offerierbaren Brands mit Effekt in dieser Auswahl.")

    # ── DCC ───────────────────────────────────────────────────────────────────
    ui.section("DCC", f"Cashback heute und Ausschöpfungs-Potenzial · {v.suffix}")
    # Ausschoepfungsquote auf der NETTO-Basis: so sind die Davos-Anker gelockt
    # (fx 4'336'735.23, dcc 905'721.92) und so stehen die Volumen in der
    # Caption.
    dcc_share = v.dcc_share
    # Obergrenze dagegen auf der KAUF-Basis: Cashback wird nur auf Kaeufe
    # gezahlt (siehe projection.VolumeBases). Auf der Netto-Basis ergaebe
    # sp_cashback / dcc_volumen 1.8518 % statt der eingestellten 1.85 % --
    # Zaehler und Nenner sassen auf verschiedenen Basen.
    cb_max = v.cashback_ceiling(profile.dcc_pct)
    # WL-Satz effektiv auf derselben Basis wie sp_cb (dcc_vol_purch, nicht die
    # Ceiling-Basis fx_vol_purch) -- sonst waeren Zaehler und Nenner nicht
    # vergleichbar (gleicher Fehler wie bei der SwiPay-Rate weiter oben).
    wl_rate = (v.wl_cb / v.dcc_vol_purch) if v.dcc_vol_purch else None
    a, b = st.columns([1, 1.35])
    with a:
        st.altair_chart(ui.chart_dcc_share(v.dcc_vol, v.fx_vol),
                        use_container_width=True)
        st.caption(f"{dcc_share:.1%} des DCC-fähigen Fremdwährungsvolumens "
                   f"laufen als DCC: CHF {chf(v.dcc_vol, 0)} von "
                   f"CHF {chf(v.fx_vol, 0)} ({v.note}, {v.suffix}).")
    with b:
        # JEDE Kachel traegt den Basis-Zusatz. Trug nur die erste ihn, lasen
        # sich die anderen wie Ist-Werte, obwohl sie hochgerechnet sind.
        sfx = v.suffix
        ui.kpi_row([
            {"label": f"Cashback SwiPay {sfx}",
             "value": f"CHF {chf(v.sp_cb, 0)}",
             "foot": f"{profile.dcc_pct*100:.2f} % auf {dcc_share:.1%} "
                     "Ausschöpfung", "accent": ui.CYAN},
            {"label": f"Cashback bei 100 % {sfx}",
             "value": f"CHF {chf(cb_max, 0)}",
             "foot": "theoretische Obergrenze", "accent": ui.BLUE},
        ])
        ui.kpi_row([
            {"label": f"Cashback Aktuell {sfx}",
             "value": f"CHF {chf(v.wl_cb, 0)}",
             "foot": f"{wl_rate*100:.2f} % effektiv" if wl_rate is not None
                     else "kein DCC-Volumen", "accent": ui.ORANGE},
            {"label": f"DCC-Kaufvolumen {sfx}",
             "value": f"CHF {chf_c(v.fx_vol_purch)}",
             "foot": "DCC-fähig, Basis der Obergrenze",
             "accent": ui.ANTHRAZIT},
        ])
    st.caption(
        "«Cashback bei 100 %» rechnet den SwiPay-Satz auf das gesamte "
        "DCC-fähige Kaufvolumen — eine Obergrenze, keine Prognose: volle "
        "Ausschöpfung setzt voraus, dass jeder Karteninhaber DCC annimmt.")

    # ── Zeit & Verteilung ──────────────────────────────────────────────────────
    # Bewusst immer Ist: ein Monatsverlauf lässt sich nicht hochrechnen, ohne
    # eine Saisonalität zu erfinden, die die Daten nicht hergeben.
    ui.section("Zeit & Verteilung",
               "Monatsverlauf, Transaktionsgrössen, Regionen · immer Ist-Werte")
    mdf = _monthly(fdf, comp)
    if not mdf.empty and len(mdf) >= 2:
        a, b = st.columns([1.4, 1])
        with a:
            st.altair_chart(ui.chart_monthly(mdf[["Monat", "Aktuell", "SP"]]),
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
                # Das PDF bekommt DASSELBE Zahlenpaket wie der Bildschirm und
                # folgt damit der gewählten Ansicht (p.a. oder Ist).
                pdf_bytes = build_pdf(
                    view=v,
                    partner_name=partner_disp,
                    period_from=frm or "–", period_to=to or "–",
                    n_terminals=d["n_term"], dcc_pct=profile.dcc_pct,
                    savings_by_type=[(r.Typ, float(r.Ersparnis))
                                     for r in sbt.itertuples()],
                    projection=agg if (agg and agg.has_projection) else None,
                    portfolio_coverage_pct=(agg.portfolio_coverage_pct
                                            if agg and agg.has_projection else None),
                    n_entities_used=len(agg.used) if agg else 0,
                    n_entities_total=(len(agg.used) + len(agg.skipped)) if agg else 0,
                    duplicate_volume_warnings=agg.duplicate_groups if agg else [],
                    fanout_partner_ids=(st.session_state.report.fanout_partner_ids
                                        if st.session_state.report else []),
                    zero_effect_brands=zero_brands,
                    mix_hints=[])
                # Bytes festhalten: der Download-Klick löst einen Rerun aus, in
                # dem der Generieren-Knopf wieder False ist. Ausserdem braucht
                # der Ablage-Knopf unten dieselben Bytes.
                st.session_state["last_pdf"] = (
                    pdf_bytes,
                    f"SwiPay_Analyse_{partner_disp}_{frm}_{to}.pdf"
                    .replace(" ", "_").replace(",", "").replace("/", "-"))
            except Exception as exc:
                st.error(f"PDF-Fehler: {exc}")
        _last = st.session_state.get("last_pdf")
        if _last:
            _bytes, _fname = _last
            st.download_button("PDF herunterladen", data=_bytes,
                               file_name=_fname, mime="application/pdf")
            _act = st.session_state.get("active_case")
            if _act:
                if st.button(f"Zum Fall «{_act['kunde_name']}» ablegen"):
                    try:
                        _p = cases.save_report(_act["kunde_slug"], _bytes,
                                               label=_act["variant_name"])
                        st.success(f"Bericht abgelegt: {_p.name}")
                    except Exception as exc:
                        st.error(f"Ablage fehlgeschlagen: {exc}")
            else:
                st.caption("Kein Fall aktiv — unter **Einstellungen → Fälle** "
                           "speichern, dann kann der Bericht dort abgelegt werden.")
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
    ui.page_header("Transaktionen", "Filtern, Monatsverlauf und Verteilung prüfen.",
                   status="Staging", meta="Umsatz pro Monat · Verteilung")
    months = _months(df)

    # Zweispaltig wie das Etrax-Dashboard: Filter links als Panel, Auswertung
    # rechts. Der Filter gilt NUR für diese Seite -- die Präsentation wählt
    # Partner und Gruppen für die Hochrechnung aus, ein Brand- oder
    # Terminal-Filter dort würde Kennzahlen und Kunden-PDF still verfälschen.
    fcol, mcol = st.columns([1, 3], gap="large")

    with fcol:
        with st.container(key="trx_filter"):
            st.markdown("**Filter & Zeitraum**")

            # Alle Widget-Keys tragen eine Generation. «Zurücksetzen» zählt sie
            # hoch, wodurch die Widgets neue Identitäten bekommen und leer
            # starten. Nur den session_state-Schlüssel zu löschen genügt NICHT:
            # das Frontend schickt seinen alten Wert zurück, und dann zeigt das
            # Feld «Visa», während die Kennzahlen ungefiltert rechnen.
            _gen = st.session_state.get("trx_gen", 0)
            _rk = f"trx_range_{_gen}"

            # Ein gespeicherter Bereich kann nach einem Exportwechsel auf
            # Monate zeigen, die es nicht mehr gibt -- der Schieber bricht
            # dann. Vorher prüfen statt hinterher abfangen.
            _saved = st.session_state.get(_rk)
            if _saved and (len(months) < 2 or _saved[0] not in months
                           or _saved[1] not in months):
                st.session_state.pop(_rk, None)

            def _set_range(rng) -> None:
                if rng:
                    st.session_state[_rk] = rng

            st.caption("Zeitraum")
            # Jahres-Schnellwahl als Knöpfe, nicht als st.pills: die Chips
            # sähen hübscher aus, lassen sich aber nicht automatisiert prüfen.
            # «Gesamt» auf eigener Zeile, damit in der schmalen Filterspalte
            # kein Label umbricht.
            if st.button("Gesamt", key="trx_y_all", use_container_width=True):
                if months:
                    _set_range((months[0], months[-1]))
                st.rerun()
            _years = filters.years_from_months(months)[:4]
            if _years:
                _yc = st.columns(len(_years))
                for _i, _y in enumerate(_years):
                    if _yc[_i].button(_y, key=f"trx_y_{_y}",
                                      use_container_width=True):
                        _set_range(filters.year_range(_y, months))
                        st.rerun()

            frm = to = None
            if len(months) >= 2:
                st.session_state.setdefault(_rk, (months[0], months[-1]))
                frm, to = st.select_slider(
                    "Monate", options=months, key=_rk,
                    label_visibility="collapsed")
            elif months:
                frm = to = months[0]

            # Leere Auswahl heisst «Alle» und filtert nicht (siehe filters.py).
            sel: dict[str, list[str]] = {}
            for _key, _col, _label in filters.TRX_FIELDS:
                _opts = filters.options_for(df, _col)
                sel[_key] = st.multiselect(_label, _opts, default=[],
                                           placeholder="Alle",
                                           key=f"trx_f_{_key}_{_gen}",
                                           disabled=not _opts)

            if st.button("Filter zurücksetzen", key="trx_reset",
                         use_container_width=True):
                for _k in [k for k in st.session_state if isinstance(k, str)
                           and (k.startswith("trx_f_") or k.startswith("trx_range_"))]:
                    st.session_state.pop(_k, None)
                st.session_state["trx_gen"] = _gen + 1
                st.rerun()

    fdf = df[filters.apply_filters(df, sel, frm, to)].reset_index(drop=True)

    with mcol:
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

        # Bruttoumsatz der Käufe, dieselbe Definition wie auf der Präsentation
        # (_monthly setzt Refunds auf 0) -- ein Monat mit vielen Gutschriften
        # wird sonst optisch kleiner, als er an Geschäft war.
        ui.section("Umsatz pro Monat", "Bruttoumsatz der Käufe · immer Ist-Werte")
        mdf = _monthly(fdf, comp)
        if mdf.empty:
            st.caption("Kein Datum in den Daten — kein Monatsverlauf möglich.")
        else:
            st.altair_chart(ui.chart_volume_monthly(mdf[["Monat", "Umsatz"]],
                                                    height=280),
                            use_container_width=True)

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

        ui.section("Gebühren pro Brand",
                   "Ist-Gebühren aus dem Export, nur Käufe, vor DCC-Cashback")
        _render_fees_by_brand(fdf)


# ════════════════════════════════════════════════════════════════════════════
# PAGE: MERCHANTS  (read-only; Gruppieren + Hochrechnung live in Einstellungen)
# ════════════════════════════════════════════════════════════════════════════
_MONEY = ["Umsatz", "Ø Ticket", "Aktuell-Geb.", "SP-Geb.", "Diff.", "DCC-Vtl."]
_MERCH_COLS   = ["Name", "Partner-ID", "Umsatz", "Ø Ticket", "Txn",
                 "Aktuell-Geb.", "SP-Geb.", "Diff.", "DCC-Vtl."]
_MERCH_WIDTHS = [2.4, 1.3, 1.2, 1.0, 0.8, 1.1, 1.1, 1.0, 1.0]


def _agg_merchant(sub: pd.DataFrame) -> dict:
    """One merchant's (or one group's) aggregate row. `sub` must already carry
    wl_net/sp_net/wl_cb/sp_cb/wl_fee/sp_fee (see _with_comparison).

    The display keys (capitalised, e.g. "Aktuell-Geb.") feed the Merchants table.
    The underscore keys carry the Ist decomposition an EntityInput needs to
    split its contribution into Acquiring vs DCC -- see _period_entity().
    """
    pu = sub[~sub["is_refund"]]
    n = len(pu)
    vb = volume_bases(sub)
    return {
        "Name": str(sub["partner_name"].iloc[0]) if "partner_name" in sub and n else "",
        "Umsatz": round(float(pu["brutto"].sum()), 2), "Txn": len(sub),
        "Ø Ticket": round(float(pu["brutto"].mean()), 2) if n else 0.0,
        "Aktuell-Geb.": round(float(sub["wl_net"].sum()), 2),
        "SP-Geb.": round(float(sub["sp_net"].sum()), 2),
        "Diff.": round(float(sub["wl_net"].sum() - sub["sp_net"].sum()), 2),
        "DCC-Vtl.": round(float(sub["sp_cb"].sum() - sub["wl_cb"].sum()), 2),
        "_wl_fee": float(sub["wl_fee"].sum()),
        "_sp_fee": float(sub["sp_fee"].sum()),
        "_wl_cb": float(sub["wl_cb"].sum()),
        "_sp_cb": float(sub["sp_cb"].sum()),
        "_dcc_vol": vb.dcc_net,
        "_fx_vol": vb.fx_net,
        "_dcc_vol_purch": vb.dcc_purchase,
        "_fx_vol_purch": vb.fx_purchase,
        "_sp_asf": float(sub["sp_asf"].sum()),
        "_wl_processing": float(sub["wl_processing"].sum()),
    }


def _with_comparison(df_subset: pd.DataFrame) -> pd.DataFrame:
    comp = run_comparison(df_subset, params, offer, profile.dcc_pct)
    out = df_subset.copy()
    out["wl_net"], out["sp_net"] = comp["wl_net"].values, comp["sp_net"].values
    out["wl_cb"], out["sp_cb"] = comp["wl_cashback"].values, comp["sp_cashback"].values
    # Fee level BEFORE DCC cashback -- the Acquiring leg of the advantage.
    out["wl_fee"], out["sp_fee"] = comp["wl_fee"].values, comp["sp_fee"].values
    # ASF-Ebene fuer den Ø-Satz-Vergleich.
    out["sp_asf"] = comp["sp_asf"].values
    out["wl_processing"] = comp["wl_processing"].values
    return out


# ── Jahresumsatz-Eingabe ──────────────────────────────────────────────────────
# st.number_input kann keine Tausender-Trennzeichen (format ist ein
# C-Format-String), deshalb ein Textfeld mit ui.parse_chf(). Angezeigt wird
# Schweizer Schreibweise mit Apostroph und zwei Dezimalen (1'159'429.00), damit
# man einer siebenstelligen Zahl beim Eintippen ansieht, was drin steht.

def _fmt_vol(v: float) -> str:
    return chf(float(v), 2)


def _volume_input(container, key: str, default: float, *,
                  label: str = "Jahresumsatz CHF",
                  label_visibility: str = "visible") -> float:
    """Formatiertes Jahresumsatz-Feld. Gibt den numerischen Wert zurueck.

    Der Zahlenwert lebt in <key>__num, der Text im Widget-Key <key>__txt. Bei
    unlesbarer Eingabe bleibt der letzte gueltige Wert stehen und es gibt einen
    sichtbaren Hinweis -- keine stille 0.

    Die beiden Keys werden UNABHAENGIG voneinander geseedet: num_key ist ein
    gewoehnlicher session_state-Eintrag und ueberlebt jeden Rerun, txt_key
    dagegen ist ein Widget-Key -- Streamlit raeumt den auf, sobald das Widget
    in einem Run nicht instanziiert wird (z. B. weil man auf eine andere Seite
    wechselt). Haengte man txt_key nur an "num_key not in session_state", blieb
    das Feld nach der Rueckkehr leer: num_key existierte noch, aber txt_key war
    von Streamlit bereits geloescht und wurde nie neu aus num_key befuellt --
    obwohl der Wert (und die Hochrechnung damit) im Hintergrund weiter stimmte.
    """
    txt_key, num_key, err_key = f"{key}__txt", f"{key}__num", f"{key}__err"
    if num_key not in st.session_state:
        st.session_state[num_key] = float(default)
    if txt_key not in st.session_state:
        st.session_state[txt_key] = _fmt_vol(st.session_state[num_key])

    def _sync() -> None:
        parsed = ui.parse_chf(st.session_state[txt_key])
        if parsed is None:
            st.session_state[err_key] = st.session_state[txt_key]
            st.session_state[txt_key] = _fmt_vol(st.session_state[num_key])
        else:
            st.session_state[err_key] = None
            st.session_state[num_key] = parsed
            st.session_state[txt_key] = _fmt_vol(parsed)

    container.text_input(label, key=txt_key, on_change=_sync,
                         label_visibility=label_visibility)
    bad = st.session_state.get(err_key)
    if bad:
        container.caption(f"«{bad}» ist keine Zahl — letzter Wert beibehalten.")
    return float(st.session_state[num_key])


_NON_PIDS = {"", "nan", "none", "<na>", "nat"}


def _is_real_pid(pid: str) -> bool:
    """False fuer leere/NaN-Partner-IDs -- siehe _merchant_rows()."""
    return str(pid).strip().lower() not in _NON_PIDS


def _artefact_rows(base_df: pd.DataFrame, pid_col: str) -> int:
    """Anzahl Zeilen ohne verwertbare Partner-ID. Wird als Hinweis angezeigt,
    damit das Ausblenden sichtbar bleibt (lieber eine Luecke als eine Luege)."""
    if pid_col not in base_df.columns:
        return 0
    pid_clean = base_df[pid_col].astype(str).map(ui.pid)
    return int((~pid_clean.map(_is_real_pid)).sum())


def _merchant_rows(base_df: pd.DataFrame, pid_col: str,
                   groups: dict[str, list[str]]) -> list[dict]:
    """One row per group + one row per ungrouped merchant, sorted by Name.

    Group rows carry a 'members' list (each an _agg_merchant() dict + 'pid' +
    '_df'); 'pid_display' is None for groups (renders as a +/- toggle) and the
    cleaned Partner-ID for singles. Every row keeps '_df' — the raw (pre-
    comparison) transaction slice — so the Hochrechnung layer (aggregation.py)
    can run a Tier-B projection on it directly.

    Zeilen OHNE Partner-ID sind keine Merchants: echte Worldline-Exporte
    tragen am Ende eine komplett leere Zeile (alle Spalten NaN), die sonst als
    Haendler «nan (nan)» in der Liste und in der Hochrechnung auftaucht. Sie
    wird hier uebersprungen, aber NICHT aus den Daten entfernt -- der Davos-
    Anker zaehlt 113'497 Zeilen, und sie traegt ohnehin 0 zu jeder Kennzahl
    bei. _artefact_rows() macht sie sichtbar statt sie zu verschweigen."""
    pid_clean = base_df[pid_col].astype(str).map(ui.pid)
    m = _with_comparison(base_df)
    grouped = {ui.pid(p) for pids in groups.values() for p in pids}
    real_pids = {p for p in set(pid_clean) if _is_real_pid(p)}

    rows: list[dict] = []
    for gname, raw_pids in groups.items():
        pids = [ui.pid(p) for p in raw_pids]
        mask = pid_clean.isin(pids)
        if not mask.any():
            continue
        row = _agg_merchant(m[mask])
        row["Name"] = gname
        row["_df"] = base_df[mask]
        row["pid_display"] = None
        members = []
        for pv in pids:
            pmask = pid_clean == pv
            if not pmask.any():
                continue
            mrow = _agg_merchant(m[pmask])
            if not mrow["Name"]:
                mrow["Name"] = pv
            mrow["pid"] = pv
            mrow["_df"] = base_df[pmask]
            members.append(mrow)
        row["members"] = sorted(members, key=lambda r: r["Name"].lower())
        rows.append(row)

    for pv in sorted(real_pids - grouped):
        pmask = pid_clean == pv
        row = _agg_merchant(m[pmask])
        if not row["Name"]:
            row["Name"] = pv
        row["_df"] = base_df[pmask]
        row["pid_display"] = pv
        row["members"] = []
        rows.append(row)

    rows.sort(key=lambda r: r["Name"].lower())
    return rows


def _row_key(row: dict) -> str:
    """Unique, stable key for a merchant/group row (Name alone can collide —
    several ungrouped merchants may share the same partner_name)."""
    return f"g_{row['Name']}" if row["pid_display"] is None else f"s_{row['pid_display']}"


def _merchant_options(base_df: pd.DataFrame, pid_col: str) -> list[str]:
    """'<Partner-ID> – <Name>' strings; Streamlit's multiselect filters on
    this text as the user types, giving search-by-name-or-ID for free."""
    if pid_col not in base_df.columns:
        return []
    cols = [pid_col] + (["partner_name"] if "partner_name" in base_df else [])
    sub = base_df[cols].dropna(subset=[pid_col]).copy()
    sub["_pid"] = sub[pid_col].astype(str).map(ui.pid)
    sub = sub.drop_duplicates("_pid")
    out = []
    for _, r in sub.iterrows():
        name = str(r.get("partner_name", "")).strip()
        out.append(f"{r['_pid']} – {name}" if name else r["_pid"])
    return sorted(out, key=str.lower)


def _pid_from_option(opt: str) -> str:
    return opt.split(" – ", 1)[0].strip()


def _fmt_row_values(row: dict) -> list[str]:
    return [chf(row["Umsatz"]), chf(row["Ø Ticket"]), num(row["Txn"]),
            chf(row["Aktuell-Geb."]), chf(row["SP-Geb."]), chf(row["Diff."]),
            chf(row["DCC-Vtl."])]


def _merchant_table_header() -> None:
    cols = st.columns(_MERCH_WIDTHS)
    for c, label in zip(cols, _MERCH_COLS):
        c.markdown(f"**{label}**")


def _entity_from_row(r: dict, label: str, key: str, annual_volume: float) -> EntityInput:
    """Build an aggregation.EntityInput from a _merchant_rows()/_agg_merchant()
    row dict (Ist-Aggregate über r['_df'] bereits berechnet)."""
    return EntityInput(
        label=label, key=key, df=r["_df"], annual_volume=annual_volume,
        ist_wl_net=r["Aktuell-Geb."], ist_sp_net=r["SP-Geb."],
        ist_dcc_adv=r["DCC-Vtl."], ist_txn=r["Txn"], ist_brutto=r["Umsatz"],
        ist_wl_fee=r.get("_wl_fee", 0.0), ist_sp_fee=r.get("_sp_fee", 0.0),
        ist_wl_cashback=r.get("_wl_cb", 0.0), ist_sp_cashback=r.get("_sp_cb", 0.0),
        ist_dcc_vol=r.get("_dcc_vol", 0.0), ist_fx_vol=r.get("_fx_vol", 0.0),
        ist_dcc_purchase_vol=r.get("_dcc_vol_purch", 0.0),
        ist_fx_purchase_vol=r.get("_fx_vol_purch", 0.0),
        ist_sp_asf=r.get("_sp_asf", 0.0),
        ist_wl_processing=r.get("_wl_processing", 0.0),
    )


def _period_entity(label: str, key: str, raw_df: pd.DataFrame, annual_volume: float,
                   frm, to) -> EntityInput:
    """Build an EntityInput from a raw (pre-comparison) transaction slice,
    eingeschränkt auf den in der Präsentation gewählten Zeitraum -- Ist-
    Aggregate und Tier-B-Projektionsbasis müssen denselben Zeitraum spiegeln."""
    pf = raw_df[_apply_period(raw_df, frm, to)]
    if pf.empty:
        agg_row = {"Aktuell-Geb.": 0.0, "SP-Geb.": 0.0, "DCC-Vtl.": 0.0, "Txn": 0,
                   "Umsatz": 0.0}
    else:
        agg_row = _agg_merchant(_with_comparison(pf))
    return EntityInput(
        label=label, key=key, df=pf, annual_volume=annual_volume,
        ist_wl_net=agg_row["Aktuell-Geb."], ist_sp_net=agg_row["SP-Geb."],
        ist_dcc_adv=agg_row["DCC-Vtl."], ist_txn=agg_row["Txn"],
        ist_brutto=agg_row["Umsatz"],
        ist_wl_fee=agg_row.get("_wl_fee", 0.0),
        ist_sp_fee=agg_row.get("_sp_fee", 0.0),
        ist_wl_cashback=agg_row.get("_wl_cb", 0.0),
        ist_sp_cashback=agg_row.get("_sp_cb", 0.0),
        ist_dcc_vol=agg_row.get("_dcc_vol", 0.0),
        ist_fx_vol=agg_row.get("_fx_vol", 0.0),
        ist_dcc_purchase_vol=agg_row.get("_dcc_vol_purch", 0.0),
        ist_fx_purchase_vol=agg_row.get("_fx_vol_purch", 0.0),
        ist_sp_asf=agg_row.get("_sp_asf", 0.0),
        ist_wl_processing=agg_row.get("_wl_processing", 0.0),
    )


def _entities_for_rows(rows: list[dict], partner_vols: dict[str, float],
                       group_vols: dict[str, float], frm, to) -> list[EntityInput]:
    """One EntityInput per top-level row (Präsentation-Scope "Alle", oder eine
    einzelne Gruppen-Zeile aus "Auswahl").
    Präzedenz je Gruppe, wie in Einstellungen → Hochrechnung: Mitglieder-Werte
    auf Partner-Ebene schlagen den Gruppen-Lump-Sum; ohne beides bleibt die
    Gruppe/der Merchant beim Ist (annual_volume=0)."""
    entities: list[EntityInput] = []
    for row in rows:
        is_group = row["pid_display"] is None
        if is_group:
            member_has_vol = any(
                partner_vols.get(m["pid"], 0.0) > 0 for m in row["members"])
            if member_has_vol:
                for m in row["members"]:
                    entities.append(_period_entity(
                        f"{m['Name']} ({m['pid']})", m["pid"], m["_df"],
                        partner_vols.get(m["pid"], 0.0), frm, to))
            else:
                entities.append(_period_entity(
                    row["Name"], row["Name"], row["_df"],
                    group_vols.get(row["Name"], 0.0), frm, to))
        else:
            pid = row["pid_display"]
            entities.append(_period_entity(
                f"{row['Name']} ({pid})", pid, row["_df"],
                partner_vols.get(pid, 0.0), frm, to))
    return entities


def _entities_for_pids(base_df: pd.DataFrame, pid_col: str, pids: list[str],
                       partner_vols: dict[str, float], frm, to) -> list[EntityInput]:
    """One EntityInput pro einzeln ausgewählter Partner-ID -- unabhängig von
    einer evtl. bestehenden Gruppenzugehörigkeit, da der Nutzer hier explizit
    einzelne IDs wählt (keine Gruppe im Spiel, siehe _entities_for_selection)."""
    pid_clean = base_df[pid_col].astype(str).map(ui.pid)
    entities: list[EntityInput] = []
    for pid in pids:
        praw = base_df[pid_clean == pid]
        name = pid
        if "partner_name" in praw.columns:
            names = praw["partner_name"].dropna().unique().tolist()
            if names:
                name = str(names[0])
        entities.append(_period_entity(
            f"{name} ({pid})", pid, praw, partner_vols.get(pid, 0.0), frm, to))
    return entities


def _entities_for_selection(
    base_df: pd.DataFrame, pid_col: str, groups: dict[str, list[str]],
    sel_group_names: list[str], sel_partner_pids: list[str],
    partner_vols: dict[str, float], group_vols: dict[str, float], frm, to,
) -> list[EntityInput]:
    """Präsentation-Scope "Auswahl": ein oder mehrere Partner und/oder Gruppen
    gemischt ausgewählt. Jede Gruppe expandiert (via _entities_for_rows) nach
    derselben Mitglieder-Präzedenz wie sonst überall; direkt ausgewählte
    Partner, die bereits über eine ausgewählte Gruppe abgedeckt sind, werden
    NICHT nochmal einzeln gezählt (keine Doppelzählung)."""
    entities: list[EntityInput] = []
    covered: set[str] = set()
    if sel_group_names:
        all_rows = _merchant_rows(base_df, pid_col, groups)
        for gn in sel_group_names:
            group_row = next(
                (r for r in all_rows if r["pid_display"] is None and r["Name"] == gn),
                None)
            if group_row:
                entities += _entities_for_rows(
                    [group_row], partner_vols, group_vols, frm, to)
                covered.update(ui.pid(p) for p in groups.get(gn, []))
    extra_pids = [p for p in sel_partner_pids if p not in covered]
    if extra_pids:
        entities += _entities_for_pids(base_df, pid_col, extra_pids, partner_vols, frm, to)
    return entities


def _render_aggregate(agg) -> None:
    """Unified KPI-Renderer für eine Hochrechnung -- eine Entity (Schnell-
    Hochrechnung in Einstellungen) oder mehrere summierte Entities (Gruppen-
    Mitglieder, oder die Präsentation-Scopes)."""
    cov = agg.coverage
    cov_txt = {CoverageLabel.SIMULATABLE: "hohe Deckung",
               CoverageLabel.LOW_COVERAGE: "mittlere Deckung",
               CoverageLabel.INDICATIVE: "indikativ"}[cov.label]
    ui.kpi_row([
        {"label": "Diff. p.a.", "value": f"CHF {chf(agg.saving_annual, 0)}",
         "foot": f"Band {chf(agg.band_low, 0)} – {chf(agg.band_high, 0)}",
         "accent": ui.GREEN if agg.saving_annual >= 0 else ui.ROT},
        {"label": "Transaktionen p.a.", "value": num(agg.n_txn_annual), "accent": ui.CYAN},
        {"label": "DCC-Vorteil p.a.", "value": f"CHF {chf(agg.dcc_advantage_annual)}",
         "accent": ui.CYAN},
        {"label": "Deckungsgrad", "value": f"{cov.coverage_pct:.0%}", "foot": cov_txt,
         "accent": ui.BLUE},
    ])
    if agg.skipped or len(agg.used) > 1:
        st.caption(f"Hochgerechnet: {', '.join(agg.used) if agg.used else '–'}. "
                  f"Ist-Werte übernommen (kein Jahresumsatz): "
                  f"{', '.join(agg.skipped) if agg.skipped else '–'}.")
    for cluster in agg.duplicate_groups:
        st.warning(f"Möglicher Fan-out: {', '.join(cluster)} haben denselben "
                  "Jahresumsatz hinterlegt — wird dennoch summiert. Bitte prüfen.")


def page_merchants() -> None:
    ui.page_header("Merchants", "Alle Händler auf einen Blick — Gruppen aufklappbar.",
                   status="Aktiv", meta="Gruppierung nach Partner-ID")
    pid_col = "partner_id" if "partner_id" in df.columns else None
    if not pid_col:
        ui.info_banner("Keine Partner-ID-Spalte in den Daten.")
        return

    groups = get_groups(DB_PATH)
    rows = _merchant_rows(df, pid_col, groups)

    ui.section("Alle Merchants", f"{num(len(rows))} Einträge · nach Name sortiert · "
               "Gruppieren & Hochrechnung unter Einstellungen → Merchants")
    _merchant_table_header()
    for row in rows:
        is_group = row["pid_display"] is None
        key = f"open_{_row_key(row)}"
        cols = st.columns(_MERCH_WIDTHS)
        cols[0].markdown(("👥 " if is_group else "") + row["Name"])
        if is_group:
            st.session_state.setdefault(key, False)
            label = ("−" if st.session_state[key] else "+") + f" {len(row['members'])}"
            if cols[1].button(label, key=f"{key}_btn"):
                st.session_state[key] = not st.session_state[key]
        else:
            cols[1].write(row["pid_display"])
        for c, v in zip(cols[2:], _fmt_row_values(row)):
            c.write(v)
        if is_group and st.session_state.get(key, False):
            for mrow in row["members"]:
                ccols = st.columns(_MERCH_WIDTHS)
                ccols[0].markdown(f"&nbsp;&nbsp;&nbsp;↳ {mrow['Name']}", unsafe_allow_html=True)
                ccols[1].write(mrow["pid"])
                for c, v in zip(ccols[2:], _fmt_row_values(mrow)):
                    c.write(v)


_FORMAT_LABEL = {"worldline": "Worldline", "sbb": "SBB IC++"}


def _format_note(rpt) -> str:
    """«datei.csv» → Worldline · «datei.xlsx» → SBB IC++ — pro Datei, damit
    sichtbar ist, wonach erkannt wurde, ohne dass eine Auswahl nötig war."""
    formats = getattr(rpt, "file_formats", None)
    if not formats:
        return ""
    parts = [f"«{fname}» → {_FORMAT_LABEL.get(fmt, fmt)}"
             for fname, fmt in formats.items()]
    return "Erkannt: " + " · ".join(parts) + ". "


def _load_note(dfn, rpt) -> tuple[str, str]:
    """Was der Ingest wirklich getan hat — nie «0 Zeilen geladen» neben Daten.

    Drei Fälle: neue Zeilen · Datei bit-identisch blockiert · Datei neu, aber
    jede Zeile schon registriert (derselbe Export neu gespeichert).
    """
    prefix = _format_note(rpt)
    if rpt.rows_new:
        return ("ok", f"{prefix}{num(rpt.rows_new)} Zeilen geladen.")
    if getattr(rpt, "files_known_rows", None):
        return ("info",
                f"{prefix}{', '.join(rpt.files_known_rows)}: Datei ist neu, aber jede "
                f"Zeile war bereits registriert (derselbe Export, neu "
                f"gespeichert). {num(len(dfn))} Zeilen werden angezeigt, "
                "nichts doppelt gezählt.")
    if rpt.files_blocked_hash:
        return ("info",
                f"{prefix}{', '.join(rpt.files_blocked_hash)}: bit-identisch bereits "
                f"eingelesen. {num(len(dfn))} Zeilen werden angezeigt.")
    return ("info", f"{prefix}{num(len(dfn))} Zeilen angezeigt, keine neuen Zeilen.")


# ── Fälle & Vorlagen: Helfer ─────────────────────────────────────────────────

def _fmt_when(iso: str) -> str:
    """ISO-UTC aus einer Fall-Datei als Schweizer Lokalzeit."""
    if not iso:
        return "—"
    try:
        return (pd.to_datetime(iso, utc=True).tz_convert("Europe/Zurich")
                .strftime("%d.%m.%Y %H:%M"))
    except Exception:
        return str(iso)[:16].replace("T", " ")


def _forget_kondition_widgets() -> None:
    """Widget-Keys der Konditionen vergessen.

    Pflicht nach jedem programmatischen Setzen von st.session_state.profile:
    die Zahlenfelder im ASF-Tab würden sonst im nächsten Render ihre ALTEN
    Werte zurückschreiben (session_store.save_profile läuft dort bei jedem
    Durchlauf) und das Geladene sofort überschreiben.
    """
    for _t in OFFERABLE_TYPES:
        for _pfx in ("asf_", "trx_", "mf_"):
            st.session_state.pop(f"{_pfx}{_t}", None)
    st.session_state.pop("dcc_in", None)
    st.session_state.pop("expert_editor", None)


def _forget_hochrechnung_widgets() -> None:
    """Widget-Keys der Hochrechnung vergessen.

    Pflicht nach jedem programmatischen Schreiben in hochrechnung_store (Fall-
    Import, Reset): die Zahlenfelder unter Einstellungen → Merchants →
    Hochrechnung seeden sich in _volume_input() nur beim ERSTEN Auftreten
    ihres Keys aus dem Default — sonst gewinnt der alte session_state-Wert und
    das Geladene bleibt unsichtbar, bis ein Browser-Reload session_state leert.
    """
    for _k in [k for k in st.session_state
               if isinstance(k, str) and (k.startswith("hoch_vol_")
                                          or k.startswith("hoch_open_"))]:
        st.session_state.pop(_k, None)


def _kondition_summary(p: RateProfile) -> str:
    """Einzeiler, was ein Konditionen-Satz enthält. Damit lässt sich eine
    Vorlage lesen, ohne sie anzuwenden — sonst müsste man die aktuellen
    Konditionen überschreiben, nur um nachzusehen."""
    rates = [p.type_rates[t] for t in OFFERABLE_TYPES if t in p.type_rates]
    if not rates:
        return "keine Sätze hinterlegt"

    def _uniq(fmt) -> str:
        vals = list(dict.fromkeys(fmt(r) for r in rates))
        return vals[0] if len(vals) == 1 else " / ".join(vals)

    return (f"ASF {' / '.join(f'{r.asf_pct * 100:.3f}' for r in rates)} % · "
            f"Trx-Fee {_uniq(lambda r: f'{r.trx_fee * 100:.2f}')} Rp. · "
            f"Mindestgeb. {_uniq(lambda r: f'{r.min_fee:.2f}')} CHF · "
            f"DCC {p.dcc_pct * 100:.2f} %"
            + (f" · {len(p.brand_overrides)} Brand-Werte"
               if p.brand_overrides else ""))


def _kondition_diff(old: RateProfile, new: RateProfile,
                    col_old: str, col_new: str) -> list[dict]:
    """Zeilenweiser Vergleich zweier Konditionen-Sätze, NUR die Unterschiede.

    ui.pct() multipliziert selbst mit 100 — hier den Bruch übergeben, sonst
    steht 140.00 % statt 1.40 % in der Änderungsliste.
    """
    rows = [{"Kondition": "DCC-Satz", col_old: pct(old.dcc_pct),
             col_new: pct(new.dcc_pct)}]
    for tkey in OFFERABLE_TYPES:
        o, nw = old.type_rates.get(tkey), new.type_rates.get(tkey)
        if not (o and nw):
            continue
        lbl = _TYPE_LABEL[tkey]
        rows += [
            {"Kondition": f"ASF {lbl}",
             col_old: f"{o.asf_pct * 100:.3f} %", col_new: f"{nw.asf_pct * 100:.3f} %"},
            {"Kondition": f"Trx-Fee {lbl}",
             col_old: f"{o.trx_fee * 100:.2f} Rp.", col_new: f"{nw.trx_fee * 100:.2f} Rp."},
            {"Kondition": f"Mindestgeb. {lbl}",
             col_old: chf(o.min_fee), col_new: chf(nw.min_fee)},
        ]
    return [r for r in rows if r[col_old] != r[col_new]]


def _template_meta(name: str):
    """Metadaten einer Vorlage nachschlagen (None, wenn es sie nicht gibt)."""
    for m in list_templates():
        if m.name == name:
            return m
    return None


def _render_template_panel() -> None:
    """Überschreiben, Bearbeiten und Löschen einer Vorlage.

    Jede der drei Aktionen zeigt erst ihre Konsequenz. Die Sätze einer Vorlage
    sind von Hand aus einem Preisblatt getippt — sie still zu ersetzen wäre
    teuer, und anders als ein Fall hat eine Vorlage keinen Autosave.
    """
    pend = st.session_state.get("tmpl_pending")
    if not pend:
        return
    name, mode = pend["name"], pend["mode"]

    def _close() -> None:
        # tmpl_pick muss mit weg: nach Umbenennen oder Löschen zeigt der Key
        # auf ein Label, das es nicht mehr gibt -- die Selectbox bricht dann.
        for k in ("tmpl_pending", "tmpl_pick", "tmpl_e_name", "tmpl_e_art",
                  "tmpl_e_notiz"):
            st.session_state.pop(k, None)

    if mode == "overwrite":
        ui.section(f"Konditionen in «{name}» speichern",
                   "Name, Art und Notiz bleiben unverändert.")
        meta = _template_meta(name)
        try:
            old, _ = load_template(name)
        except Exception as exc:
            st.error(f"Vorlage nicht lesbar: {exc}")
            if st.button("Schliessen", key="tmpl_p_close"):
                _close()
                st.rerun()
            return
        rows = _kondition_diff(old, profile, "In der Vorlage", "Neu")
        if rows:
            st.dataframe(pd.DataFrame(rows), hide_index=True,
                         use_container_width=True)
        else:
            st.caption("Identisch — es gäbe nichts zu speichern.")
        c1, c2 = st.columns([1, 3])
        if c1.button("Speichern", type="primary", key="tmpl_p_save",
                     disabled=not rows):
            try:
                save_template(profile, name,
                              meta.art if meta else "standard",
                              meta.notiz if meta else "")
                _close()
                st.session_state["tmpl_flash"] = f"«{name}» aktualisiert."
                st.rerun()
            except Exception as exc:
                st.error(f"Konnte nicht speichern: {exc}")
        if c2.button("Abbrechen", key="tmpl_p_cancel"):
            _close()
            st.rerun()

    elif mode == "meta":
        ui.section(f"Vorlage «{name}» bearbeiten",
                   "Nur Name, Art und Notiz — die Sätze bleiben unangetastet.")
        e1, e2 = st.columns(2)
        _nn = e1.text_input("Name", value=name, key="tmpl_e_name")
        _na = e2.selectbox("Art", TEMPLATE_ARTEN, key="tmpl_e_art",
                           index=TEMPLATE_ARTEN.index(pend.get("art", "standard")),
                           format_func=lambda a: TEMPLATE_ART_LABEL[a])
        _nz = st.text_input("Notiz (optional)", value=pend.get("notiz", ""),
                            key="tmpl_e_notiz")
        c1, c2 = st.columns([1, 3])
        if c1.button("Speichern", type="primary", key="tmpl_p_meta"):
            try:
                update_template_meta(name, art=_na, notiz=_nz.strip(),
                                     new_name=_nn.strip())
                _close()
                st.session_state["tmpl_flash"] = f"«{_nn.strip()}» gespeichert."
                st.rerun()
            except Exception as exc:
                st.error(f"Konnte nicht speichern: {exc}")
        if c2.button("Abbrechen", key="tmpl_p_mcancel"):
            _close()
            st.rerun()

    elif mode == "delete":
        ui.section(f"Vorlage «{name}» löschen")
        st.warning("Die hinterlegten Sätze gehen verloren. Eine Vorlage hat "
                   "keinen Autosave — willst du sie nur ändern, nimm "
                   "«Überschreiben» oder «Bearbeiten».")
        c1, c2 = st.columns([1, 3])
        if c1.button("Endgültig löschen", type="primary", key="tmpl_p_del"):
            delete_template(name)
            _close()
            st.session_state["tmpl_flash"] = f"«{name}» gelöscht."
            st.rerun()
        if c2.button("Abbrechen", key="tmpl_p_dcancel"):
            _close()
            st.rerun()


def _current_payload(variant_name: str, note: str = "") -> dict:
    """Der aktuelle Arbeitsstand als Fall-Payload."""
    basis = (st.session_state.get("applied_vorlage")
             or (st.session_state.get("active_case") or {}).get("vorlage") or {})
    return cases.build_payload(
        variant_name=variant_name, variant_note=note,
        profile=profile, master=master,
        groups=get_groups(DB_PATH),
        partner_vols=hoch_store.get_partner_volumes(DB_PATH),
        group_vols=hoch_store.get_group_volumes(DB_PATH),
        auswahl=st.session_state.get("auswahl_snapshot") or {},
        daten=cases.resolve_data_files(DB_PATH, df),
        vorlage_name=basis.get("name", ""), vorlage_art=basis.get("art", ""))


def _stage_case(payload: dict, quelle: str, kunde_slug: str = "",
                kunde_name: str = "") -> None:
    """Fall vormerken. Geschrieben wird erst nach der Änderungsliste."""
    st.session_state["case_pending"] = {
        "payload": payload, "quelle": quelle,
        "kunde_slug": kunde_slug, "kunde_name": kunde_name,
    }


def _apply_case(payload: dict, new_brands: list[str], kunde_slug: str,
                kunde_name: str) -> None:
    """Fall übernehmen: sichern, dann schreiben.

    Der Autosave läuft ZUERST und fängt damit die ALTEN Hochrechnungen und
    Gruppen ein — ein versehentlicher Import bleibt umkehrbar.
    """
    snap = cases.autosave("Vor Fallwechsel", profile=profile, master=master,
                          db_path=DB_PATH, df=df,
                          auswahl=st.session_state.get("auswahl_snapshot"))

    cases.apply_merchants(DB_PATH, payload)
    _forget_hochrechnung_widgets()

    st.session_state.profile = cases.profile_from_payload(payload)
    session_store.save_profile(st.session_state.profile)
    _forget_kondition_widgets()

    if new_brands:
        merged = cases.merge_new_brands(master, payload, new_brands)
        merged.save()
        st.session_state.master = merged
        st.session_state.pop("master_editor", None)

    if payload.get("auswahl"):
        st.session_state["auswahl_restore"] = payload["auswahl"]

    st.session_state["active_case"] = {
        "kunde_slug": kunde_slug, "kunde_name": kunde_name,
        "variant_name": payload.get("variante", {}).get("name", ""),
        "gespeichert_am": payload.get("gespeichert_am", ""),
        "vorlage": payload.get("basis", {}),
    }
    st.session_state.pop("case_pending", None)
    st.session_state.pop("last_pdf", None)
    st.session_state["case_flash"] = (
        f"Fall «{kunde_name} · {payload.get('variante', {}).get('name', '')}» "
        f"übernommen. Vorheriger Stand gesichert als «{snap.name}».")


def _render_case_panel() -> None:
    """Änderungsliste vor dem Übernehmen. Ein Codepfad für Laden und Import."""
    pend = st.session_state.get("case_pending")
    if not pend:
        return
    payload = pend["payload"]
    kunde_name = pend.get("kunde_name") or "Import"
    vname = payload.get("variante", {}).get("name", "—")

    ui.section(f"Übernehmen: {kunde_name} · {vname}",
               f"Quelle: {pend['quelle']} · gespeichert {_fmt_when(payload.get('gespeichert_am',''))}")

    # 1) Datenabgleich
    dm = cases.match_data(payload.get("daten", []),
                          cases.resolve_data_files(DB_PATH, df))
    exp_txt = ", ".join(f"{e.get('file_name')} ({num(e.get('rows', 0))} Zeilen)"
                        for e in dm.expected) or "keine hinterlegt"
    if dm.state == "match":
        st.success(f"Datenabgleich: geladener Export passt zum Fall — {exp_txt}.")
    elif dm.state == "none":
        st.info(f"Kein passender Export geladen. Der Fall gehört zu: {exp_txt}. "
                "Konditionen und Hinterlegungen lassen sich trotzdem übernehmen.")
    else:
        st.error("Der geladene Export ist **nicht** der des Falls "
                 f"(Fall: {exp_txt} · geladen: "
                 + ", ".join(f.get("file_name", "?") for f in dm.found)
                 + "). Die Zahlen werden vom archivierten Stand abweichen.")

    # 2) Partner, die es in den Daten nicht gibt
    miss = cases.missing_partners(payload, df)
    if miss:
        st.warning(f"Im Fall hinterlegt, in den Daten nicht gefunden: {len(miss)} "
                   "Partner-ID(s) — " + ", ".join(miss[:12])
                   + (" …" if len(miss) > 12 else "")
                   + ". Deren Hochrechnung bleibt ohne Wirkung.")

    # 3) Konditionen
    changed = _kondition_diff(profile, cases.profile_from_payload(payload),
                              "Aktuell", "Fall")
    st.markdown("**Konditionen**")
    if changed:
        st.dataframe(pd.DataFrame(changed), hide_index=True, use_container_width=True)
    else:
        st.caption("Unverändert.")

    # 4) Brand-Stammliste — der Master gewinnt, Abweichung sichtbar
    bd = cases.diff_brands(master, payload)
    st.markdown("**Brand-Stammliste**")
    picked: list[str] = []
    if bd.identical:
        st.caption("Deckt sich mit der Stammliste.")
    else:
        if bd.conflicts:
            st.warning("Die Stammliste bleibt unverändert (sie ist geteilt und "
                       "git-versioniert). Abweichungen des Falls:")
            st.dataframe(pd.DataFrame(bd.conflicts), hide_index=True,
                         use_container_width=True)
        if bd.new_brands:
            st.caption("Brands, die nur der Fall kennt — additiv übernehmbar:")
            st.dataframe(pd.DataFrame(bd.new_brands), hide_index=True,
                         use_container_width=True)
            picked = st.multiselect(
                "Diese Brands in die Stammliste aufnehmen",
                [b["Brand"] for b in bd.new_brands], default=[],
                key="case_new_brands")

    # 5) swipay.db
    dd = cases.diff_db(DB_PATH, payload)
    st.markdown("**Gruppen und Hochrechnungen (swipay.db)**")
    if dd.empty:
        st.caption("Keine Änderung.")
    else:
        st.dataframe(pd.DataFrame(dd.rows), hide_index=True, use_container_width=True)
        st.caption(f"{len(dd.rows)} Eintrag/Einträge werden geschrieben. Der "
                   "vorherige Stand wird vorher automatisch als Fall gesichert.")

    c1, c2 = st.columns([1, 3])
    if c1.button("Übernehmen", type="primary", key="case_apply"):
        try:
            _apply_case(payload, picked, pend.get("kunde_slug") or cases.slugify(kunde_name),
                        kunde_name)
            st.rerun()
        except Exception as exc:
            st.error(f"Übernehmen fehlgeschlagen: {exc}")
    if c2.button("Abbrechen", key="case_cancel"):
        st.session_state.pop("case_pending", None)
        st.session_state.pop("case_new_brands", None)
        st.rerun()


# ════════════════════════════════════════════════════════════════════════════
# PAGE: EINSTELLUNGEN  (Upload · Mapping · ASF)
# ════════════════════════════════════════════════════════════════════════════
def page_einstellungen() -> None:
    ui.page_header("Einstellungen", "Import, Brand-Mapping, Konditionen, Merchants.",
                   status="Konfiguration", meta="Import · Mapping · ASF · Merchants")
    with st.container(key="settings_tabs"):
        tab_up, tab_map, tab_asf, tab_merch, tab_cases, tab_reset = st.tabs(
            ["Import", "Mapping (Brands)", "ASF & DCC", "Merchants", "Fälle",
             "Reset"])

    # ── Upload ──
    with tab_up:
        ui.section("Export laden")
        st.caption("Worldline (XLSB/CSV) oder SBB IC++ (XLSX) — das Format "
                   "wird automatisch anhand der Spalten erkannt, keine Auswahl "
                   "nötig.")
        _note = st.session_state.pop("load_note", None)
        if _note:
            (st.success if _note[0] == "ok" else st.info)(_note[1])
        uploaded = st.file_uploader("Worldline- oder SBB-Exporte (XLSB / XLSX / CSV)",
                                    type=["xlsb", "xlsx", "csv"], accept_multiple_files=True)
        sheet_val = st.text_input("Sheet-Name (Worldline-XLSB, leer = erstes Sheet)",
                                   value="WL")
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
                session_store.save_df(dfn)
                st.session_state["load_note"] = _load_note(dfn, rpt)
                st.rerun()
            except Exception as exc:
                st.error(f"Fehler beim Laden: {exc}")
            finally:
                import shutil
                shutil.rmtree(tmp_dir, ignore_errors=True)

        # Convenience: load an export already present in data/ (no re-upload).
        existing = sorted(p.name for ext in ("*.xlsb", "*.xlsx", "*.csv")
                          for p in (ROOT / "data").glob(ext))
        if existing:
            pick = st.selectbox("Oder vorhandene Datei aus data/ laden",
                                ["—"] + existing)
            if st.button("Aus data/ laden") and pick != "—":
                try:
                    dfn, rpt = ingest_files([str(ROOT / "data" / pick)], DB_PATH,
                                            sheet=sheet_val.strip() or None)
                    st.session_state.df = dfn; st.session_state.report = rpt
                    session_store.save_df(dfn)
                    st.session_state["load_note"] = _load_note(dfn, rpt)
                    st.rerun()
                except Exception as exc:
                    st.error(f"Fehler beim Laden: {exc}")

        rpt = st.session_state.report
        if rpt is not None:
            ui.section("Abgleichsbericht")
            if getattr(rpt, "file_formats", None):
                st.caption(_format_note(rpt).removesuffix(". "))
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
                         "anbietbar behandelt (aktueller Anbieter 1:1).")
            else:
                st.success("Alle Brands im Export sind der Stammliste zugeordnet.")

    # ── Mapping ──
    with tab_map:
        ui.section("Brand-Stammliste", "git-versioniert · config/brands.json")
        st.caption("Logisches Brand = ein/mehrere Such-Codes (Aliase). Typ steuert die "
                   "ASF. QR-Code-Brands sind nie anbietbar (aktueller Anbieter 1:1).")
        # Typ als Klartext-Label (dieselben Namen wie in der Aufschlüsselung und
        # bei der ASF-Eingabe), nicht als Rohschlüssel. Beim Speichern zurück
        # auf den Schlüssel gemappt.
        rows = [{"Anzeigename": r.display_name,
                 "Typ": _TYPE_LABEL.get(r.type, r.type), "Anbietbar": r.offerable,
                 "Reihenfolge": r.order, "Such-Codes": ", ".join(r.search_codes)}
                for r in master.sorted_brands()]
        edit = st.data_editor(pd.DataFrame(rows), hide_index=True, num_rows="dynamic",
            use_container_width=True, column_config={
                "Typ": st.column_config.SelectboxColumn(
                    options=[_TYPE_LABEL[t] for t in ALL_TYPES]),
                "Anbietbar": st.column_config.CheckboxColumn()}, key="master_editor")
        if st.button("Stammliste speichern", type="primary"):
            try:
                recs = []
                for _, r in edit.iterrows():
                    name = str(r["Anzeigename"]).strip()
                    if not name:
                        continue
                    # Label -> Schlüssel. Ein bereits roher Schlüssel (Altbestand
                    # oder händisch getippt) wird durchgelassen; alles andere
                    # faellt in BrandRecord's Validierung mit klarer Meldung.
                    typ_in = str(r["Typ"]).strip()
                    typ = _TYPE_KEY.get(typ_in, typ_in)
                    codes = [c.strip() for c in str(r["Such-Codes"]).split(",") if c.strip()]
                    recs.append(BrandRecord(name, typ,
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
        # ── Vorlagen ─────────────────────────────────────────────────────
        # Wiederverwendbares Preisblatt OHNE Kundenbezug (config/profiles/,
        # git-versioniert) — im Gegensatz zum Fall (data/cases/, gitignored).
        ui.section("Vorlagen", "Standard · Verband · Rahmenvertrag")
        _tflash = st.session_state.pop("tmpl_flash", None)
        if _tflash:
            st.success(_tflash)
        _tmpls = list_templates()
        v1, v2 = st.columns(2)
        with v1:
            st.markdown("**Vorlage anwenden oder pflegen**")
            if not _tmpls:
                st.caption("Noch keine Vorlage hinterlegt. Rechts die aktuellen "
                           "Konditionen sichern — dann steht hier das echte "
                           "SwiPay-Preisblatt statt der Platzhalter.")
            else:
                _lbl = {f"{m.art_label} · {m.name}": m for m in _tmpls}
                _sel = st.selectbox("Vorlage", list(_lbl), key="tmpl_pick")
                _meta = _lbl[_sel]
                st.caption((_meta.notiz + " · " if _meta.notiz else "")
                           + f"geändert {_fmt_when(_meta.updated_at)}")
                # Inhalt zeigen, ohne sie anwenden zu müssen — sonst muss man
                # die Konditionen überschreiben, nur um nachzusehen.
                try:
                    _tprof, _ = load_template(_meta.name)
                    st.caption(_kondition_summary(_tprof))
                except Exception as exc:
                    _tprof = None
                    st.error(f"Vorlage nicht lesbar: {exc}")

                # Vier Aktionen in EINER Reihe. Die Spalten sind leicht nach
                # Label-Länge gewichtet: bei vier gleichen Spalten bricht
                # «Überschreiben» als längstes Label um, während daneben Platz
                # verfällt. Unter ~1200 px Fensterbreite wird es für vier
                # Knöpfe in der halben Spalte ohnehin eng.
                t1, t2, t3, t4 = st.columns([1, 1.3, 1.1, 0.95])
                if t1.button("Anwenden", key="tmpl_apply", disabled=_tprof is None):
                    st.session_state.profile = _tprof
                    session_store.save_profile(_tprof)
                    _forget_kondition_widgets()
                    st.session_state["applied_vorlage"] = {
                        "name": _meta.name, "art": _meta.art}
                    st.rerun()
                if t2.button("Überschreiben", key="tmpl_over",
                             disabled=_tprof is None,
                             help="Die aktuellen Konditionen in diese Vorlage "
                                  "speichern — Name, Art und Notiz bleiben."):
                    st.session_state["tmpl_pending"] = {
                        "mode": "overwrite", "name": _meta.name}
                    st.rerun()
                if t3.button("Bearbeiten", key="tmpl_edit",
                             help="Name, Art und Notiz ändern — die Sätze "
                                  "bleiben unangetastet."):
                    st.session_state["tmpl_pending"] = {
                        "mode": "meta", "name": _meta.name,
                        "art": _meta.art, "notiz": _meta.notiz}
                    st.rerun()
                if t4.button("Löschen", key="tmpl_del"):
                    st.session_state["tmpl_pending"] = {
                        "mode": "delete", "name": _meta.name}
                    st.rerun()
        with v2:
            st.markdown("**Aktuelle Konditionen als neue Vorlage sichern**")
            st.caption(_kondition_summary(profile))
            _tn = st.text_input("Name", key="tmpl_name",
                                placeholder="z. B. SwiPay Standard 2026")
            _ta = st.selectbox("Art", TEMPLATE_ARTEN, key="tmpl_art",
                               format_func=lambda a: TEMPLATE_ART_LABEL[a])
            _tz = st.text_input("Notiz (optional)", key="tmpl_notiz")
            if st.button("Als neue Vorlage speichern", key="tmpl_save"):
                try:
                    _name = str(_tn).strip()
                    if any(m.name.lower() == _name.lower() for m in _tmpls):
                        raise ValueError(
                            f"«{_name}» gibt es schon — links auswählen und "
                            "«Überschreiben» nehmen, damit nichts still ersetzt "
                            "wird.")
                    _p = save_template(profile, _name, _ta, _tz.strip())
                    st.success(f"Vorlage gespeichert: {_p.stem}")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Konnte nicht speichern: {exc}")

        _render_template_panel()

        st.session_state.profile = profile
        session_store.save_profile(profile)

    # ── Merchants (Gruppieren + Hochrechnung) ──
    with tab_merch:
        pid_col = "partner_id" if "partner_id" in df.columns else None
        if not pid_col:
            ui.info_banner("Keine Partner-ID-Spalte in den Daten.")
        else:
            with st.container(key="merchant_subtabs"):
                sub_group, sub_hoch = st.tabs(["Gruppieren", "Hochrechnung"])
            options = _merchant_options(df, pid_col)

            # ── Gruppieren ──
            with sub_group:
                ui.section("Neue Gruppe anlegen",
                           "Suche nach Name oder Partner-ID, wähle mehrere aus.")
                gname = st.text_input("Gruppenname", key="new_group_name")
                gsel = st.multiselect("Merchants auswählen", options,
                                      key="new_group_members",
                                      placeholder="Name oder Partner-ID suchen")
                if (st.button("Gruppe speichern", type="primary", key="save_new_group")
                        and gname.strip() and gsel):
                    assign_group(DB_PATH, gname.strip(), [_pid_from_option(o) for o in gsel])
                    st.success(f"Gruppe «{gname.strip()}» gespeichert."); st.rerun()

                groups = get_groups(DB_PATH)
                if groups:
                    ui.section("Gruppen bearbeiten")
                    dname = st.selectbox("Gruppe wählen", list(groups.keys()),
                                         key="edit_group_select")
                    current_pids = {ui.pid(p) for p in groups[dname]}
                    default_sel = [o for o in options if _pid_from_option(o) in current_pids]
                    esel = st.multiselect("Mitglieder", options, default=default_sel,
                                          key=f"edit_members_{dname}")
                    c1, c2 = st.columns(2)
                    with c1:
                        if st.button("Änderungen speichern", type="primary",
                                     key="save_edit_group"):
                            new_pids = {_pid_from_option(o) for o in esel}
                            removed = current_pids - new_pids
                            if removed:
                                unassign_group(DB_PATH, list(removed))
                            if new_pids:
                                assign_group(DB_PATH, dname, list(new_pids))
                            st.success(f"Gruppe «{dname}» aktualisiert."); st.rerun()
                    with c2:
                        if st.button("Gruppe löschen", key="delete_group"):
                            unassign_group(DB_PATH, groups[dname])
                            st.success(f"Gruppe «{dname}» gelöscht."); st.rerun()
                else:
                    st.caption("Noch keine Gruppen definiert.")

            # ── Hochrechnung ──
            with sub_hoch:
                ui.section(
                    "Hochrechnung",
                    "Jahresumsatz je Merchant oder Gruppe eintragen — für einfachere "
                    "Szenarien auch als Schnellhochrechnung nutzbar. Wirkt sich "
                    "automatisch auf die Seite Präsentation aus.")
                groups = get_groups(DB_PATH)
                rows = _merchant_rows(df, pid_col, groups)
                partner_vols_db = hoch_store.get_partner_volumes(DB_PATH)
                group_vols_db = hoch_store.get_group_volumes(DB_PATH)
                # Ein Merchant hat EINEN Jahresumsatz, egal ob als eigene Zeile
                # oder über "Mitglieder einzeln eintragen" editiert -- beide
                # Wege schreiben auf denselben Partner-ID-Schlüssel in swipay.db.
                partner_updates: dict[str, float] = {}
                group_updates: dict[str, float] = {}

                for row in rows:
                    is_group = row["pid_display"] is None
                    rk = _row_key(row)
                    label = row["Name"] if is_group else f"{row['Name']} ({row['pid_display']})"
                    with st.container(border=True):
                        c1, c2 = st.columns([2.2, 1.3])
                        c1.markdown(("👥 **" if is_group else "**") + label + "**")
                        c1.caption(
                            f"Ist: Umsatz CHF {chf_c(row['Umsatz'])} · {num(row['Txn'])} Txn · "
                            f"Diff. CHF {chf(row['Diff.'])} · DCC-Vtl. CHF {chf(row['DCC-Vtl.'])}")
                        vol_key = f"hoch_vol_{rk}"
                        default_vol = (group_vols_db if is_group else partner_vols_db).get(
                            row["Name"] if is_group else row["pid_display"], 0.0)

                        # Bei einer Gruppe zuerst nachsehen, ob Mitglieder eigene
                        # Werte tragen. Dann ist der Gruppen-Lump-Sum per
                        # Präzedenz wirkungslos (siehe aggregation.py) -- statt
                        # ein Feld anzubieten, das nichts tut, zeigt die Gruppe
                        # die SUMME ihrer Mitglieder, schreibgeschützt.
                        member_sum = 0.0
                        if is_group:
                            for mrow in row["members"]:
                                mk = f"hoch_vol_{rk}_{mrow['pid']}__num"
                                member_sum += float(st.session_state.get(
                                    mk, partner_vols_db.get(mrow["pid"], 0.0)))

                        if is_group and member_sum > 0:
                            c2.text_input("Jahresumsatz CHF",
                                          value=_fmt_vol(member_sum), disabled=True,
                                          key=f"{vol_key}__sum_display")
                            c2.caption("Summe der Mitglieder")
                            annual_vol = member_sum
                            # Kein Gruppen-Lump-Sum speichern: die Mitglieder
                            # tragen den Wert, eine zweite Zahl daneben wäre
                            # eine stille Doppelspur.
                            group_updates[row["Name"]] = 0.0
                        else:
                            annual_vol = _volume_input(c2, vol_key, default_vol)
                            if is_group:
                                group_updates[row["Name"]] = annual_vol
                            else:
                                partner_updates[row["pid_display"]] = annual_vol

                        member_vols: dict[str, float] = {}
                        if is_group and row["members"]:
                            open_key = f"hoch_open_{rk}"
                            st.session_state.setdefault(open_key, False)
                            btn_label = (
                                "− Mitglieder ausblenden" if st.session_state[open_key]
                                else f"+ {len(row['members'])} Mitglieder einzeln eintragen")
                            if st.button(btn_label, key=f"{open_key}_btn"):
                                st.session_state[open_key] = not st.session_state[open_key]
                            if st.session_state[open_key]:
                                for mrow in row["members"]:
                                    mc1, mc2 = st.columns([2.2, 1.3])
                                    mc1.markdown(
                                        f"&nbsp;&nbsp;↳ {mrow['Name']} ({mrow['pid']})",
                                        unsafe_allow_html=True)
                                    mkey = f"hoch_vol_{rk}_{mrow['pid']}"
                                    mv = _volume_input(
                                        mc2, mkey,
                                        partner_vols_db.get(mrow["pid"], 0.0),
                                        label_visibility="collapsed")
                                    partner_updates[mrow["pid"]] = mv
                                    if mv > 0:
                                        member_vols[mrow["pid"]] = mv

                        if is_group and member_vols:
                            entities = [
                                _entity_from_row(
                                    mrow, f"{mrow['Name']} ({mrow['pid']})", mrow["pid"],
                                    member_vols.get(mrow["pid"], 0.0))
                                for mrow in row["members"]
                            ]
                            agg = aggregate(entities, params, offer, profile.dcc_pct)
                            if agg:
                                _render_aggregate(agg)
                        elif annual_vol > 0:
                            ent = _entity_from_row(row, label, rk, annual_vol)
                            agg = aggregate([ent], params, offer, profile.dcc_pct)
                            if agg and agg.has_projection:
                                _render_aggregate(agg)
                            else:
                                st.warning(
                                    "Hochrechnung nicht möglich (keine Käufe in der Auswahl).")

                hoch_store.save_volumes(DB_PATH, partner_updates, group_updates)

    # ── Fälle ──
    with tab_cases:
        _flash = st.session_state.pop("case_flash", None)
        if _flash:
            st.success(_flash)

        if st.session_state.get("case_pending"):
            _render_case_panel()
        else:
            _act = st.session_state.get("active_case")
            if _act:
                ui.info_banner(
                    f"Aktiver Fall: <b>{_act['kunde_name']} · "
                    f"{_act['variant_name']}</b> — gespeichert "
                    f"{_fmt_when(_act.get('gespeichert_am',''))}")

            # ── Speichern ────────────────────────────────────────────────────
            ui.section("Aktuellen Stand speichern",
                       "Konditionen, Gruppen, Hochrechnungen, Auswahl und der "
                       "zugehörige Export.")
            _custs = cases.list_customers()
            _names = [c.name for c in _custs if not c.is_autosave]
            _NEW = "➕ Neuer Kunde"
            _opts = [_NEW] + _names
            _idx = (_opts.index(_act["kunde_name"])
                    if _act and _act.get("kunde_name") in _opts else 0)
            s1, s2 = st.columns(2)
            with s1:
                _pick = st.selectbox("Kunde", _opts, index=_idx, key="case_kunde_pick")
                _kunde = (st.text_input("Name des Kunden", key="case_kunde_new")
                          if _pick == _NEW else _pick)
            with s2:
                _def_var = (_act["variant_name"] if _act
                            and _act.get("kunde_name") == _pick
                            else f"Stand {pd.Timestamp.today().strftime('%Y-%m-%d')}")
                _variant = st.text_input("Variante", value=_def_var, key="case_variant")
            _notiz = st.text_input("Notiz (optional)", key="case_notiz")

            _daten = cases.resolve_data_files(DB_PATH, df)
            _src = cases.find_export_source(_daten)
            if _daten:
                st.caption("Datengrundlage: "
                           + ", ".join(f"{d['file_name']} ({num(d['rows'])} Zeilen)"
                                       for d in _daten)
                           + (f" · Kopie im Fall: {_src.name}" if _src
                              else " · Originaldatei nicht in data/ gefunden — "
                                   "der Fall speichert nur Name und Hash."))
            else:
                st.caption("Keine Daten geladen — der Fall hält nur Konditionen "
                           "und Hinterlegungen.")

            if st.button("Fall speichern", type="primary", key="case_save"):
                try:
                    if not str(_kunde).strip():
                        raise cases.CaseError("Bitte einen Kundennamen angeben.")
                    if not str(_variant).strip():
                        raise cases.CaseError("Bitte einen Variantennamen angeben.")
                    _pl = _current_payload(_variant.strip(), _notiz.strip())
                    _info = cases.save_variant(
                        _kunde.strip(), _pl, kunde_notiz=None, export_src=_src)
                    st.session_state["active_case"] = {
                        "kunde_slug": _info.kunde_slug, "kunde_name": _info.kunde_name,
                        "variant_name": _info.name,
                        "gespeichert_am": _info.gespeichert_am,
                        "vorlage": _pl.get("basis", {})}
                    st.session_state["case_flash"] = (
                        f"Gespeichert: {_info.kunde_name} · {_info.name}")
                    st.rerun()
                except Exception as exc:
                    st.error(f"Speichern fehlgeschlagen: {exc}")

            # ── Rename-Dialog ────────────────────────────────────────────────
            _ren = st.session_state.get("rename_pending")
            if _ren:
                ui.section("Variante umbenennen")
                _new = st.text_input("Neuer Name", value=_ren["name"],
                                     key="rename_input")
                r1, r2 = st.columns([1, 3])
                if r1.button("Umbenennen", type="primary", key="rename_go"):
                    try:
                        cases.rename_variant(_ren["kunde_slug"], _ren["slug"], _new)
                        st.session_state.pop("rename_pending", None)
                        st.session_state["case_flash"] = f"Umbenannt in «{_new}»."
                        st.rerun()
                    except Exception as exc:
                        st.error(str(exc))
                if r2.button("Abbrechen", key="rename_cancel"):
                    st.session_state.pop("rename_pending", None)
                    st.rerun()

            # ── Lösch-Bestätigung ────────────────────────────────────────────
            _del = st.session_state.get("del_pending")
            if _del:
                ui.section("Löschen bestätigen")
                if _del.get("kind") == "customer":
                    st.warning(
                        f"Kunde **{_del['kunde_name']}** vollständig entfernen — "
                        f"inklusive Export-Kopie und abgelegter Berichte "
                        f"({_del.get('size','')}). Das ist nicht umkehrbar.")
                else:
                    st.warning(f"Variante **{_del['name']}** von "
                               f"{_del['kunde_name']} löschen.")
                d1, d2 = st.columns([1, 3])
                if d1.button("Endgültig löschen", type="primary", key="del_go"):
                    try:
                        if _del.get("kind") == "customer":
                            cases.delete_customer(_del["kunde_slug"])
                            if (st.session_state.get("active_case") or {}).get(
                                    "kunde_slug") == _del["kunde_slug"]:
                                st.session_state.pop("active_case", None)
                            st.session_state["case_flash"] = (
                                f"Kunde «{_del['kunde_name']}» entfernt.")
                        else:
                            _left = cases.delete_variant(_del["kunde_slug"],
                                                         _del["slug"])
                            st.session_state["case_flash"] = (
                                f"Variante «{_del['name']}» gelöscht."
                                + (" Der Kunde hat keine Variante mehr — Export-"
                                   "Kopie und Berichte liegen weiter da."
                                   if _left == 0 else ""))
                        st.session_state.pop("del_pending", None)
                        st.rerun()
                    except Exception as exc:
                        st.error(str(exc))
                if d2.button("Abbrechen", key="del_cancel"):
                    st.session_state.pop("del_pending", None)
                    st.rerun()

            # ── Liste ────────────────────────────────────────────────────────
            ui.section("Gespeicherte Fälle",
                       f"{len(_custs)} Kunde(n) · Ablage data/cases/")
            if not _custs:
                st.caption("Noch nichts gespeichert.")
            for c in _custs:
                _mb = c.bytes_used / 1_048_576
                _title = (f"{'🕓' if c.is_autosave else '📁'} {c.name} — "
                          f"{len(c.variants)} Variante(n) · {_mb:.1f} MB")
                with st.expander(_title, expanded=False):
                    if c.notiz:
                        st.caption(c.notiz)
                    if c.export_files:
                        st.caption("Export im Fall: " + ", ".join(c.export_files))
                    for v in c.variants:
                        w = st.columns([2.2, 1.3, 0.9, 0.95, 1.35, 0.95])
                        w[0].markdown(f"**{v.name}**"
                                      + (f"  \n<span style='font-size:.75rem;"
                                         f"color:#8a9495'>{v.notiz}</span>"
                                         if v.notiz else ""),
                                      unsafe_allow_html=True)
                        w[1].caption(_fmt_when(v.gespeichert_am))
                        if w[2].button("Laden", key=f"ld_{c.slug}_{v.slug}"):
                            try:
                                _stage_case(cases.load_variant(c.slug, v.slug),
                                            f"Fall {c.name}", c.slug, c.name)
                                st.rerun()
                            except Exception as exc:
                                st.error(str(exc))
                        try:
                            _pl = cases.load_variant(c.slug, v.slug)
                            w[3].download_button(
                                "Export", data=cases.export_bytes(_pl),
                                file_name=cases.export_filename(c.name, _pl),
                                mime="application/json",
                                key=f"ex_{c.slug}_{v.slug}")
                        except Exception:
                            w[3].caption("defekt")
                        if w[4].button("Umbenennen", key=f"rn_{c.slug}_{v.slug}"):
                            st.session_state["rename_pending"] = {
                                "kunde_slug": c.slug, "slug": v.slug, "name": v.name}
                            st.rerun()
                        if w[5].button("Löschen", key=f"dl_{c.slug}_{v.slug}"):
                            st.session_state["del_pending"] = {
                                "kind": "variant", "kunde_slug": c.slug,
                                "kunde_name": c.name, "slug": v.slug, "name": v.name}
                            st.rerun()
                    if st.button(f"Kunde «{c.name}» ganz entfernen",
                                 key=f"dc_{c.slug}"):
                        st.session_state["del_pending"] = {
                            "kind": "customer", "kunde_slug": c.slug,
                            "kunde_name": c.name, "size": f"{_mb:.1f} MB"}
                        st.rerun()

            # ── Import ───────────────────────────────────────────────────────
            ui.section("Fall importieren",
                       "Config-Datei aus einem anderen Lauf oder von einem "
                       "anderen Rechner.")
            _up = st.file_uploader(f"Fall-Datei ({cases.EXPORT_SUFFIX})",
                                   type=["json"], key="case_import")
            if _up is not None and st.button("Datei prüfen", key="case_import_go"):
                try:
                    _pl = cases.parse_import(_up.read())
                    _kn = Path(_up.name).name.split("--")[0].replace("-", " ").title()
                    _stage_case(_pl, f"Import {_up.name}", cases.slugify(_kn), _kn)
                    st.rerun()
                except Exception as exc:
                    st.error(f"Import nicht möglich: {exc}")
            st.caption("Fall-Dateien enthalten Vertragsdaten (Partner-IDs, Namen, "
                       "Jahresumsätze) — vertraulich behandeln. Keine "
                       "Transaktionszeilen, keine Kartennummern.")

    # ── Reset ──
    with tab_reset:
        ui.section("Analyse zurücksetzen",
                   "Geladene Daten und Konditionen verwerfen, wieder bei null starten.")
        st.caption("Betrifft nur die laufende Arbeitssitzung (data/session/). Die "
                   "Brand-Stammliste (config/brands.json), Gruppen-Zuordnungen und "
                   "hinterlegte Hochrechnungen (swipay.db) bleiben unberührt.")
        confirm = st.checkbox("Ja, aktuelle Daten und Konditionen verwerfen.")
        if st.button("Reset", type="primary", disabled=not confirm):
            # Immer zuerst sichern: der Verlust wird strukturell unmöglich
            # statt disziplinabhängig. Ohne Export-Kopie (die Datei liegt noch
            # in data/), dafür mit den DB-Werten von JETZT.
            try:
                _snap = cases.autosave("Vor Reset", profile=profile, master=master,
                                       db_path=DB_PATH, df=df,
                                       auswahl=st.session_state.get("auswahl_snapshot"))
                st.info(f"Vorher gesichert als **{cases.AUTOSAVE_NAME} · "
                        f"{_snap.name}** (Einstellungen → Fälle).")
            except Exception as exc:
                st.error(f"Autosave fehlgeschlagen — Reset abgebrochen: {exc}")
                st.stop()
            session_store.reset()
            st.session_state.df = pd.DataFrame()
            st.session_state.report = None
            st.session_state.profile = default_rate_profile()
            for _t in OFFERABLE_TYPES:
                for _pfx in ("asf_", "trx_", "mf_"):
                    st.session_state.pop(f"{_pfx}{_t}", None)
            st.session_state.pop("dcc_in", None)
            st.session_state.pop("active_case", None)
            st.session_state.pop("last_pdf", None)
            _forget_hochrechnung_widgets()
            st.success("Zurückgesetzt. Lade einen neuen Export, um weiterzuarbeiten.")
            st.rerun()


# ── Router ──────────────────────────────────────────────────────────────────
if page == "Präsentation":
    page_praesentation()
elif page == "Transaktionen":
    page_transaktionen()
elif page == "Merchants":
    page_merchants()
else:
    page_einstellungen()
