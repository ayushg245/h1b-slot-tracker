"""SQLite file that remembers which reports were already seen, so only new ones alert."""

from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .parser import SlotReport

SCHEMA = """
CREATE TABLE IF NOT EXISTS seen (
    uid TEXT PRIMARY KEY,
    consulate TEXT, visa TEXT, appointment_type TEXT,
    availability TEXT, slots INTEGER, reported TEXT,
    first_seen_utc TEXT, alerted INTEGER
);
CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT);
"""


class State:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(self.path)
        self.db.executescript(SCHEMA)

    def is_empty(self) -> bool:
        return self.db.execute("SELECT COUNT(*) FROM seen").fetchone()[0] == 0

    def has(self, uid: str) -> bool:
        return self.db.execute("SELECT 1 FROM seen WHERE uid=?", (uid,)).fetchone() is not None

    def add(self, r: SlotReport, alerted: bool):
        self.db.execute(
            "INSERT OR IGNORE INTO seen VALUES (?,?,?,?,?,?,?,?,?)",
            (r.uid, r.consulate, r.visa, r.appointment_type, r.availability_raw, r.slots,
             r.reported_raw, datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S"), int(alerted)))

    def get_meta(self, k: str) -> str | None:
        row = self.db.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
        return row[0] if row else None

    def set_meta(self, k: str, v: str):
        self.db.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (k, v))

    def prune(self, keep_days: int = 60):
        self.db.execute("DELETE FROM seen WHERE first_seen_utc < datetime('now', ?)", (f"-{keep_days} days",))

    def commit(self):
        self.db.commit()

    def close(self):
        self.db.commit()
        self.db.close()
