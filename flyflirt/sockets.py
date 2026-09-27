"""Socket.IO events: matchmaking, invites, presence and the live analysis pipeline.

Message flow
------------
1. ``message`` arrives -> validated, rate-limited, appended to the room and broadcast
   immediately (chat stays instant no matter how slow analysis is).
2. It joins the room's queue. One worker per room drains the queue *in order*, so the
   simulation always sees messages in the order they were sent even when LLM latency
   differs between them.
3. Worker: LLM analysis (with fallback chain) -> engine step -> persist -> broadcast
   ``fly_update`` (parameters, meter, narration) and ``brain_wave`` (the neurons and
   synapses this message lit, frame by frame).
"""
from __future__ import annotations

import logging
import time

from flask import request, session
from flask_socketio import emit, join_room

from .matchmaking import PAIRS, SEEKING, Ticket
from .moderation import check_message
from .narrate import narrate
from .rooms import LABELS, RoomMessage
from .util import clean_nickname, clean_text, normalise_code

log = logging.getLogger("flyflirt.sockets")


def register_socket_handlers(socketio, svc) -> None:
    cfg = svc.config

    # -- helpers -----------------------------------------------------------------------
    def cid() -> str | None:
        return session.get("cid")

    def ip() -> str:
        return request.remote_addr or "?"

    def fail(code: str, message: str) -> None:
        emit("error_message", {"code": code, "message": message})

    def ban_notice(client) -> bool:
        """Tell a banned client why they can't chat. Returns True if they are banned."""
        left = svc.safety.banned_for(client) if client else 0
        if not left:
            return False
        minutes = max(1, round(left / 60))
        fail("banned", f"You are temporarily blocked from chatting (about {minutes} min left) because of reports "
                       "or messages that broke the community rules.")
        return True

    def presence(room) -> dict:
        return {"participants": [p.public() for p in room.participants], "status": room.status}

    def match_payload(room, slot: int) -> dict:
        me, other = room.participant(slot), room.participant(1 - slot)
        return {
            "room_id": room.id,
            "mode": room.mode,
            "you": me.public(),
            "partner": {"label": other.label, "nickname": other.nickname},
        }

    def invite_payload(room) -> dict:
        remaining = max(0, int(cfg.INVITE_TTL_S - (time.time() - room.created_at)))
        return {"code": room.code, "path": f"/join/{room.code}", "expires_in": remaining, "room_id": room.id}

    def discard_invite(client_id: str, keep_room_id: str | None = None) -> None:
        room = svc.rooms.waiting_invite_for(client_id)
        if room is not None and room.id != keep_room_id:
            svc.rooms.discard(room)

    def start_match(newcomer: Ticket, waiting: Ticket) -> None:
        for t in (newcomer, waiting):
            discard_invite(t.client_id)
        mode = "flirt" if (waiting.is_flirt and newcomer.is_flirt) else "friends"
        room = svc.rooms.create_matched(
            mode,
            (waiting.client_id, waiting.nickname, waiting.label),
            (newcomer.client_id, newcomer.nickname, newcomer.label),
        )
        socketio.emit("matched", match_payload(room, 0), to=waiting.sid)
        socketio.emit("matched", match_payload(room, 1), to=newcomer.sid)
        log.info("matched %s <-> %s in room %s (%s)", waiting.mode, newcomer.mode, room.id, mode)

    def ensure_invite(client: str, mode: str, nick: str, sid: str):
        room = svc.rooms.waiting_invite_for(client)
        if room is None:
            room = svc.rooms.create_waiting("friends", client, nick, LABELS["friends"])
        room.owner_sids.add(sid)
        return room

    # -- connection lifecycle ----------------------------------------------------------
    @socketio.on("connect")
    def on_connect(auth=None):
        svc.online.add(request.sid)

    @socketio.on("disconnect")
    def on_disconnect(*_args):
        sid = request.sid
        svc.online.discard(sid)
        svc.matchmaker.drop_sid(sid)
        info = svc.sid_index.pop(sid, None)
        if info:
            room = svc.rooms.get(info[0])
            participant = room.participant(info[1]) if room else None
            if participant is not None:
                participant.sids.discard(sid)
                participant.last_seen = time.time()
                socketio.emit("presence", presence(room), to=room.id)

    # -- matchmaking -------------------------------------------------------------------
    @socketio.on("queue_join")
    def on_queue_join(data=None):
        client = cid()
        data = data if isinstance(data, dict) else {}
        mode = data.get("mode")
        if not client:
            return fail("no_session", "Please reload the page.")
        if mode not in PAIRS:
            return fail("bad_mode", "Choose male, female or friends.")
        if not svc.join_bucket.allow(ip()):
            return fail("rate_limited", "Too many attempts. Please wait a moment.")
        if ban_notice(client):
            return
        nickname = clean_nickname(data.get("nickname"))
        if not nickname:
            return fail("nickname_required", "Please enter a nickname.")
        seeking = data.get("seeking") if data.get("seeking") in SEEKING else ""
        ticket = Ticket(client, request.sid, nickname, mode, seeking=seeking)
        partner = svc.matchmaker.enqueue(ticket)
        if partner is not None:
            start_match(ticket, partner)
            return
        emit("queue_waiting", {
            "mode": mode, "seeking": ticket.seeking, "timeout": cfg.MATCH_TIMEOUT_S, "position": svc.matchmaker.position(client),
            "counts": svc.matchmaker.counts(), "elapsed": int(time.time() - svc.matchmaker.get(client).joined_at),
        })

    @socketio.on("queue_cancel")
    def on_queue_cancel(data=None):
        client = cid()
        if not client:
            return
        svc.matchmaker.cancel(client)
        if not (isinstance(data, dict) and data.get("keep_invite")):
            discard_invite(client)
        emit("queue_cancelled", {})

    @socketio.on("invite_create")
    def on_invite_create(data=None):
        client = cid()
        data = data if isinstance(data, dict) else {}
        if not client:
            return fail("no_session", "Please reload the page.")
        if not svc.join_bucket.allow(ip()):
            return fail("rate_limited", "Too many attempts. Please wait a moment.")
        if ban_notice(client):
            return
        nickname = clean_nickname(data.get("nickname"))
        if not nickname:
            return fail("nickname_required", "Please enter a nickname.")
        mode = data.get("mode") if data.get("mode") in PAIRS else "friends"
        room = ensure_invite(client, mode, nickname, request.sid)
        emit("invite_created", invite_payload(room))

    @socketio.on("invite_join")
    def on_invite_join(data=None):
        client = cid()
        data = data if isinstance(data, dict) else {}
        if not client:
            return fail("no_session", "Please reload the page.")
        code = normalise_code(data.get("code"))
        if code is None:
            return emit("invite_error", {"reason": "invalid"})
        if not svc.code_bucket.allow(ip()):
            return emit("invite_error", {"reason": "rate_limited"})
        if ban_notice(client):
            return
        target = svc.rooms.peek_code(code)
        if target is not None and target.participants and svc.safety.blocked_between(target.participants[0].client_id, client):
            return emit("invite_error", {"reason": "not_found"})      # a blocked pair just sees "expired"
        nickname = clean_nickname(data.get("nickname"))
        if not nickname:
            return fail("nickname_required", "Please enter a nickname.")
        room, error = svc.rooms.join_by_code(code, client, nickname)
        if room is None:
            return emit("invite_error", {"reason": error})
        owner = room.participants[0]
        for person in (owner.client_id, client):
            svc.matchmaker.cancel(person)
            discard_invite(person, keep_room_id=room.id)
        emit("matched", match_payload(room, 1))
        for sid in list(room.owner_sids):
            socketio.emit("matched", match_payload(room, 0), to=sid)
        log.info("invite joined room %s", room.id)

    # -- rooms -------------------------------------------------------------------------
    def load_member(room_id):
        client = cid()
        room = svc.rooms.get(room_id) if isinstance(room_id, str) else None
        if room is None:
            fail("not_found", "This chat does not exist or has expired.")
            return None, None
        slot = room.slot_of(client or "")
        if slot is None:
            fail("forbidden", "You are not part of this chat.")
            return None, None
        return room, slot

    @socketio.on("room_join")
    def on_room_join(data=None):
        data = data if isinstance(data, dict) else {}
        room, slot = load_member(data.get("room_id"))
        if room is None:
            return
        participant = room.participant(slot)
        participant.sids.add(request.sid)
        participant.last_seen = time.time()
        svc.sid_index[request.sid] = (room.id, slot)
        join_room(room.id)
        room.touch()
        with room.lock:
            state_idx, state_val = room.engine.state_snapshot()
            messages = [m.public(viewer=slot) for m in room.messages[-200:]]
            counts = room.counts()
        emit("room_state", {
            "room": room.public(),
            "you": slot,
            "messages": messages,
            "meter": round(room.engine.meter(), 4),
            "state": {"idx": state_idx, "val": state_val},
            "totals": {
                "cells_active": int((room.engine.peak_abs >= svc.engine_cfg.act_thr).sum()),
                "edges_active": int((room.engine.peak_flux >= svc.engine_cfg.edge_thr).sum()),
                "cells_total": room.engine.conn.n, "edges_total": room.engine.conn.n_edges,
            },
            "message_counts": {"0": counts[0], "1": counts[1]},
            "verdict_ready": room.verdict_ready(cfg.MIN_MESSAGES_FOR_VERDICT),
            "min_for_verdict": cfg.MIN_MESSAGES_FOR_VERDICT,
            "max_len": cfg.MAX_MESSAGE_LEN,
            "retain_text_default": svc.retain_text_default,
        })
        socketio.emit("presence", presence(room), to=room.id)

    @socketio.on("typing")
    def on_typing(data=None):
        data = data if isinstance(data, dict) else {}
        room, slot = load_member(data.get("room_id"))
        if room is None:
            return
        emit("typing", {"slot": slot, "on": bool(data.get("on"))}, to=room.id, include_self=False)

    @socketio.on("seen")
    def on_seen(data=None):
        """Delivery receipts: purely in-memory, never written to disk, gone the moment the process
        restarts or the room ends — that's the point, it's a live-chat nicety, not a record."""
        data = data if isinstance(data, dict) else {}
        room, slot = load_member(data.get("room_id"))
        if room is None:
            return
        try:
            seq = int(data.get("seq"))
        except (TypeError, ValueError):
            return
        if seq < 0 or seq >= len(room.messages):
            return
        if room.seen.get(slot, -1) >= seq:
            return   # already at least this far; nothing changed
        room.seen[slot] = seq
        socketio.emit("seen", {"slot": slot, "seq": seq}, to=room.id, include_self=False)

    @socketio.on("leave_room")
    def on_leave_room(data=None):
        data = data if isinstance(data, dict) else {}
        room, slot = load_member(data.get("room_id"))
        if room is None:
            return
        if room.status != "ended":
            if data.get("skip") and len(room.participants) == 2:
                svc.safety.skip(room.participant(0).client_id, room.participant(1).client_id)
            svc.rooms.end(room, slot)
            socketio.emit("room_ended", {"by": slot, "status": "ended"}, to=room.id)
        for sid in list(room.participant(slot).sids):
            svc.sid_index.pop(sid, None)
        room.participant(slot).sids.clear()
        socketio.emit("presence", presence(room), to=room.id)

    @socketio.on("block_user")
    def on_block_user(data=None):
        """Block the other person: end this chat, never pair the two of you again, and log a report."""
        data = data if isinstance(data, dict) else {}
        room, slot = load_member(data.get("room_id"))
        if room is None or len(room.participants) < 2:
            return
        me, other = room.participant(slot), room.participant(1 - slot)
        svc.safety.block(me.client_id, other.client_id)
        svc.safety.report(room.id, me.client_id, other.client_id, "other", "blocked by partner")
        if room.status != "ended":
            svc.rooms.end(room, slot)
            socketio.emit("room_ended", {"by": slot, "status": "ended"}, to=room.id)
        emit("blocked", {"room_id": room.id})
        for sid in list(me.sids):
            svc.sid_index.pop(sid, None)
        me.sids.clear()
        socketio.emit("presence", presence(room), to=room.id)

    @socketio.on("set_scale")
    def on_set_scale(data=None):
        """Either person can switch the room between the standard and the bigger full-pathway brain."""
        data = data if isinstance(data, dict) else {}
        room, slot = load_member(data.get("room_id"))
        if room is None:
            return
        scale = data.get("scale")
        if scale not in ("standard", "full"):
            return fail("bad_scale", "Unknown brain size.")
        if scale == room.scale:
            return emit("scale_changed", {"scale": scale})
        if not svc.join_bucket.allow(ip()):
            return fail("rate_limited", "Too many changes. Please wait a moment.")
        if scale == "full":
            if not svc.full_available:
                return fail("no_full", "The full-pathway brain is not available on this server.")
            if svc.rooms.full_rooms(exclude=room.id) >= cfg.FULL_MAX_ROOMS:
                return fail("full_busy", "The full-pathway brain is busy right now. Please try again in a few minutes.")

        def switch():
            svc.rooms.set_scale(room, scale)
            socketio.emit("scale_changed", {"scale": scale}, to=room.id)
        emit("scale_working", {"scale": scale})
        socketio.start_background_task(switch)

    @socketio.on("set_privacy")
    def on_set_privacy(data=None):
        """Either person can choose whether *this* chat's messages get written to the server's disk,
        overriding the server-wide default for this room only. Never retroactive: it only changes what
        happens to messages sent after the change."""
        data = data if isinstance(data, dict) else {}
        room, slot = load_member(data.get("room_id"))
        if room is None:
            return
        want = data.get("store")   # true = save, false = don't save, null = go back to the server default
        if want is not None and not isinstance(want, bool):
            return fail("bad_privacy", "Unknown privacy setting.")
        room.store_override = want
        svc.storage.set_store_override(room.id, want)
        effective = svc.retain_text_default if want is None else want
        socketio.emit("privacy_changed", {"store_override": want, "effective": effective}, to=room.id)

    @socketio.on("lab_subscribe")
    def on_lab_subscribe(data=None):
        join_room("lab")
        emit("lab_stats", svc.stats())

    # -- messages ----------------------------------------------------------------------
    @socketio.on("message")
    def on_message(data=None):
        data = data if isinstance(data, dict) else {}
        room, slot = load_member(data.get("room_id"))
        if room is None:
            return
        if room.status != "active" or len(room.participants) < 2:
            return fail("not_active", "This chat is not active.")
        text = clean_text(data.get("text"), cfg.MAX_MESSAGE_LEN)
        if not text:
            return
        if not svc.msg_bucket.allow(cid()):
            return fail("slow_down", "You are sending messages too fast.")
        if ban_notice(cid()):
            return
        verdict = check_message(text)
        if verdict.action in ("block", "spam") or svc.repeat_guard.repeated(cid(), text):
            if verdict.action == "block":
                strikes, banned = svc.safety.strike(cid())
                log.info("blocked message (%s) strike %s in room %s", verdict.reason, strikes, room.id)
                if banned:
                    svc.rooms.end(room, slot)
                    socketio.emit("room_ended", {"by": slot, "status": "ended", "reason": "conduct"}, to=room.id)
                    return fail("banned", "This chat was ended and you are blocked for a while because of repeated rule-breaking messages.")
                return emit("message_blocked", {"reason": verdict.reason, "message": verdict.message,
                                               "strikes_left": svc.safety.strike_limit - strikes})
            return emit("message_blocked", {"reason": verdict.reason or "spam",
                                           "message": verdict.message or "Please don't repeat the same message.",
                                           "strikes_left": None})
        text = verdict.text
        if verdict.action == "mask":
            emit("message_masked", {"reason": verdict.reason})
        with room.lock:
            if len(room.messages) >= cfg.MAX_MESSAGES_PER_ROOM:
                return fail("room_full", "This chat reached its message limit. Open the verdict!")
            msg = RoomMessage(seq=len(room.messages), slot=slot, text=text, ts=time.time())
            room.messages.append(msg)
            room.queue.append(msg)
            room.touch()
            start_worker = not room.worker_running
            if start_worker:
                room.worker_running = True
            counts = room.counts()
        socketio.emit("new_message", {"seq": msg.seq, "slot": slot, "text": text, "ts": msg.ts,
                                      "message_counts": {"0": counts[0], "1": counts[1]}}, to=room.id)
        socketio.emit("analysing", {"seq": msg.seq}, to=room.id)
        if start_worker:
            socketio.start_background_task(process_queue, room)

    def process_queue(room) -> None:
        while True:
            with room.lock:
                if not room.queue:
                    room.worker_running = False
                    return
                msg = room.queue.popleft()
            try:
                analyse_message(room, msg)
            except Exception:  # never let one bad message kill the room's worker
                log.exception("analysis failed for room %s seq %s", room.id, msg.seq)
                socketio.emit("analysis_failed", {"seq": msg.seq}, to=room.id)

    def analyse_message(room, msg: RoomMessage) -> None:
        prev = next((m.text for m in reversed(room.messages[: msg.seq]) if m.slot != msg.slot), "")
        analysis = svc.router.analyse(msg.text, prev, room.mode)
        with room.lock:
            step = room.engine.step(analysis.params)
            trace = step.trace
            msg.params = analysis.params
            msg.diary, msg.tip, msg.topic = analysis.diary, analysis.tip, analysis.topic
            msg.provider, msg.degraded = analysis.provider, analysis.degraded
            msg.meter = trace.meter
            msg.narration = narrate(trace, room.engine.conn)
            msg.counts = {
                "new_cells": trace.new_cells, "new_edges": trace.new_edges,
                "cells_active": trace.cells_active, "edges_active": trace.edges_active,
            }
            counts = room.counts()
            ready = room.verdict_ready(cfg.MIN_MESSAGES_FOR_VERDICT)
            room.touch()
            room.last_message_at = time.time()
            room.verdict_nudged_at = None   # a fresh message means the conversation isn't quiet any more
        svc.storage.add_message(room.id, msg, retain_text=room.store_override)

        shared = {
            "seq": msg.seq, "slot": msg.slot, "meter": trace.meter,
            "counts": {**msg.counts, "cells_total": room.engine.conn.n, "edges_total": room.engine.conn.n_edges},
            "message_counts": {"0": counts[0], "1": counts[1]},
            "verdict_ready": ready,
        }
        # The fly's reading of a message (parameters, diary, narration, model) goes to its author only;
        # the reply tip goes to the *other* person, since it is advice on how to answer.
        private = {
            "params": msg.params, "channels": trace.channels, "topic": msg.topic, "diary": msg.diary,
            "narration": msg.narration, "llm": analysis.public(),
        }
        author = room.participants[msg.slot] if msg.slot < len(room.participants) else None
        author_sids = set(author.sids) if author else set()
        for sid in author_sids:
            socketio.emit("fly_update", {**shared, **private, "private": True}, to=sid)
        for other in room.participants:
            if other is author:
                continue
            for sid in set(other.sids):
                socketio.emit("fly_update", {**shared, "private": False, "tip": msg.tip}, to=sid)
        socketio.emit("brain_wave", {
            "seq": msg.seq, "frames": step.frames,
            "state": {"idx": step.state_idx, "val": step.state_val},
        }, to=room.id)
        # Anonymous live-lab pulse: neuron indices only, never text or authorship. The lab draws the
        # standard connectome, so full-pathway rooms (different indices) don't feed it.
        if room.scale == "standard":
            frames = [{"cells": f["cells"][:80], "acts": f["acts"][:80]} for f in step.frames[:5]]
            socketio.emit("lab_pulse", {"frames": frames}, to="lab")


