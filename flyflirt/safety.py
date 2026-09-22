"""Trust & safety state: blocks, temporary bans, strikes, "skip" cool-offs and report handling.

Everything is keyed by the anonymous client id (the signed session cookie). Blocks and bans are
persisted in SQLite so they survive a restart; strikes and skip cool-offs are short-lived and
in memory. Nothing here identifies a real person.
"""
from __future__ import annotations

import threading
import time

REPORT_CATEGORIES = {
    "harassment": "Harassment or bullying",
    "sexual": "Sexual or explicit content",
    "hate": "Hate speech",
    "threat": "Threats or self-harm",
    "spam": "Spam or scams",
    "other": "Something else",
}


class Safety:
    def __init__(self, storage, *, strike_limit=3, strike_window_s=600, strike_ban_s=900,
                 report_ban_threshold=3, report_ban_s=3600, skip_avoid_s=1800):
        self.storage = storage
        self.strike_limit, self.strike_window_s, self.strike_ban_s = strike_limit, strike_window_s, strike_ban_s
        self.report_ban_threshold, self.report_ban_s = report_ban_threshold, report_ban_s
        self.skip_avoid_s = skip_avoid_s
        self._lock = threading.RLock()
        self._blocks: dict[str, set[str]] = {}      # blocker -> {blocked}
        self._bans: dict[str, tuple[float, str]] = {}  # client -> (until, reason)
        self._avoid: dict[frozenset, float] = {}    # pair -> until (someone you just skipped)
        self._strikes: dict[str, list[float]] = {}
        for blocker, blocked in storage.load_blocks():
            self._blocks.setdefault(blocker, set()).add(blocked)
        for client, until, reason in storage.load_bans(time.time()):
            self._bans[client] = (until, reason)

    # -- blocks & skips ----------------------------------------------------------------
    def block(self, blocker: str, blocked: str) -> None:
        if not blocker or not blocked or blocker == blocked:
            return
        with self._lock:
            self._blocks.setdefault(blocker, set()).add(blocked)
        self.storage.add_block(blocker, blocked)

    def skip(self, a: str, b: str) -> None:
        """You skipped each other: don't pair the same two people straight away again."""
        with self._lock:
            self._avoid[frozenset((a, b))] = time.time() + self.skip_avoid_s

    def blocked_between(self, a: str, b: str) -> bool:
        with self._lock:
            if b in self._blocks.get(a, ()) or a in self._blocks.get(b, ()):
                return True
            until = self._avoid.get(frozenset((a, b)))
            if until is not None:
                if until > time.time():
                    return True
                self._avoid.pop(frozenset((a, b)), None)
        return False

    # -- bans --------------------------------------------------------------------------
    def ban(self, client: str, seconds: float, reason: str) -> None:
        until = time.time() + seconds
        with self._lock:
            self._bans[client] = (until, reason)
        self.storage.set_ban(client, until, reason)

    def banned_for(self, client: str) -> int:
        """Seconds left on a ban (0 when not banned)."""
        with self._lock:
            entry = self._bans.get(client)
            if not entry:
                return 0
            left = entry[0] - time.time()
            if left <= 0:
                self._bans.pop(client, None)
                return 0
            return int(left) + 1

    # -- strikes (blocked messages) ----------------------------------------------------
    def strike(self, client: str) -> tuple[int, bool]:
        """Record a blocked message. Returns (strikes in window, True if the client is now banned)."""
        now = time.time()
        with self._lock:
            hits = [t for t in self._strikes.get(client, []) if now - t < self.strike_window_s] + [now]
            self._strikes[client] = hits
            over = len(hits) >= self.strike_limit
            if over:
                self._strikes[client] = []
        if over:
            self.ban(client, self.strike_ban_s, "repeated rule-breaking messages")
        return len(hits), over

    # -- reports -----------------------------------------------------------------------
    def report(self, room_id: str, reporter: str, reported: str, category: str, reason: str) -> dict:
        """Store (or update) this reporter's report of this room and apply the automatic response."""
        if category not in REPORT_CATEGORIES:
            category = "other"
        created = self.storage.upsert_report(room_id, reporter, reported, category, reason)
        distinct = self.storage.distinct_reporters(reported, time.time() - 86400)
        banned = False
        if distinct >= self.report_ban_threshold and not self.banned_for(reported):
            self.ban(reported, self.report_ban_s, f"reported by {distinct} different people")
            banned = True
        return {"new": created, "category": category, "reporters_24h": distinct, "auto_banned": banned}
