"""
Integrity layer: multi-file ingest with three-level duplicate detection,
row-level deduplication, fan-out flagging, and SQLite audit/hash persistence.

Level 1 — filename:      soft warning, not a blocker.
Level 2 — content hash:  hard block for bit-identical files.
Level 3 — row key:       backstop against partial period overlaps across runs.
"""

from __future__ import annotations

import hashlib
import sqlite3
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

from loader import load_worldline, add_keys


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------

@dataclass
class IngestReport:
    """Full record of a single ingest run."""
    correlation_id: str
    files_processed: list[str] = field(default_factory=list)
    files_blocked_hash: list[str] = field(default_factory=list)   # level-2 hard block
    files_warned_name: list[str] = field(default_factory=list)    # level-1 soft warn
    # New file bytes, but every row already registered (export re-saved). The
    # rows are returned for display, the file is not registered.
    files_known_rows: list[str] = field(default_factory=list)
    rows_new: int = 0
    rows_skipped_overlap: int = 0   # intra-batch dedup + level-3 inter-run backstop
    fanout_partner_ids: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# SQLite schema
# ---------------------------------------------------------------------------

_DDL = """
CREATE TABLE IF NOT EXISTS processed_files (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    file_name       TEXT    NOT NULL,
    file_hash       TEXT    NOT NULL UNIQUE,
    rows_added      INTEGER NOT NULL,
    correlation_id  TEXT    NOT NULL,
    processed_at    TEXT    NOT NULL
);
CREATE TABLE IF NOT EXISTS row_keys (
    idempotency_key TEXT    PRIMARY KEY,
    file_hash       TEXT    NOT NULL,
    correlation_id  TEXT    NOT NULL,
    ingested_at     TEXT    NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_log (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    correlation_id  TEXT    NOT NULL,
    event           TEXT    NOT NULL,
    detail          TEXT,
    logged_at       TEXT    NOT NULL
);
"""


def init_db(db_path: str) -> None:
    """Create tables if they do not exist yet."""
    with sqlite3.connect(db_path) as con:
        con.executescript(_DDL)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _file_hash(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65_536), b""):
            h.update(chunk)
    return h.hexdigest()


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _audit(con: sqlite3.Connection, cid: str, event: str, detail: str = "") -> None:
    con.execute(
        "INSERT INTO audit_log (correlation_id, event, detail, logged_at) "
        "VALUES (?,?,?,?)",
        (cid, event, detail, _utc()),
    )


def _known_keys(con: sqlite3.Connection, keys: list[str]) -> set[str]:
    """Return which keys are already in row_keys. Batched to stay within SQLite limits."""
    found: set[str] = set()
    for i in range(0, len(keys), 900):
        batch = keys[i : i + 900]
        ph = ",".join("?" for _ in batch)
        rows = con.execute(
            f"SELECT idempotency_key FROM row_keys WHERE idempotency_key IN ({ph})",
            batch,
        ).fetchall()
        found.update(r[0] for r in rows)
    return found


def _persist_keys(
    con: sqlite3.Connection, keys: list[str], fhash: str, cid: str
) -> None:
    now = _utc()
    con.executemany(
        "INSERT OR IGNORE INTO row_keys "
        "(idempotency_key, file_hash, correlation_id, ingested_at) VALUES (?,?,?,?)",
        [(k, fhash, cid, now) for k in keys],
    )


# ---------------------------------------------------------------------------
# Fan-out detection (transaction level)
# ---------------------------------------------------------------------------

