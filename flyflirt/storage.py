"""SQLite persistence (WAL). Small, synchronous, thread-safe via a single connection + lock.

Stores rooms, participants and messages (with the LLM parameters that drove the
simulation), so a room can be rebuilt after a restart by replaying its messages through
the deterministic engine. Also keeps per-model LLM usage counters and abuse reports.
"""
from __future__ import annotations

import json
import os
import sqlite3
import threading
import time

SCHEMA = """
CREATE TABLE IF NOT EXISTS rooms (
    id TEXT PRIMARY KEY,
    code TEXT,
    mode TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at REAL NOT NULL,
    started_at REAL,
    ended_at REAL,
    share_token TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS idx_rooms_code ON rooms(code) WHERE code IS NOT NULL;
CREATE INDEX IF NOT EXISTS idx_rooms_share ON rooms(share_token);
CREATE TABLE IF NOT EXISTS participants (
    room_id TEXT NOT NULL,
    slot INTEGER NOT NULL,
    client_id TEXT NOT NULL,
    label TEXT NOT NULL,
    nickname TEXT NOT NULL,
    PRIMARY KEY (room_id, slot),
    FOREIGN KEY (room_id) REFERENCES rooms(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS idx_participants_client ON participants(client_id);
CREATE TABLE IF NOT EXISTS messages (
    room_id TEXT NOT NULL,
    seq INTEGER NOT NULL,
    slot INTEGER NOT NULL,
    text TEXT NOT NULL,
    ts REAL NOT NULL,
    params TEXT,
    diary TEXT,
    tip TEXT,
    topic TEXT,
    provider TEXT,
    degraded INTEGER DEFAULT 0,
    PRIMARY KEY (room_id, seq),
    FOREIGN KEY (room_id) REFERENCES rooms(id) ON DELETE CASCADE
);
CREATE TABLE IF NOT EXISTS reports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    room_id TEXT NOT NULL,
    reporter TEXT NOT NULL,
    reason TEXT,
    ts REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS blocks (
    blocker TEXT NOT NULL,
    blocked TEXT NOT NULL,
    ts REAL NOT NULL,
    PRIMARY KEY (blocker, blocked)
);
CREATE TABLE IF NOT EXISTS bans (
    client TEXT PRIMARY KEY,
    until REAL NOT NULL,
    reason TEXT
);
CREATE TABLE IF NOT EXISTS llm_usage (
    day TEXT NOT NULL,
    provider TEXT NOT NULL,
    tokens INTEGER NOT NULL DEFAULT 0,
    requests INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (day, provider)
);
"""


