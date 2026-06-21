"""Console entry point: compare a Worldline export against SwiPay offer pricing."""
from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from .engine import run_engine
from .loader import load_file
from .logging_setup import setup_logging
from .pdf_report import to_pdf
from .report import build_breakdown, to_csv, to_html

logger = logging.getLogger(__name__)


def _check_identity(df) -> None:
    """Print identity check: Gebühren = Processing Fee + Scheme Fee + Interchange."""
    cols = ["Processing Fee", "Scheme Fee", "Interchange"]
    mask = df[cols].notna().all(axis=1)
    recomputed = df.loc[mask, cols].sum(axis=1)
    diff = (df.loc[mask, "Gebühren"] - recomputed).round(2)
    mismatches = int((diff != 0).sum())
    max_dev = float(diff.abs().max())
    logger.info(
        "Identity check: %d rows | max deviation %.4f CHF | mismatches %d",
        int(mask.sum()),
        max_dev,
        mismatches,
    )
    print("\n1) IDENTITÄT  Gebühren = PF + SF + IC")
    print(
        f"   geprüft: {int(mask.sum()):>7} Zeilen | "
        f"max. Abweichung: {max_dev:.2f} CHF | "
        f"Mismatches: {mismatches}"
    )


def main() -> None:
    """CLI entry point registered as 'swipay-wl-compare'."""
    parser = argparse.ArgumentParser(
        prog="swipay-wl-compare",
        description="Worldline-Transaktionsexport gegen SwiPay-Konditionen vergleichen",
    )
    parser.add_argument(
        "file",
        help="Pfad zur Worldline-Exportdatei (.xlsb oder .csv)",
    )
    parser.add_argument(
        "--output-dir",
        default="output",
        help="Ordner für CSV- und HTML-Reports (Standard: output/)",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Log-Level (Standard: INFO)",
    )
    args = parser.parse_args()

    cid = setup_logging(level=getattr(logging, args.log_level))
    logger.info("swipay-wl-compare started")

    path = Path(args.file).expanduser().resolve()
    if not path.exists():
        print(f"Fehler: Datei nicht gefunden: {path}", file=sys.stderr)
        sys.exit(1)

    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        df = load_file(path)
    except Exception as exc:
        logger.error("Datei konnte nicht geladen werden: %s", exc)
        sys.exit(1)

    print(f"\nDatensatz: {len(df):,} Transaktionen aus '{path.name}'")

    _check_identity(df)

    wl_fee = float(-df["Gebühren"].sum())
    wl_dcc = float(df["DCC Payback"].fillna(0).sum())
    wl_net = wl_fee - wl_dcc

    print("\n2) WORLDLINE IST")
    print(
        f"   Gebühren:  {wl_fee:>12,.2f} CHF | "
        f"DCC-Cashback: {wl_dcc:>10,.2f} CHF | "
        f"Netto: {wl_net:>12,.2f} CHF"
    )

    try:
        eng = run_engine(df)
    except Exception as exc:
        logger.error("Engine-Fehler: %s", exc)
        sys.exit(1)

    sp_fee = float(eng["sp_fee"].sum())
    sp_cashback = float(eng["sp_cashback"].sum())
    sp_net = sp_fee - sp_cashback

    print("\n3) SWIPAY (Parameter — NICHT die echte Preisliste)")
    print(
        f"   Gebühren:  {sp_fee:>12,.2f} CHF | "
        f"DCC-Cashback: {sp_cashback:>10,.2f} CHF | "
        f"Netto: {sp_net:>12,.2f} CHF"
    )
    print(f"   Differenz Netto (Worldline − SwiPay): {wl_net - sp_net:>12,.2f} CHF")

    n_floored = int(eng["floored"].sum())
    n_offer = int(eng["offerable"].sum())
    print("\n4) MINDESTGEBÜHR-FLOOR (transaktionsgenau)")
    print(
        f"   offerierbare Transaktionen: {n_offer:,} | "
        f"davon gefloored: {n_floored:,} "
        f"({n_floored / max(n_offer, 1) * 100:.1f} %)"
    )

    dcc_rows = df["DCC"].astype(str).str.lower().eq("ja")
    print("\n5) DCC-REIHENFOLGE  (Floor zuerst, Cashback danach separat)")
    print(
        f"   DCC-Transaktionen: {int(dcc_rows.sum()):,} | "
        f"SwiPay-Cashback total: {sp_cashback:,.2f} CHF | "
        f"Worldline-Cashback: {wl_dcc:,.2f} CHF"
    )

    # --- Reports ---
    stem = path.stem
    meta = f"Quelle: {path.name} | {len(df):,} Transaktionen | Parameter: SwiPay"

    by_cat = build_breakdown(df, eng, "Karten Kategorie")
    by_brand = build_breakdown(df, eng, "Brand")

    to_csv(by_cat,   out_dir / f"{stem}_nach_kategorie.csv")
    to_csv(by_brand, out_dir / f"{stem}_nach_brand.csv")

    to_html(
        {
            "Nach Karten-Kategorie": by_cat,
            "Nach Brand":           by_brand,
        },
        out_dir / f"{stem}_report.html",
        filename=f"SwiPay-Vergleich {stem}",
        meta=meta,
    )

    _COL_W = [42, 20, 32, 32, 36, 26]
    to_pdf(
        {
            "Nach Karten-Kategorie": (by_cat,   "Karten Kategorie", _COL_W),
            "Nach Brand":            (by_brand,  "Brand",            _COL_W),
        },
        out_dir / f"{stem}_report.pdf",
        title=f"SwiPay-Vergleich {stem}",
        meta=meta,
    )

    print(f"\n6) REPORTS gespeichert in '{out_dir}/'")
    print(f"   {stem}_nach_kategorie.csv")
    print(f"   {stem}_nach_brand.csv")
    print(f"   {stem}_report.html")
    print(f"   {stem}_report.pdf")

    logger.info(
        "Run complete — WL net=%.2f CHF, SP net=%.2f CHF, delta=%.2f CHF",
        wl_net,
        sp_net,
        wl_net - sp_net,
    )