def _detect_fanout(df: pd.DataFrame) -> list[str]:
    """Partner IDs with >= 2 contracts but identical total brutto per contract.

    Signals the source may have stamped the same data across contracts.
    Recommendation: do not sum across these contracts until manually resolved.
    """
    if "partner_id" not in df.columns or "vertragsnummer" not in df.columns:
        return []
    totals = (
        df.groupby(["partner_id", "vertragsnummer"])["brutto"]
        .sum()
        .reset_index()
    )
    summary = totals.groupby("partner_id").agg(
        n_contracts=("vertragsnummer", "nunique"),
        n_distinct=("brutto", lambda x: x.round(0).nunique()),
    )
    suspects = summary[(summary["n_contracts"] > 1) & (summary["n_distinct"] == 1)]
    return list(suspects.index.astype(str))


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def ingest_files(
    paths: list[str],
    db_path: str,
    sheet: str | None = None,
) -> tuple[pd.DataFrame, IngestReport]:
    """Load, deduplicate, and persist one or more Worldline export files.

    Returns the merged, deduplicated DataFrame and a full IngestReport.
    Blocked files contribute 0 rows; overlapping rows are dropped silently
    and counted in IngestReport.rows_skipped_overlap.
    """
    init_db(db_path)
    cid = str(uuid.uuid4())
    report = IngestReport(correlation_id=cid)

    with sqlite3.connect(db_path) as con:
        _audit(con, cid, "INGEST_START", f"files={len(paths)}")

        # Pass 1: pre-checks and loading.
        # accepted = files that are new and will be registered in the DB.
        # reload_only = files already known (hash-blocked) but still loaded for
        #               in-session display; they are NOT re-registered to avoid
        #               double-counting row_keys across runs.
        accepted:     list[tuple[pd.DataFrame, str, str]] = []
        reload_only:  list[tuple[pd.DataFrame, str, str]] = []

        for raw_path in paths:
            fname = Path(raw_path).name
            fhash = _file_hash(raw_path)

            # Level 1: soft filename warning.
            if con.execute(
                "SELECT 1 FROM processed_files WHERE file_name = ? LIMIT 1", (fname,)
            ).fetchone():
                report.files_warned_name.append(fname)
                _audit(con, cid, "WARN_FILENAME_SEEN", f"file={fname}")

            # Level 2: hard content-hash block.
            if con.execute(
                "SELECT 1 FROM processed_files WHERE file_hash = ? LIMIT 1", (fhash,)
            ).fetchone():
                report.files_blocked_hash.append(fname)
                _audit(con, cid, "BLOCK_DUPLICATE_FILE",
                       f"file={fname} hash={fhash[:12]}")
                # Still load into memory so the UI can display the data.
                df = load_worldline(raw_path, sheet=sheet)
                df = add_keys(df)
                reload_only.append((df, fhash, fname))
                continue

            df = load_worldline(raw_path, sheet=sheet)
            df = add_keys(df)
            accepted.append((df, fhash, fname))
            _audit(con, cid, "FILE_LOADED", f"file={fname} rows={len(df)}")

        if not accepted and not reload_only:
            _audit(con, cid, "INGEST_END", "rows_new=0 reason=all_blocked")
            return pd.DataFrame(), report

        if accepted:
            # Merge and dedup within this batch (same row present in two files).
            merged = pd.concat([df for df, _, _ in accepted], ignore_index=True)
            before = len(merged)
            merged = merged.drop_duplicates(subset=["idempotency_key"])
            intra = before - len(merged)
            if intra:
                report.rows_skipped_overlap += intra
                _audit(con, cid, "INTRA_BATCH_DEDUP", f"skipped={intra}")

            # Level 3: drop rows already in the DB from a previous run.
            all_keys = merged["idempotency_key"].tolist()
            already = _known_keys(con, all_keys)
            if already:
                report.rows_skipped_overlap += len(already)
                merged = merged[~merged["idempotency_key"].isin(already)]
                _audit(con, cid, "OVERLAP_ROWS_DROPPED", f"count={len(already)}")

            report.rows_new = len(merged)

            if merged.empty:
                # Every row is already registered although the file itself is
                # new -- the same export re-saved (Excel rewrites metadata, so
                # the content hash changes while every row stays identical).
                # Return the loaded rows for display, exactly like the
                # hash-blocked path above, instead of handing back an empty
                # frame that reads as "no data loaded". The file is NOT
                # registered: a processed_files row with rows_added=0 would
                # claim an ingest that did not happen.
                merged = pd.concat([d for d, _, _ in accepted], ignore_index=True)
                merged = merged.drop_duplicates(subset=["idempotency_key"])
                report.files_known_rows = [f for _, _, f in accepted]
                _audit(con, cid, "ALL_ROWS_ALREADY_KNOWN",
                       f"files={report.files_known_rows} rows={len(merged)}")
            else:
                # Persist: attribute each surviving row to the first file that
                # owns it.
                remaining: set[str] = set(merged["idempotency_key"].tolist())
                for df_part, fhash, fname in accepted:
                    mine = list(remaining & set(df_part["idempotency_key"].tolist()))
                    remaining -= set(mine)
                    _persist_keys(con, mine, fhash, cid)
                    con.execute(
                        "INSERT OR IGNORE INTO processed_files "
                        "(file_name, file_hash, rows_added, correlation_id, "
                        "processed_at) VALUES (?,?,?,?,?)",
                        (fname, fhash, len(mine), cid, _utc()),
                    )
                    report.files_processed.append(fname)
        else:
            # All files were hash-blocked; rows_new stays 0.
            # Return the reload_only data (already in DB) for display.
            merged = pd.concat([df for df, _, _ in reload_only], ignore_index=True)
            merged = merged.drop_duplicates(subset=["idempotency_key"])

        # Fan-out detection on the full accepted dataset.
        all_accepted = pd.concat(
            [df for df, _, _ in (accepted + reload_only)], ignore_index=True
        )
        report.fanout_partner_ids = _detect_fanout(all_accepted)
        if report.fanout_partner_ids:
            _audit(con, cid, "FANOUT_SUSPECTED",
                   f"partners={report.fanout_partner_ids}")

        _audit(con, cid, "INGEST_END",
               f"rows_new={report.rows_new} rows_skipped={report.rows_skipped_overlap}")

    return merged, report
