"""SQLite storage for availability snapshots.

One row per (venue, item, slot, observation). The tracker appends snapshots on
every poll; the report reads them back and works out which slots went unsold.

Times are stored in UTC ISO-8601 so slots and observations compare directly,
regardless of venue timezone.
"""

from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

DEFAULT_DB = Path(__file__).parent / "tracking.db"

SCHEMA = """
CREATE TABLE IF NOT EXISTS snapshots (
    venue_id      TEXT NOT NULL,
    venue_name    TEXT NOT NULL,
    item_id       TEXT NOT NULL,   -- platform sub-resource: FareHarbor item pk, tennis court id, ...
    item_name     TEXT NOT NULL,
    slot_start    TEXT NOT NULL,   -- UTC ISO, slot start
    slot_end      TEXT NOT NULL,   -- UTC ISO, slot end
    slot_local    TEXT NOT NULL,   -- local wall-clock start, for display (e.g. 2026-08-22 14:00)
    observed_at   TEXT NOT NULL,   -- UTC ISO, when we polled
    is_available  INTEGER NOT NULL,-- 1 = open/bookable, 0 = sold/taken
    capacity      INTEGER,         -- capacity-based venues: free units (e.g. free bays) this hour; else NULL
    capacity_total INTEGER,        -- capacity-based venues: total units (e.g. total bays); else NULL
    PRIMARY KEY (venue_id, item_id, slot_start, observed_at)
);
CREATE INDEX IF NOT EXISTS idx_slot ON snapshots (venue_id, item_id, slot_start);
CREATE INDEX IF NOT EXISTS idx_observed ON snapshots (observed_at);

CREATE TABLE IF NOT EXISTS poll_log (
    observed_at TEXT NOT NULL,
    venue_id    TEXT NOT NULL,
    status      TEXT NOT NULL,   -- 'ok' | 'error'
    slots_seen  INTEGER NOT NULL,
    detail      TEXT
);
"""


@dataclass
class Slot:
    """One availability observation from an adapter."""
    item_id: str
    item_name: str
    slot_start_utc: str
    slot_end_utc: str
    slot_local: str
    is_available: bool
    capacity: int | None = None        # capacity venues: free units this slot
    capacity_total: int | None = None  # capacity venues: total units


def _migrate(conn):
    """Add columns introduced after the first DB was created."""
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(snapshots)")}
    if "capacity_total" not in cols:
        conn.execute("ALTER TABLE snapshots ADD COLUMN capacity_total INTEGER")


@contextmanager
def connect(db_path: str | Path = DEFAULT_DB):
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    try:
        conn.executescript(SCHEMA)
        _migrate(conn)
        yield conn
        conn.commit()
    finally:
        conn.close()


def record_snapshot(conn, venue_id: str, venue_name: str, observed_at: str, slots: list[Slot]) -> int:
    rows = [
        (venue_id, venue_name, s.item_id, s.item_name, s.slot_start_utc, s.slot_end_utc,
         s.slot_local, observed_at, 1 if s.is_available else 0, s.capacity, s.capacity_total)
        for s in slots
    ]
    conn.executemany(
        "INSERT OR REPLACE INTO snapshots "
        "(venue_id, venue_name, item_id, item_name, slot_start, slot_end, slot_local, "
        " observed_at, is_available, capacity, capacity_total) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        rows,
    )
    return len(rows)


def record_poll(conn, observed_at: str, venue_id: str, status: str, slots_seen: int, detail: str = ""):
    conn.execute(
        "INSERT INTO poll_log (observed_at, venue_id, status, slots_seen, detail) VALUES (?,?,?,?,?)",
        (observed_at, venue_id, status, slots_seen, detail),
    )
