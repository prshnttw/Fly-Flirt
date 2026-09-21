import itertools
import json
import os
import sqlite3
import threading
import time
import uuid
from datetime import datetime, timezone

from dotenv import load_dotenv
from flask import Flask, flash, redirect, render_template, session, url_for
from flask_socketio import SocketIO, emit, join_room

import circuit_sim
import stats
from judge import judge_line
from llm import brainstorm_comment, score_message

load_dotenv()

app = Flask(__name__)
app.config["SECRET_KEY"] = os.environ.get("SECRET_KEY", "dev")
socketio = SocketIO(app)

CLAIMED_ROLES = {"male": None, "female": None}
ROOM = "fly-chat"

THRESHOLD = 0.75
MAX_MESSAGE_LENGTH = 500
MIN_SECONDS_BETWEEN_MESSAGES = 1.0
MIN_MESSAGES_FOR_VERDICT = 3
_flavor_cycle = itertools.cycle(["thinking", "suggesting"])
_message_lock = threading.Lock()
_last_message_at = {"male": 0.0, "female": 0.0}
MESSAGE_COUNTS = {"male": 0, "female": 0}

DB_PATH = os.path.join(os.path.dirname(__file__), "chat.db")
BEST_QUOTE_PATH = os.path.join(os.path.dirname(__file__), "best_quote.json")


def _load_best_quote():
    if os.path.exists(BEST_QUOTE_PATH):
        try:
            with open(BEST_QUOTE_PATH) as f:
                return json.load(f)
        except (json.JSONDecodeError, ValueError):
            pass
    return {"text": None, "avg_score": -1.0}


def _save_best_quote():
    with open(BEST_QUOTE_PATH, "w") as f:
        json.dump(BEST_QUOTE, f)


BEST_QUOTE = _load_best_quote()


