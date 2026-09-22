"""Conversation rooms: participants, messages, per-room simulation, restore-by-replay."""
from __future__ import annotations

import json
import logging
import sqlite3
import threading
import time
from collections import deque
from dataclasses import dataclass, field

from .connectome import Connectome
from .engine import EngineConfig, RoomEngine
from .narrate import narrate
from .util import new_invite_code, new_room_id, new_share_token

log = logging.getLogger("flyflirt.rooms")

LABELS = {"male": "Male", "female": "Female", "friends": "Friend"}
PARTNER_LABEL = {"Male": "Female", "Female": "Male", "Friend": "Friend"}


@dataclass
class Participant:
    slot: int
    client_id: str
    label: str
    nickname: str
    sids: set = field(default_factory=set)
    last_seen: float = field(default_factory=time.time)

    @property
    def connected(self) -> bool:
        return bool(self.sids)

    def public(self) -> dict:
        return {"slot": self.slot, "label": self.label, "nickname": self.nickname, "connected": self.connected}


@dataclass
class RoomMessage:
    seq: int
    slot: int
    text: str
    ts: float
    params: dict | None = None
    diary: str = ""
    tip: str = ""
    topic: str = ""
    provider: str = ""
    degraded: bool = False
    meter: float = 0.0
    narration: dict | None = None
    counts: dict | None = None

    @property
    def analysed(self) -> bool:
        return self.params is not None

    def public(self, viewer: int | None = None) -> dict:
        """Serialise for a client. The fly's reading of a message (parameters, diary, tip,
        narration, model used) is private to its author, and the reply tip is private to the recipient: pass ``viewer`` to enforce that."""
        out = {
            "seq": self.seq, "slot": self.slot, "text": self.text, "ts": self.ts,
            "analysed": self.analysed, "params": self.params, "diary": self.diary, "tip": self.tip,
            "topic": self.topic, "provider": self.provider, "degraded": self.degraded,
            "meter": self.meter, "narration": self.narration, "counts": self.counts,
        }
        if viewer is not None:
            if viewer != self.slot:
                for key in AUTHOR_ONLY:
                    out[key] = None
                out["diary"] = ""
            else:
                for key in RECIPIENT_ONLY:
                    out[key] = None
        return out


AUTHOR_ONLY = ("params", "topic", "provider", "degraded", "narration")   # the fly's reading of *your* message
RECIPIENT_ONLY = ("tip",)                                                 # advice for whoever replies


class Room:
    def __init__(self, room_id: str, code: str | None, mode: str, status: str, created_at: float,
                 engine: RoomEngine, share_token: str, scale: str = "standard"):
        self.id = room_id
        self.scale = scale                     # "standard" (1,350 cells) or "full" (4,300 cells)
        self.code = code
        self.mode = mode
        self.status = status  # waiting | active | ended
        self.created_at = created_at
        self.engine = engine
        self.share_token = share_token
        self.participants: list[Participant] = []
        self.messages: list[RoomMessage] = []
        self.lock = threading.RLock()
        self.queue: deque = deque()
        self.worker_running = False
        self.last_activity = created_at
        self.owner_sids: set[str] = set()
        self.ended_by: int | None = None
        self.compacted = False

    def slot_of(self, client_id: str) -> int | None:
        for p in self.participants:
            if p.client_id == client_id:
                return p.slot
        return None

    def participant(self, slot: int) -> Participant | None:
        return next((p for p in self.participants if p.slot == slot), None)

    def counts(self) -> dict:
        out = {0: 0, 1: 0}
        for m in self.messages:
            out[m.slot] = out.get(m.slot, 0) + 1
        return out

    def verdict_ready(self, minimum: int) -> bool:
        counts = self.counts()
        return len(self.participants) == 2 and counts[0] >= minimum and counts[1] >= minimum

    def touch(self) -> None:
        self.last_activity = time.time()
        self.compacted = False

    def partner_of(self, slot: int) -> Participant | None:
        return self.participant(1 - slot)

    def public(self) -> dict:
        return {
            "id": self.id, "mode": self.mode, "status": self.status, "code": self.code, "scale": self.scale,
            "participants": [p.public() for p in self.participants],
            "counts": self.counts(),
        }


