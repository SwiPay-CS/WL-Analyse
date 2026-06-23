"""SQLite persistence for manual partner-group assignments (Phase 4).

Each partner_id belongs to at most one group (UNIQUE constraint). Re-assigning
a partner_id to a new group replaces the old assignment via INSERT OR REPLACE.
Every mutation is logged to the shared audit_log table (created by init_db).
"""

from __future__ import annotations

import sqlite3
import uuid
from datetime import datetime, timezone


_DDL = """
CREATE TABLE IF NOT EXISTS partner_groups (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    group_name      TEXT    NOT NULL,
    partner_id      TEXT    NOT NULL,
    assigned_at     TEXT    NOT NULL,
    correlation_id  TEXT    NOT NULL,
    UNIQUE(partner_id)
);
"""


def _utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def init_groups_db(db_path: str) -> None:
    """Create partner_groups table if it does not exist yet."""
    with sqlite3.connect(db_path) as con:
        con.executescript(_DDL)


def get_groups(db_path: str) -> dict[str, list[str]]:
    """Return {group_name: [partner_id, ...]} for all stored assignments."""
    with sqlite3.connect(db_path) as con:
        rows = con.execute(
            "SELECT group_name, partner_id "
            "FROM partner_groups ORDER BY group_name, partner_id"
        ).fetchall()
    result: dict[str, list[str]] = {}
    for gname, pid in rows:
        result.setdefault(gname, []).append(pid)
    return result


def assign_group(db_path: str, group_name: str, partner_ids: list[str]) -> None:
    """Assign partner_ids to group_name, replacing any prior assignment for each ID."""
    cid = str(uuid.uuid4())
    now = _utc()
    with sqlite3.connect(db_path) as con:
        con.executemany(
            "INSERT OR REPLACE INTO partner_groups "
            "(group_name, partner_id, assigned_at, correlation_id) VALUES (?,?,?,?)",
            [(group_name, pid, now, cid) for pid in partner_ids],
        )
        con.execute(
            "INSERT INTO audit_log "
            "(correlation_id, event, detail, logged_at) VALUES (?,?,?,?)",
            (cid, "GROUP_ASSIGN", f"group={group_name!r} n={len(partner_ids)}", now),
        )


def unassign_group(db_path: str, partner_ids: list[str]) -> None:
    """Remove partner_ids from whatever group they belong to."""
    cid = str(uuid.uuid4())
    now = _utc()
    with sqlite3.connect(db_path) as con:
        con.executemany(
            "DELETE FROM partner_groups WHERE partner_id = ?",
            [(pid,) for pid in partner_ids],
        )
        con.execute(
            "INSERT INTO audit_log "
            "(correlation_id, event, detail, logged_at) VALUES (?,?,?,?)",
            (cid, "GROUP_UNASSIGN", f"n={len(partner_ids)}", now),
        )
