"""Flask web UI for the SwiPay Worldline comparison tool."""
from __future__ import annotations

import json
import os
import tempfile
import uuid
from pathlib import Path

from flask import Flask, abort, flash, redirect, render_template, request, send_file, url_for
from werkzeug.utils import secure_filename

from ..engine import run_engine
from ..loader import load_file
from ..logging_setup import setup_logging
from ..pdf_report import to_pdf
from ..projection import detect_period, project_breakdown, project_summary
from ..report import build_breakdown, to_csv

_UPLOAD_DIR = Path(tempfile.gettempdir()) / "swipay_wl_ui"
_ALLOWED_EXT = {".xlsb", ".csv"}
_COL_W = [42, 20, 32, 32, 36, 26]
_DISPLAY_COLS = [
    "Anzahl Tx",
    "WL Netto CHF",
    "SP Netto CHF",
    "Differenz Netto CHF",
    "Differenz %",
]


def _fmt_chf(value: object) -> str:
    try:
        return f"{float(value):,.2f}".replace(",", "'")
    except (ValueError, TypeError):
        return str(value)


def create_app() -> Flask:
    app = Flask(__name__)
    app.secret_key = os.urandom(24)
    app.config["MAX_CONTENT_LENGTH"] = 50 * 1024 * 1024  # 50 MB
    _UPLOAD_DIR.mkdir(exist_ok=True)

    app.jinja_env.filters["chf"] = _fmt_chf

    @app.template_filter("intfmt")
    def intfmt_filter(v: object) -> str:
        try:
            return f"{int(v):,}".replace(",", "'")
        except (ValueError, TypeError):
            return str(v)

    # ------------------------------------------------------------------
    @app.route("/")
    def index():
        return render_template("index.html")

    # ------------------------------------------------------------------
    @app.route("/analyse", methods=["POST"])
    def analyse():
        f = request.files.get("file")
        if not f or not f.filename:
            flash("Keine Datei ausgewählt.")
            return redirect(url_for("index"))

        ext = Path(f.filename).suffix.lower()
        if ext not in _ALLOWED_EXT:
            flash(f"Ungültiger Dateityp «{ext}» – bitte .xlsb oder .csv hochladen.")
            return redirect(url_for("index"))

        project_days = max(1, int(request.form.get("project_days") or 365))
        sid = str(uuid.uuid4())
        sid_dir = _UPLOAD_DIR / sid
        sid_dir.mkdir()

        safe_name = secure_filename(f.filename)
        upload_path = sid_dir / safe_name
        f.save(str(upload_path))

        try:
            df = load_file(upload_path)
            eng = run_engine(df)
        except Exception as exc:
            flash(f"Fehler beim Laden der Datei: {exc}")
            return redirect(url_for("index"))

        wl_fee  = float(-df["Gebühren"].sum())
        wl_dcc  = float(df["DCC Payback"].fillna(0).sum())
        wl_net  = wl_fee - wl_dcc
        sp_fee  = float(eng["sp_fee"].sum())
        sp_cash = float(eng["sp_cashback"].sum())
        sp_net  = sp_fee - sp_cash
        delta   = wl_net - sp_net

        by_cat   = build_breakdown(df, eng, "Karten Kategorie")
        by_brand = build_breakdown(df, eng, "Brand")

        proj       = None
        proj_table = None
        period_str = "–"

        try:
            period     = detect_period(df)
            proj       = project_summary(
                wl_fee=wl_fee, wl_cashback=wl_dcc,
                sp_fee=sp_fee, sp_cashback=sp_cash,
                tx_count=len(df),
                actual_days=period.actual_days,
                target_days=project_days,
            )
            proj_table = project_breakdown(
                by_cat, "Karten Kategorie", period.actual_days, project_days
            )
            period_str = f"{period.start.date()} – {period.end.date()}"
        except Exception:
            pass

        stem = Path(safe_name).stem
        to_csv(by_cat,   sid_dir / f"{stem}_kategorie.csv")
        to_csv(by_brand, sid_dir / f"{stem}_brand.csv")
        if proj_table is not None:
            to_csv(proj_table, sid_dir / f"{stem}_hochrechnung.csv")

        pdf_secs = {
            "Nach Karten-Kategorie": (by_cat,   "Karten Kategorie", _COL_W),
            "Nach Brand":            (by_brand,  "Brand",            _COL_W),
        }
        if proj_table is not None:
            pdf_secs[f"Hochrechnung auf {project_days} Tage"] = (
                proj_table, "Karten Kategorie", _COL_W
            )
        try:
            to_pdf(
                pdf_secs,
                sid_dir / f"{stem}_report.pdf",
                title=f"SwiPay-Vergleich {stem}",
                meta=f"Quelle: {safe_name} | {len(df):,} Transaktionen",
            )
        except Exception:
            pass  # PDF failure is non-fatal

        def _table_rows(df_src, group_col: str) -> list[dict]:
            cols = [group_col] + [c for c in _DISPLAY_COLS if c in df_src.columns]
            return df_src[cols].to_dict("records")

        summary = {
            "filename":     safe_name,
            "stem":         stem,
            "tx_count":     len(df),
            "period":       period_str,
            "project_days": project_days,
            "wl_fee":       wl_fee,
            "wl_dcc":       wl_dcc,
            "wl_net":       wl_net,
            "sp_fee":       sp_fee,
            "sp_cashback":  sp_cash,
            "sp_net":       sp_net,
            "delta":        delta,
            "delta_pct":    round(delta / max(abs(wl_net), 1e-9) * 100, 2),
            "by_cat":       _table_rows(by_cat,   "Karten Kategorie"),
            "by_brand":     _table_rows(by_brand, "Brand"),
            "proj":         proj,
            "proj_by_cat":  (_table_rows(proj_table, "Karten Kategorie")
                             if proj_table is not None else None),
        }

        with open(sid_dir / "summary.json", "w", encoding="utf-8") as fp:
            json.dump(summary, fp, ensure_ascii=False, default=str)

        return redirect(url_for("result", sid=sid))

    # ------------------------------------------------------------------
    @app.route("/result/<sid>")
    def result(sid):
        sid_dir = _UPLOAD_DIR / sid
        summary_path = sid_dir / "summary.json"
        if not summary_path.exists():
            flash("Session abgelaufen oder ungültig.")
            return redirect(url_for("index"))

        with open(summary_path, encoding="utf-8") as fp:
            data = json.load(fp)

        stem = data["stem"]
        downloads = []
        for fname, label in [
            (f"{stem}_kategorie.csv",    "CSV – nach Kategorie"),
            (f"{stem}_brand.csv",        "CSV – nach Brand"),
            (f"{stem}_hochrechnung.csv", "CSV – Hochrechnung"),
            (f"{stem}_report.pdf",       "PDF Report"),
        ]:
            if (sid_dir / fname).exists():
                downloads.append({"filename": fname, "label": label})

        return render_template("result.html", data=data, sid=sid, downloads=downloads)

    # ------------------------------------------------------------------
    @app.route("/download/<sid>/<filename>")
    def download(sid, filename):
        safe = secure_filename(filename)
        if safe != filename:
            abort(400)
        fp = _UPLOAD_DIR / sid / safe
        if not fp.exists():
            abort(404)
        return send_file(str(fp), as_attachment=True, download_name=safe)

    return app


def serve() -> None:
    """CLI entry point: start the web server."""
    setup_logging()
    app = create_app()
    print("\nSwiPay WL Compare  –  Web UI")
    print("Öffne http://localhost:8080 im Browser\n")
    app.run(host="127.0.0.1", port=8080, debug=False)
