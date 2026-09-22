"""Matchmaking queue.

Every ticket says who you are (``mode``: male / female / friends = "prefer not to say") and who
you want to meet (``seeking``: male / female / any). Two tickets pair when each one's preference
accepts the other's gender.
FIFO: the longest-waiting compatible person is matched first. After ``MATCH_TIMEOUT_S``
without a partner the waiting user is told to invite a friend with a code instead (they
keep being matched in the background if someone compatible shows up).
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass, field

PAIRS = {"male": "female", "female": "male", "friends": "friends"}   # default preference per mode
SEEKING = ("male", "female", "any")


def default_seeking(mode: str) -> str:
    return {"male": "female", "female": "male"}.get(mode, "any")


def accepts(seeking: str, mode: str) -> bool:
    return seeking == "any" or seeking == mode


@dataclass
class Ticket:
    client_id: str
    sid: str
    nickname: str
    mode: str
    joined_at: float = field(default_factory=time.time)
    suggested: bool = False
    seeking: str = ""

    def __post_init__(self):
        if self.seeking not in SEEKING:
            self.seeking = default_seeking(self.mode)

    @property
    def is_flirt(self) -> bool:
        """A dating-style pairing needs two explicit opposite genders who each asked for the other."""
        return self.mode in ("male", "female") and self.seeking == PAIRS[self.mode]

    @property
    def label(self) -> str:
        return {"male": "Male", "female": "Female", "friends": "Friend"}[self.mode]


class Matchmaker:
    def __init__(self, timeout_s: float):
        self.timeout_s = timeout_s
        self._tickets: dict[str, Ticket] = {}
        self._lock = threading.RLock()
        self.is_blocked = lambda a, b: False   # set by the app: blocks and 'skipped' pairs are never matched

    def enqueue(self, ticket: Ticket) -> Ticket | None:
        """Add a ticket (replacing any earlier one from the same client). If a compatible
        partner is already waiting, remove both and return the partner."""
        if ticket.mode not in PAIRS:
            raise ValueError("unknown mode")
        with self._lock:
            previous = self._tickets.pop(ticket.client_id, None)
            if previous is not None:
                ticket.joined_at = previous.joined_at  # a refresh must not lose your place in line
                ticket.suggested = previous.suggested
            candidates = [t for t in self._tickets.values() if t.client_id != ticket.client_id
                          and accepts(ticket.seeking, t.mode) and accepts(t.seeking, ticket.mode)
                          and not self.is_blocked(ticket.client_id, t.client_id)]
            if candidates:
                partner = min(candidates, key=lambda t: t.joined_at)
                self._tickets.pop(partner.client_id, None)
                return partner
            self._tickets[ticket.client_id] = ticket
            return None

    def cancel(self, client_id: str) -> Ticket | None:
        with self._lock:
            return self._tickets.pop(client_id, None)

    def get(self, client_id: str) -> Ticket | None:
        with self._lock:
            return self._tickets.get(client_id)

    def drop_sid(self, sid: str) -> list[Ticket]:
        with self._lock:
            gone = [t for t in self._tickets.values() if t.sid == sid]
            for t in gone:
                self._tickets.pop(t.client_id, None)
            return gone

    def due_for_invite(self, now: float | None = None) -> list[Ticket]:
        """Tickets that have waited past the timeout and have not been told yet."""
        now = now or time.time()
        with self._lock:
            due = [t for t in self._tickets.values() if not t.suggested and now - t.joined_at >= self.timeout_s]
            for t in due:
                t.suggested = True
            return due

    def snapshot(self) -> list[Ticket]:
        with self._lock:
            return list(self._tickets.values())

    def counts(self) -> dict:
        with self._lock:
            out = {"male": 0, "female": 0, "friends": 0}
            for t in self._tickets.values():
                out[t.mode] += 1
            return out

    def position(self, client_id: str) -> int:
        with self._lock:
            me = self._tickets.get(client_id)
            if me is None:
                return 0
            return 1 + sum(1 for t in self._tickets.values() if t.mode == me.mode and t.seeking == me.seeking and t.joined_at < me.joined_at)