def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS messages (
                id INTEGER PRIMARY KEY,
                sender TEXT,
                text TEXT,
                warmth REAL, humor REAL, reciprocity REAL,
                p1_activity REAL,
                ts TEXT
            )
            """
        )


def log_message(sender, text, warmth=None, humor=None, reciprocity=None, p1_activity=None):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO messages (sender, text, warmth, humor, reciprocity, p1_activity, ts)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (sender, text, warmth, humor, reciprocity, p1_activity, datetime.now(timezone.utc).isoformat()),
        )


init_db()


@app.route("/")
def landing():
    taken = {role: (holder is not None) for role, holder in CLAIMED_ROLES.items()}
    return render_template("landing.html", taken=taken)


@app.route("/choose/<role>")
def choose_role(role):
    if role not in CLAIMED_ROLES:
        return redirect(url_for("landing"))

    client_id = session.get("client_id")
    if not client_id:
        client_id = str(uuid.uuid4())
        session["client_id"] = client_id

    holder = CLAIMED_ROLES[role]
    if holder is not None and holder != client_id:
        flash(f"{role.capitalize()} is taken — ask your partner to pick the other one.")
        return redirect(url_for("landing"))

    # Deliberately NOT auto-resetting the simulation here just because both
    # roles happened to be free — over a real network, brief drops (wifi
    # blips, phones locking) can momentarily free both seats even mid-
    # conversation. Auto-resetting on that was wiping real progress. Starting
    # fresh is now an explicit action via /reset-demo instead.
    CLAIMED_ROLES[role] = client_id
    session["role"] = role
    return redirect(url_for("chat"))


@app.route("/reset-demo")
def reset_demo():
    CLAIMED_ROLES["male"] = None
    CLAIMED_ROLES["female"] = None
    circuit_sim.reset()
    stats.reset()
    BEST_QUOTE["text"] = None
    BEST_QUOTE["avg_score"] = -1.0
    MESSAGE_COUNTS["male"] = 0
    MESSAGE_COUNTS["female"] = 0
    _save_best_quote()
    session.clear()
    flash("Demo reset — both seats are open for a new pair.")
    return redirect(url_for("landing"))


@app.route("/chat")
def chat():
    role = session.get("role")
    if role not in CLAIMED_ROLES:
        return redirect(url_for("landing"))
    return render_template("chat.html", role=role, min_messages_for_verdict=MIN_MESSAGES_FOR_VERDICT)


@app.route("/fly-brain")
def fly_brain_page():
    return render_template("fly_brain.html")


@app.route("/circuit-card")
def circuit_card_page():
    node_activity = circuit_sim.last_snapshot()
    top_neurons = sorted(node_activity.items(), key=lambda kv: abs(kv[1]), reverse=True)[:12]
    return render_template(
        "circuit_card.html",
        top_neurons=top_neurons,
        quote=BEST_QUOTE["text"],
        meter=circuit_sim.last_meter(),
        disclaimer=stats.DISCLAIMER,
    )


CONNECTED_CLIENTS = set()
DISCONNECT_GRACE_SECONDS = 15


@socketio.on("join")
def handle_join():
    role = session.get("role")
    if role not in CLAIMED_ROLES:
        return
    join_room(ROOM)
    client_id = session.get("client_id")
    if client_id:
        CONNECTED_CLIENTS.add(client_id)
    emit("status", {"text": f"{role} has entered the chat"}, room=ROOM)


@socketio.on("spectate")
def handle_spectate():
    join_room(ROOM)


@socketio.on("message")
def handle_message(data):
    role = session.get("role")
    if role not in CLAIMED_ROLES:
        return
    text = (data or {}).get("text", "").strip()
    if not text:
        return
    text = text[:MAX_MESSAGE_LENGTH]

    now = time.monotonic()
    if now - _last_message_at.get(role, 0.0) < MIN_SECONDS_BETWEEN_MESSAGES:
        return
    _last_message_at[role] = now

    emit("new_message", {"sender": role, "text": text}, room=ROOM)
    emit("fly_update", {"state": "analysing", "brainstorm": "", "judge": None, "meter": circuit_sim.last_meter()}, room=ROOM)

    with _message_lock:
        MESSAGE_COUNTS[role] += 1
        message_counts = dict(MESSAGE_COUNTS)

        scores = score_message(text)
        sim_result = circuit_sim.step(scores)
        meter = sim_result["meter"]

        log_message(role, text, scores["warmth"], scores["humor"], scores["reciprocity"], meter)

        avg_score = (scores["warmth"] + scores["humor"] + scores["reciprocity"]) / 3.0
        if avg_score > BEST_QUOTE["avg_score"]:
            BEST_QUOTE["text"] = text
            BEST_QUOTE["avg_score"] = avg_score
            _save_best_quote()

        state = "verdict" if meter >= THRESHOLD else next(_flavor_cycle)
        region_data = stats.update(role, text, scores)

    brainstorm = brainstorm_comment(text, scores, meter)
    judge = judge_line(state)

    emit(
        "fly_update",
        {
            "state": state,
            "brainstorm": brainstorm,
            "judge": judge,
            "meter": meter,
            "scores": scores,
            "message_counts": message_counts,
        },
        room=ROOM,
    )
    emit("brain_activity", {"node_activity": sim_result["node_activity"]}, room=ROOM)
    emit("region_update", region_data, room=ROOM)


@socketio.on("disconnect")
def handle_disconnect():
    role = session.get("role")
    client_id = session.get("client_id")
    if role not in CLAIMED_ROLES:
        return

    CONNECTED_CLIENTS.discard(client_id)
    emit("status", {"text": f"{role} has left the chat"}, room=ROOM)
    socketio.start_background_task(_free_role_after_grace, role, client_id)


def _free_role_after_grace(role, client_id):
    socketio.sleep(DISCONNECT_GRACE_SECONDS)
    # Only free the seat if this exact client never reconnected and still
    # holds it — a page refresh disconnects/reconnects almost instantly, so
    # this grace period keeps a normal reload from losing someone's seat.
    if client_id not in CONNECTED_CLIENTS and CLAIMED_ROLES.get(role) == client_id:
        CLAIMED_ROLES[role] = None
        socketio.emit(
            "status",
            {"text": f"{role}'s seat opened back up — they didn't reconnect."},
            room=ROOM,
        )


if __name__ == "__main__":
    port = int(os.environ.get("PORT", 5000))
    debug = os.environ.get("FLASK_DEBUG", "0") == "1"
    socketio.run(app, host="0.0.0.0", port=port, debug=debug, allow_unsafe_werkzeug=True)