class Storage:
    def __init__(self, path: str, retain_text: bool = False):
        self.path = path
        # Privacy-by-design default (see Config.RETAIN_MESSAGE_TEXT): the words people typed, and the
        # LLM's paraphrase of them (diary/tip/topic), are not written to disk unless explicitly opted in.
        # The numbers derived from a message (params, provider, timing) are kept either way, since that's
        # all the verdict/replay machinery needs. The live in-memory room is unaffected by this setting.
        self.retain_text = retain_text
        if path != ":memory:":
            os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        self._lock = threading.RLock()
        self._db = sqlite3.connect(path, check_same_thread=False, isolation_level=None, timeout=15)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.execute("PRAGMA journal_mode=WAL")
            self._db.execute("PRAGMA synchronous=NORMAL")
            self._db.execute("PRAGMA foreign_keys=ON")
            self._db.executescript(SCHEMA)
            for table, column, decl in (("reports", "reported", "TEXT"), ("reports", "category", "TEXT"),
                                        ("rooms", "scale", "TEXT DEFAULT 'standard'"),
                                        ("rooms", "store_override", "INTEGER"),   # NULL = follow the server default
                                        ("reports", "resolved", "INTEGER DEFAULT 0")):   # migrate older databases
                try:
                    self._db.execute(f"ALTER TABLE {table} ADD COLUMN {column} {decl}")
                except sqlite3.OperationalError:
                    pass

    def _run(self, sql: str, params: tuple = ()):
        with self._lock:
            return self._db.execute(sql, params)

    def _all(self, sql: str, params: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._db.execute(sql, params).fetchall()

    def _one(self, sql: str, params: tuple = ()):
        with self._lock:
            return self._db.execute(sql, params).fetchone()

    # -- rooms -------------------------------------------------------------------------
    def create_room(self, room_id: str, code: str | None, mode: str, status: str, share_token: str, ts: float) -> None:
        self._run(
            "INSERT INTO rooms (id, code, mode, status, created_at, share_token) VALUES (?,?,?,?,?,?)",
            (room_id, code, mode, status, ts, share_token),
        )

    def set_status(self, room_id: str, status: str, ts: float | None = None) -> None:
        field = {"active": "started_at", "ended": "ended_at"}.get(status)
        if field and ts is not None:
            self._run(f"UPDATE rooms SET status=?, {field}=? WHERE id=?", (status, ts, room_id))
        else:
            self._run("UPDATE rooms SET status=? WHERE id=?", (status, room_id))

    def set_scale(self, room_id: str, scale: str) -> None:
        self._run("UPDATE rooms SET scale=? WHERE id=?", (scale, room_id))

    def set_store_override(self, room_id: str, override: bool | None) -> None:
        value = None if override is None else (1 if override else 0)
        self._run("UPDATE rooms SET store_override=? WHERE id=?", (value, room_id))

    def clear_code(self, room_id: str) -> None:
        self._run("UPDATE rooms SET code=NULL WHERE id=?", (room_id,))

    def delete_room(self, room_id: str) -> None:
        self._run("DELETE FROM rooms WHERE id=?", (room_id,))

    def add_participant(self, room_id: str, slot: int, client_id: str, label: str, nickname: str) -> None:
        self._run(
            "INSERT OR REPLACE INTO participants (room_id, slot, client_id, label, nickname) VALUES (?,?,?,?,?)",
            (room_id, slot, client_id, label, nickname),
        )

    def add_message(self, room_id: str, msg, retain_text: bool | None = None) -> None:
        """``retain_text`` overrides the server-wide default for this one call (used for a room's own
        "save this chat" choice); leave it as None to just follow ``self.retain_text``."""
        retain = self.retain_text if retain_text is None else retain_text
        text, diary, tip, topic = (msg.text, msg.diary, msg.tip, msg.topic) if retain else ("", None, None, None)
        self._run(
            "INSERT OR REPLACE INTO messages (room_id, seq, slot, text, ts, params, diary, tip, topic, provider, degraded)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (room_id, msg.seq, msg.slot, text, msg.ts, json.dumps(msg.params) if msg.params else None,
             diary, tip, topic, msg.provider, 1 if msg.degraded else 0),
        )

    def load_room(self, room_id: str) -> dict | None:
        room = self._one("SELECT * FROM rooms WHERE id=?", (room_id,))
        if room is None:
            return None
        return {
            "room": dict(room),
            "participants": [dict(r) for r in self._all("SELECT * FROM participants WHERE room_id=? ORDER BY slot", (room_id,))],
            "messages": [dict(r) for r in self._all("SELECT * FROM messages WHERE room_id=? ORDER BY seq", (room_id,))],
        }

    def room_id_by_share_token(self, token: str) -> str | None:
        row = self._one("SELECT id FROM rooms WHERE share_token=?", (token,))
        return row["id"] if row else None

    def room_id_by_code(self, code: str) -> str | None:
        row = self._one("SELECT id FROM rooms WHERE code=?", (code,))
        return row["id"] if row else None

    def rooms_for_client(self, client_id: str, limit: int = 5) -> list[dict]:
        rows = self._all(
            "SELECT r.id, r.mode, r.status, r.created_at FROM rooms r JOIN participants p ON p.room_id=r.id "
            "WHERE p.client_id=? AND r.status IN ('active','waiting') ORDER BY r.created_at DESC LIMIT ?",
            (client_id, limit),
        )
        return [dict(r) for r in rows]

    # -- housekeeping ------------------------------------------------------------------
    def purge_before(self, cutoff_ts: float) -> int:
        with self._lock:
            cur = self._db.execute("DELETE FROM rooms WHERE created_at < ?", (cutoff_ts,))
            self._db.execute("DELETE FROM reports WHERE ts < ?", (cutoff_ts,))
            self._db.execute("DELETE FROM blocks WHERE ts < ?", (cutoff_ts - 23 * 86400,))   # blocks outlive chats: ~30 days
            return cur.rowcount

    def rooms_started_since(self, ts: float) -> int:
        row = self._one("SELECT COUNT(*) AS n FROM rooms WHERE started_at IS NOT NULL AND started_at >= ?", (ts,))
        return int(row["n"]) if row else 0

    def add_report(self, room_id: str, reporter: str, reason: str) -> None:
        self._run("INSERT INTO reports (room_id, reporter, reason, ts) VALUES (?,?,?,?)", (room_id, reporter, reason, time.time()))

    def upsert_report(self, room_id: str, reporter: str, reported: str, category: str, reason: str) -> bool:
        """One report per reporter per room: a second submission updates the first. True if new."""
        with self._lock:
            row = self._db.execute("SELECT id FROM reports WHERE room_id=? AND reporter=?", (room_id, reporter)).fetchone()
            if row:
                self._db.execute("UPDATE reports SET reason=?, category=?, reported=?, ts=? WHERE id=?",
                                 (reason, category, reported, time.time(), row["id"]))
                return False
            self._db.execute("INSERT INTO reports (room_id, reporter, reported, category, reason, ts) VALUES (?,?,?,?,?,?)",
                             (room_id, reporter, reported, category, reason, time.time()))
            return True

    def distinct_reporters(self, reported: str, since: float) -> int:
        row = self._one("SELECT COUNT(DISTINCT reporter) AS n FROM reports WHERE reported=? AND ts>=?", (reported, since))
        return int(row["n"]) if row else 0

    def list_reports(self, resolved: bool | None = None, limit: int = 200) -> list[dict]:
        sql, params = "SELECT * FROM reports", ()
        if resolved is not None:
            sql += " WHERE resolved=?"
            params = (1 if resolved else 0,)
        sql += " ORDER BY ts DESC LIMIT ?"
        return [dict(r) for r in self._all(sql, params + (limit,))]

    def resolve_report(self, report_id: int, resolved: bool = True) -> None:
        self._run("UPDATE reports SET resolved=? WHERE id=?", (1 if resolved else 0, report_id))

    # -- blocks & bans -----------------------------------------------------------------
    def add_block(self, blocker: str, blocked: str) -> None:
        self._run("INSERT OR REPLACE INTO blocks (blocker, blocked, ts) VALUES (?,?,?)", (blocker, blocked, time.time()))

    def load_blocks(self) -> list[tuple[str, str]]:
        return [(r["blocker"], r["blocked"]) for r in self._all("SELECT blocker, blocked FROM blocks")]

    def set_ban(self, client: str, until: float, reason: str) -> None:
        self._run("INSERT OR REPLACE INTO bans (client, until, reason) VALUES (?,?,?)", (client, until, reason))

    def load_bans(self, now: float) -> list[tuple[str, float, str]]:
        self._run("DELETE FROM bans WHERE until < ?", (now,))
        return [(r["client"], r["until"], r["reason"]) for r in self._all("SELECT client, until, reason FROM bans")]

    def delete_ban(self, client: str) -> None:
        self._run("DELETE FROM bans WHERE client=?", (client,))

    # -- LLM usage counters (persist daily quota accounting across restarts) -----------
    def load(self, day: str) -> dict[str, tuple[int, int]]:
        rows = self._all("SELECT provider, tokens, requests FROM llm_usage WHERE day=?", (day,))
        return {r["provider"]: (int(r["tokens"]), int(r["requests"])) for r in rows}

    def add(self, day: str, provider: str, tokens: int, requests: int) -> None:
        self._run(
            "INSERT INTO llm_usage (day, provider, tokens, requests) VALUES (?,?,?,?) "
            "ON CONFLICT(day, provider) DO UPDATE SET tokens=tokens+excluded.tokens, requests=requests+excluded.requests",
            (day, provider, tokens, requests),
        )

    def close(self) -> None:
        with self._lock:
            self._db.close()