def start_background_loops(socketio, svc) -> None:
    """Matchmaker timeouts / status ticks and periodic housekeeping."""
    cfg = svc.config

    def matchmaker_loop() -> None:
        tick = 0
        while True:
            socketio.sleep(1.0)
            tick += 1
            try:
                now = time.time()
                for ticket in svc.matchmaker.due_for_invite(now):
                    room = svc.rooms.waiting_invite_for(ticket.client_id)
                    if room is None:
                        room = svc.rooms.create_waiting(
                            "flirt" if ticket.is_flirt else "friends",
                            ticket.client_id, ticket.nickname, ticket.label,
                        )
                    room.owner_sids.add(ticket.sid)
                    remaining = max(0, int(cfg.INVITE_TTL_S - (now - room.created_at)))
                    socketio.emit("invite_suggested", {
                        "code": room.code, "path": f"/join/{room.code}", "expires_in": remaining, "room_id": room.id,
                    }, to=ticket.sid)
                if tick % 2 == 0:
                    counts = svc.matchmaker.counts()
                    for ticket in svc.matchmaker.snapshot():
                        socketio.emit("queue_status", {
                            "elapsed": int(now - ticket.joined_at), "timeout": cfg.MATCH_TIMEOUT_S,
                            "position": svc.matchmaker.position(ticket.client_id), "counts": counts,
                            "online": len(svc.online),
                        }, to=ticket.sid)
            except Exception:
                log.exception("matchmaker loop error")

    def janitor_loop() -> None:
        last_purge = 0.0
        while True:
            socketio.sleep(30.0)
            try:
                svc.rooms.janitor()
                if time.time() - last_purge > 3600:
                    removed = svc.rooms.purge_old()
                    last_purge = time.time()
                    if removed:
                        log.info("retention: purged %s old rooms", removed)
            except Exception:
                log.exception("janitor error")

    def idle_nudge_loop() -> None:
        """A conversation that's gone quiet gets a gentle "see your verdict?" prompt instead of just
        hanging there — never auto-ends or auto-redirects the chat, just offers the link."""
        while True:
            socketio.sleep(min(30.0, max(5.0, cfg.IDLE_VERDICT_NUDGE_S / 4)))
            try:
                now = time.time()
                for room in svc.rooms.rooms_gone_quiet(cfg.IDLE_VERDICT_NUDGE_S, cfg.MIN_MESSAGES_FOR_VERDICT, now):
                    with room.lock:
                        if room.status != "active" or room.verdict_nudged_at is not None:
                            continue   # settled elsewhere between the scan and the lock
                        room.verdict_nudged_at = now
                    socketio.emit("verdict_nudge", {"idle_s": int(now - room.last_message_at)}, to=room.id)
            except Exception:
                log.exception("idle nudge loop error")

    socketio.start_background_task(matchmaker_loop)
    socketio.start_background_task(janitor_loop)
    socketio.start_background_task(idle_nudge_loop)