class RoomManager:
    def __init__(self, config, storage, connectome: Connectome, engine_cfg: EngineConfig):
        self.config = config
        self.storage = storage
        self.conn = connectome
        self.conn_for = lambda scale: connectome    # replaced by the app: scale -> Connectome
        self.cfg_for = lambda scale: engine_cfg
        self.engine_cfg = engine_cfg
        self._rooms: dict[str, Room] = {}
        self._codes: dict[str, str] = {}
        self._lock = threading.RLock()

    # -- creation ----------------------------------------------------------------------
    def _new_engine(self, scale: str = "standard") -> RoomEngine:
        return RoomEngine(self.conn_for(scale), self.cfg_for(scale))

    def full_rooms(self, exclude: str | None = None) -> int:
        with self._lock:
            return sum(1 for r in self._rooms.values() if r.scale == "full" and r.status != "ended" and r.id != exclude)

    def set_scale(self, room: Room, scale: str) -> None:
        """Switch a room between the standard and full-pathway connectome by replaying its messages."""
        with room.lock:
            if room.scale == scale:
                return
            engine = self._new_engine(scale)
            conn = self.conn_for(scale)
            for msg in room.messages:
                if not msg.params:
                    continue
                result = engine.step(msg.params)
                msg.meter = result.trace.meter
                msg.narration = narrate(result.trace, conn)
                msg.counts = {"new_cells": result.trace.new_cells, "new_edges": result.trace.new_edges,
                              "cells_active": result.trace.cells_active, "edges_active": result.trace.edges_active}
            room.engine, room.scale, room.compacted = engine, scale, False
            room.touch()
        self.storage.set_scale(room.id, scale)

    @staticmethod
    def default_nick(label: str, slot: int) -> str:
        return label if label != "Friend" else f"Friend {slot + 1}"

    def create_waiting(self, mode: str, client_id: str, nickname: str, label: str) -> Room:
        """A private room with an invite code, waiting for a friend."""
        now = time.time()
        share = new_share_token()
        for _ in range(8):
            code = new_invite_code()
            room_id = new_room_id()
            try:
                self.storage.create_room(room_id, code, mode, "waiting", share, now)
                break
            except sqlite3.IntegrityError:
                continue
        else:  # pragma: no cover - 1e9 code space
            raise RuntimeError("could not allocate an invite code")
        room = Room(room_id, code, mode, "waiting", now, self._new_engine(), share)
        room.participants.append(Participant(0, client_id, label, nickname or self.default_nick(label, 0)))
        self.storage.add_participant(room_id, 0, client_id, label, room.participants[0].nickname)
        with self._lock:
            self._rooms[room_id] = room
            self._codes[code] = room_id
        return room

    def create_matched(self, mode: str, first: tuple, second: tuple) -> Room:
        """first/second are (client_id, nickname, label); both join an already-active room."""
        now = time.time()
        room_id, share = new_room_id(), new_share_token()
        self.storage.create_room(room_id, None, mode, "active", share, now)
        self.storage.set_status(room_id, "active", now)
        room = Room(room_id, None, mode, "active", now, self._new_engine(), share)
        for slot, (client_id, nickname, label) in enumerate((first, second)):
            p = Participant(slot, client_id, label, nickname or self.default_nick(label, slot))
            room.participants.append(p)
            self.storage.add_participant(room_id, slot, client_id, label, p.nickname)
        with self._lock:
            self._rooms[room_id] = room
        return room

    def peek_code(self, code: str) -> Room | None:
        with self._lock:
            room_id = self._codes.get(code)
            return self._rooms.get(room_id) if room_id else None

    def join_by_code(self, code: str, client_id: str, nickname: str) -> tuple[Room | None, str]:
        with self._lock:
            room_id = self._codes.get(code)
            room = self._rooms.get(room_id) if room_id else None
        if room is None:
            return None, "not_found"
        with room.lock:
            if room.status != "waiting":
                return None, "full"
            if time.time() - room.created_at > self.config.INVITE_TTL_S:
                return None, "expired"
            if room.participants and room.participants[0].client_id == client_id:
                return None, "own"
            label = PARTNER_LABEL.get(room.participants[0].label, "Friend")
            p = Participant(1, client_id, label, nickname or self.default_nick(label, 1))
            room.participants.append(p)
            room.status = "active"
            room.code = None
            room.touch()
            self.storage.add_participant(room.id, 1, client_id, label, p.nickname)
            self.storage.set_status(room.id, "active", time.time())
            self.storage.clear_code(room.id)
        with self._lock:
            self._codes.pop(code, None)
        return room, ""

    # -- lookup ------------------------------------------------------------------------
    def get(self, room_id: str | None) -> Room | None:
        if not room_id:
            return None
        with self._lock:
            room = self._rooms.get(room_id)
        if room is not None:
            return room
        return self._restore(room_id)

    def waiting_invite_for(self, client_id: str) -> Room | None:
        """The invite room this client created and is still waiting in (single source of truth)."""
        with self._lock:
            rooms = list(self._rooms.values())
        for room in rooms:
            if room.status == "waiting" and room.participants and room.participants[0].client_id == client_id:
                return room
        return None

    def by_share_token(self, token: str) -> Room | None:
        room_id = self.storage.room_id_by_share_token(token)
        return self.get(room_id) if room_id else None

    def for_client(self, client_id: str) -> list[Room]:
        with self._lock:
            rooms = list(self._rooms.values())
        found = [r for r in rooms if r.status in ("active", "waiting") and r.slot_of(client_id) is not None]
        known = {r.id for r in found}
        for row in self.storage.rooms_for_client(client_id):
            if row["id"] not in known:
                room = self.get(row["id"])
                if room is not None and room.status in ("active", "waiting"):
                    found.append(room)
        return sorted(found, key=lambda r: r.created_at, reverse=True)

    def discard(self, room: Room) -> None:
        with self._lock:
            self._rooms.pop(room.id, None)
            if room.code:
                self._codes.pop(room.code, None)
        self.storage.delete_room(room.id)

    def end(self, room: Room, slot: int) -> None:
        with room.lock:
            room.status = "ended"
            room.ended_by = slot
            room.touch()
            code, room.code = room.code, None
        if code:                       # an invite code dies with its chat
            with self._lock:
                self._codes.pop(code, None)
            self.storage.clear_code(room.id)
        self.storage.set_status(room.id, "ended", time.time())

    # -- restore -----------------------------------------------------------------------
    def _restore(self, room_id: str) -> Room | None:
        data = self.storage.load_room(room_id)
        if data is None:
            return None
        row = data["room"]
        if row["status"] == "waiting" and time.time() - row["created_at"] > self.config.INVITE_TTL_S:
            return None
        scale = row.get("scale") or "standard"
        if self.conn_for(scale) is self.conn:
            scale = "standard"                                   # full connectome not available any more
        room = Room(row["id"], row["code"], row["mode"], row["status"], row["created_at"], self._new_engine(scale),
                    row["share_token"], scale)
        for p in data["participants"]:
            room.participants.append(Participant(p["slot"], p["client_id"], p["label"], p["nickname"]))
        for m in data["messages"]:
            msg = RoomMessage(seq=m["seq"], slot=m["slot"], text=m["text"], ts=m["ts"], diary=m["diary"] or "",
                              tip=m["tip"] or "", topic=m["topic"] or "", provider=m["provider"] or "",
                              degraded=bool(m["degraded"]))
            if m["params"]:
                try:
                    msg.params = json.loads(m["params"])
                    result = room.engine.step(msg.params)
                    msg.meter = result.trace.meter
                    msg.narration = narrate(result.trace, room.engine.conn)
                    msg.counts = {"new_cells": result.trace.new_cells, "new_edges": result.trace.new_edges,
                                  "cells_active": result.trace.cells_active, "edges_active": result.trace.edges_active}
                except (ValueError, TypeError):
                    log.warning("could not replay message %s/%s", room_id, m["seq"])
            room.messages.append(msg)
        room.engine.compact()
        room.compacted = True
        room.last_activity = time.time()
        with self._lock:
            existing = self._rooms.get(room_id)
            if existing is not None:
                return existing
            self._rooms[room_id] = room
            if room.code and room.status == "waiting":
                self._codes[room.code] = room.id
        return room

    # -- housekeeping ------------------------------------------------------------------
    def janitor(self, now: float | None = None) -> dict:
        now = now or time.time()
        cfg = self.config
        removed_waiting, dropped, compacted = 0, 0, 0
        with self._lock:
            rooms = list(self._rooms.values())
        for room in rooms:
            if room.status == "waiting" and now - room.created_at > cfg.INVITE_TTL_S:
                self.discard(room)
                removed_waiting += 1
                continue
            idle = now - room.last_activity
            if idle > cfg.ROOM_IDLE_TTL_S and not any(p.connected for p in room.participants):
                with self._lock:
                    self._rooms.pop(room.id, None)
                dropped += 1
            elif idle > 300 and not room.compacted:
                with room.lock:
                    room.engine.compact()
                    room.compacted = True
                compacted += 1
        return {"removed_waiting": removed_waiting, "dropped": dropped, "compacted": compacted}

    def purge_old(self, now: float | None = None) -> int:
        cutoff = (now or time.time()) - self.config.RETENTION_DAYS * 86400
        return self.storage.purge_before(cutoff)

    def stats(self) -> dict:
        with self._lock:
            rooms = list(self._rooms.values())
        now = time.time()
        return {
            "active": sum(1 for r in rooms if r.status == "active" and now - r.last_activity < 900),
            "waiting_invites": sum(1 for r in rooms if r.status == "waiting"),
            "in_memory": len(rooms),
        }
