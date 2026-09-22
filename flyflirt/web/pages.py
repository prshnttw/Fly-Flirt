"""HTML pages. Data-heavy pages boot from a JSON blob and fetch the rest over the API."""
from __future__ import annotations

from flask import Blueprint, abort, current_app, jsonify, redirect, render_template, request, session, url_for

from ..matchmaking import PAIRS, SEEKING, default_seeking
from ..narrate import CHANNEL_LEGEND
from ..util import clean_nickname, new_client_id, normalise_code

pages_bp = Blueprint("pages", __name__)


def services():
    return current_app.extensions["flyflirt"]


@pages_bp.before_app_request
def ensure_client_id():
    if request.endpoint in (None, "static"):
        return
    if "cid" not in session:
        session["cid"] = new_client_id()
        session.permanent = True


def _room_or_404(room_id: str):
    room = services().rooms.get(room_id)
    if room is None:
        abort(404)
    slot = room.slot_of(session.get("cid", ""))
    if slot is None:
        abort(403)
    return room, slot


@pages_bp.get("/")
def landing():
    svc = services()
    resume = []
    for room in svc.rooms.for_client(session["cid"])[:3]:
        if room.status == "active" and len(room.participants) == 2:
            resume.append({"url": url_for("pages.chat", room_id=room.id), "mode": room.mode,
                           "messages": len(room.messages)})
    boot = {"stats": svc.stats(), "resume": resume, "modes": list(PAIRS)}
    return render_template("landing.html", boot=boot, resume=resume)


@pages_bp.get("/match")
def match():
    mode = request.args.get("mode", "")
    if mode not in PAIRS:
        return redirect(url_for("pages.landing"))
    nick = clean_nickname(request.args.get("nick", ""))
    seeking = request.args.get("seeking", "")
    if seeking not in SEEKING:
        seeking = default_seeking(mode)
    boot = {"mode": mode, "seeking": seeking, "nick": nick, "timeout": services().config.MATCH_TIMEOUT_S}
    return render_template("match.html", boot=boot, mode=mode)


@pages_bp.get("/invite")
def invite():
    """'Invite a buddy': create an invite code, or enter one you were given."""
    nick = clean_nickname(request.args.get("nick", ""))
    ttl_min = round(services().config.INVITE_TTL_S / 60)
    return render_template("invite.html", boot={"nick": nick}, ttl_min=ttl_min)


@pages_bp.get("/join")
def join_form():
    """No-JavaScript / slow-load fallback for the landing page's invite form."""
    normal = normalise_code(request.args.get("code", ""))
    if normal is None:
        return redirect(url_for("pages.landing"))
    return redirect(url_for("pages.join", code=normal, go=1))


@pages_bp.get("/join/<code>")
def join(code: str):
    normal = normalise_code(code)
    nick = clean_nickname(request.args.get("nick", ""))
    boot = {"code": normal, "nick": nick}
    return render_template("join.html", boot=boot, code=normal, raw=code[:12])


@pages_bp.get("/chat/<room_id>")
def chat(room_id: str):
    svc = services()
    room, slot = _room_or_404(room_id)
    boot = {
        "room_id": room.id, "slot": slot, "mode": room.mode,
        "min_for_verdict": svc.config.MIN_MESSAGES_FOR_VERDICT,
        "max_len": svc.config.MAX_MESSAGE_LEN,
        "legend": CHANNEL_LEGEND,
    }
    boot.update(scale=room.scale, full_available=svc.full_available)
    return render_template("chat.html", boot=boot, mode=room.mode)


@pages_bp.get("/verdict/<room_id>")
def verdict(room_id: str):
    room, slot = _room_or_404(room_id)
    boot = {"room_id": room.id, "slot": slot, "mode": room.mode,
            "min_for_verdict": services().config.MIN_MESSAGES_FOR_VERDICT}
    boot["scale"] = room.scale
    return render_template("verdict.html", boot=boot, mode=room.mode)


@pages_bp.get("/card/<token>")
def card(token: str):
    room = services().rooms.by_share_token(token[:64])
    if room is None:
        abort(404)
    return render_template("card.html", boot={"token": token[:64]}, mode=room.mode)


@pages_bp.get("/lab")
def lab():
    return render_template("lab.html", boot={"stats": services().stats()})


@pages_bp.get("/how")
def how():
    svc = services()
    return render_template("how.html", legend=CHANNEL_LEGEND, summary=svc.connectome.summary(),
                           chain=[s.provider.id for s in svc.router.states])


@pages_bp.get("/healthz")
def healthz():
    svc = services()
    return jsonify(ok=True, uptime_s=int(__import__("time").time() - svc.started_at), cells=svc.connectome.n)
