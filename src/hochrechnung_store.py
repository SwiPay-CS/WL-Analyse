"""SQLite persistence for Jahresumsatz-Hochrechnung inputs (Einstellungen ->
Merchants -> Hochrechnung), keyed by Partner-ID or Gruppenname.

Durable across resets and across sessions (unlike the old data/session/
hochrechnung.json, which was tied to a single working analysis) -- analogous
to db_groups.py's partner_groups table. A merchant's Jahresumsatz is one fact
regardless of whether it was entered as a standalone row or expanded from
within a group: both write to the same scope='partner' record, keyed on
Partner-ID. The group scope only holds the single lump-sum entry used when no
member of that group has its own partner-level value (see aggregation.py).

Because the key is the Partner-ID/Gruppenname rather than the loaded export,
a previously entered value reappears automatically whenever that partner
resurfaces in a later analysis -- no explicit "reload this case" step needed.
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone

_DDL = """
CREATE TABLE IF NOT EXISTS hochrechnung (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    scope           TEXT    NOT NULL CHECK (scope IN ('partner', 'group')),
    key             TEXT    NOT NULL,
    jahresumsatz    REAL    NOT NULL,
    updated_at      TEXT    NOT NULL,
    correlation_id  TEXT    NOT NULL,
    UNIQUE(scope, key)
);
"""


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_hochrechnung_db(db_path: str) -> None:
    """Create the hochrechnung table if it does not exist yet."""
    with sqlite3.connect(db_path) as con:
        con.executescript(_DDL)


def _get_volumes(db_path: str, scope: str) -> dict[str, float]:
    with sqlite3.connect(db_path) as con:
        rows = con.execute(
            "SELECT key, jahresumsatz FROM hochrechnung WHERE scope = ?", (scope,)
        ).fetchall()
    return {k: float(v) for k, v in rows}


def get_partner_volumes(db_path: str) -> dict[str, float]:
    """{partner_id: jahresumsatz} for every partner with a stored value (> 0)."""
    return _get_volumes(db_path, "partner")


def get_group_volumes(db_path: str) -> dict[str, float]:
    """{group_name: jahresumsatz} -- the group-level lump sum, used only when
    no member of that group has its own partner-level entry."""
    return _get_volumes(db_path, "group")


def set_volume(db_path: str, scope: str, key: str, value: float) -> None:
    """Upsert one value. value <= 0 deletes the record instead (keeps the
    table clean and consistent with "0 = nichts hinterlegt")."""
    if scope not in ("partner", "group"):
        raise ValueError(f"Unbekannter Hochrechnung-Scope: {scope!r}")
    cid = str(uuid.uuid4())
    now = _utc()
    with sqlite3.connect(db_path) as con:
        if value > 0:
            con.execute(
                "INSERT INTO hochrechnung "
                "(scope, key, jahresumsatz, updated_at, correlation_id) "
                "VALUES (?,?,?,?,?) "
                "ON CONFLICT(scope, key) DO UPDATE SET "
                "jahresumsatz=excluded.jahresumsatz, updated_at=excluded.updated_at, "
                "correlation_id=excluded.correlation_id",
                (scope, key, float(value), now, cid),
            )
        else:
            con.execute(
                "DELETE FROM hochrechnung WHERE scope = ? AND key = ?", (scope, key)
            )
        con.execute(
            "INSERT INTO audit_log (correlation_id, event, detail, logged_at) "
            "VALUES (?,?,?,?)",
            (cid, "HOCHRECHNUNG_SET", f"scope={scope!r} key={key!r} value={value}", now),
        )


def save_volumes(
    db_path: str, partner_vols: dict[str, float], group_vols: dict[str, float]
) -> None:
    """Bulk upsert-or-delete both maps (used by the Einstellungen tab on every
    render, mirroring the current widget state)."""
    for pid, v in partner_vols.items():
        set_volume(db_path, "partner", pid, v)
    for gname, v in group_vols.items():
        set_volume(db_path, "group", gname, v)


def migrate_from_session_json(db_path: str, values: dict[str, float]) -> int:
    """One-time migration from the old data/session/hochrechnung.json widget-key
    format (hoch_vol_s_<pid>, hoch_vol_g_<name>) into the scope/key table.
    Zero/invalid entries are skipped. Returns the number of records written.

    Safe to call repeatedly/on an already-migrated DB: set_volume() upserts,
    so re-running just re-asserts the same values.
    """
    n = 0
    for k, v in values.items():
        if not isinstance(v, (int, float)) or v <= 0:
            continue
        if not isinstance(k, str) or not k.startswith("hoch_vol_"):
            continue
        rest = k[len("hoch_vol_"):]
        if rest.startswith("s_"):
            set_volume(db_path, "partner", rest[2:], float(v))
            n += 1
        elif rest.startswith("g_"):
            set_volume(db_path, "group", rest[2:], float(v))
            n += 1
    return n
