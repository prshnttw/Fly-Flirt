"""The service container shared by HTTP routes, socket handlers and background loops."""
from __future__ import annotations

import logging
import os
import threading
import time
import dataclasses
from dataclasses import dataclass, field

from .connectome import Connectome, load_connectome
from .engine import EngineConfig
from .llm import LLMRouter, build_router
from .matchmaking import Matchmaker
from .moderation import RepeatGuard
from .rooms import RoomManager
from .safety import Safety
from .storage import Storage
from .util import TokenBucket

log = logging.getLogger("flyflirt")


@dataclass
class Services:
    config: type
    connectome: Connectome
    engine_cfg: EngineConfig
    storage: Storage
    router: LLMRouter
    rooms: RoomManager
    matchmaker: Matchmaker
    msg_bucket: TokenBucket
    join_bucket: TokenBucket
    code_bucket: TokenBucket
    report_bucket: TokenBucket
    safety: Safety = None
    repeat_guard: RepeatGuard = field(default_factory=RepeatGuard)
    online: set = field(default_factory=set)
    sid_index: dict = field(default_factory=dict)  # sid -> (room_id, slot)
    started_at: float = field(default_factory=time.time)
    lock: threading.RLock = field(default_factory=threading.RLock)
    _full: Connectome | None = None

    # -- standard vs "full pathway" connectome ---------------------------------------------
    @property
    def full_available(self) -> bool:
        return bool(self.config.FULL_ENABLED and os.path.exists(self.config.CONNECTOME_FULL_PATH))

    def connectome_for(self, scale: str) -> Connectome:
        if scale != "full" or not self.full_available:
            return self.connectome
        with self.lock:                       # ~75 MB dense matrix: load once, on first use
            if self._full is None:
                self._full = load_connectome(self.config.CONNECTOME_FULL_PATH)
                log.info("full-pathway connectome loaded: %s", self._full.summary())
            return self._full

    def engine_cfg_for(self, scale: str) -> EngineConfig:
        if scale == "full" and self.full_available:
            return dataclasses.replace(self.engine_cfg, meter_scale=self.config.SIM_METER_SCALE_FULL)
        return self.engine_cfg

    def stats(self) -> dict:
        room_stats = self.rooms.stats()
        waiting = self.matchmaker.counts()
        return {
            "online": len(self.online),
            "waiting": waiting,
            "waiting_total": sum(waiting.values()),
            "chats_active": room_stats["active"],
            "chats_today": self.storage.rooms_started_since(time.time() - 86400),
            "neurons": self.connectome.n,
            "synapses": self.connectome.n_edges,
        }


def build_services(config) -> Services:
    db_path = getattr(config, "DB_PATH", None) or os.path.join(config.DATA_DIR, "flyflirt.db")
    storage = Storage(db_path)
    connectome = load_connectome(config.CONNECTOME_PATH)
    engine_cfg = EngineConfig.from_config(config)
    router = build_router(config, usage_store=storage)
    rooms = RoomManager(config, storage, connectome, engine_cfg)
    log.info("connectome: %s | LLM chain: %s", connectome.summary(), [s.provider.id for s in router.states] or "local only")
    safety = Safety(storage)
    matchmaker = Matchmaker(config.MATCH_TIMEOUT_S)
    matchmaker.is_blocked = safety.blocked_between
    svc = Services(
        config=config,
        connectome=connectome,
        engine_cfg=engine_cfg,
        storage=storage,
        router=router,
        rooms=rooms,
        matchmaker=matchmaker,
        msg_bucket=TokenBucket(config.MSG_BURST, config.MSG_REFILL_PER_S),
        join_bucket=TokenBucket(config.JOIN_BURST, config.JOIN_REFILL_PER_S),
        code_bucket=TokenBucket(config.CODE_GUESS_BURST, config.CODE_GUESS_REFILL_PER_S),
        report_bucket=TokenBucket(3, 1 / 300),   # 3 reports, then one every 5 minutes
        safety=safety,
    )
    rooms.conn_for, rooms.cfg_for = svc.connectome_for, svc.engine_cfg_for
    return svc
