"""JSON API."""
from __future__ import annotations

from flask import Blueprint, current_app, jsonify, request, session, url_for

from ..narrate import CHANNEL_LEGEND
from ..safety import REPORT_CATEGORIES
from ..util import clean_text
from ..verdict import build_verdict, public_card

api_bp = Blueprint("api", __name__)


def services():
    return current_app.extensions["flyflirt"]


def _client_ip() -> str:
    return request.remote_addr or "?"


def _err(status: int, code: str, message: str, **extra):
    return jsonify(error=code, message=message, **extra), status


def _participant_room(room_id: str):
    svc = services()
    room = svc.rooms.get(room_id)
    if room is None:
        return None, None, _err(404, "not_found", "That chat does not exist or has expired.")
    slot = room.slot_of(session.get("cid", ""))
    if slot is None:
        return None, None, _err(403, "forbidden", "You are not part of this chat.")
    return room, slot, None


@api_bp.get("/stats")
def stats():
    return jsonify(services().stats())


@api_bp.get("/llm/status")
def llm_status():
    return jsonify(services().router.status())


@api_bp.get("/connectome")
def connectome():
    svc = services()
    return jsonify(summary=svc.connectome.summary(), legend=CHANNEL_LEGEND, meta=svc.connectome.meta)


@api_bp.get("/rooms/<room_id>/verdict")
def verdict(room_id: str):
    svc = services()
    room, _slot, error = _participant_room(room_id)
    if error:
        return error
    minimum = svc.config.MIN_MESSAGES_FOR_VERDICT
    if not room.verdict_ready(minimum):
        counts = room.counts()
        return _err(409, "not_ready", f"Each person needs to send at least {minimum} messages first.",
                    counts={"0": counts[0], "1": counts[1]}, minimum=minimum)
    with room.lock:
        if room.queue or room.worker_running:
            return _err(409, "analysing", "The fly is still reading the latest messages. Try again in a moment.",
                        retry_after=2)
        result = build_verdict(room, room.engine.conn, room.engine.cfg)
    payload = dict(result)
    payload["share_url"] = url_for("pages.card", token=room.share_token, _external=True)
    payload["you"] = room.slot_of(session["cid"])
    return jsonify(payload)


@api_bp.get("/card/<token>")
def card(token: str):
    svc = services()
    room = svc.rooms.by_share_token(token[:64])
    if room is None:
        return _err(404, "not_found", "That card does not exist.")
    if not room.verdict_ready(svc.config.MIN_MESSAGES_FOR_VERDICT):
        return _err(404, "not_found", "That card does not exist.")
    with room.lock:
        result = build_verdict(room, room.engine.conn, room.engine.cfg)
        return jsonify({**public_card(result, room, room.engine.conn, room.engine.cfg), "scale": room.scale})


@api_bp.post("/rooms/<room_id>/report")
def report(room_id: str):
    svc = services()
    room, slot, error = _participant_room(room_id)
    if error:
        return error
    client = session["cid"]
    if len(room.participants) < 2:
        return _err(409, "nobody_to_report", "There is nobody else in this chat to report.")
    if not svc.report_bucket.allow(client):
        return _err(429, "slow_down", "You have reported a lot recently. Please wait a few minutes.")
    body = request.get_json(silent=True) or {}
    reason = clean_text(body.get("reason", ""), 300)
    category = body.get("category") if body.get("category") in REPORT_CATEGORIES else "other"
    other = room.participant(1 - slot).client_id
    result = svc.safety.report(room.id, client, other, category, reason)
    if body.get("block"):
        svc.safety.block(client, other)
    return jsonify(ok=True, updated=not result["new"], blocked=bool(body.get("block")))
