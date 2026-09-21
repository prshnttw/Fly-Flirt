import json
import os
import re

STATE_PATH = os.path.join(os.path.dirname(__file__), "stats_state.json")

DECISION_WORDS = {
    "someday", "future", "believe", "value", "values", "dream", "imagine",
    "forever", "eventually", "goals",
}
LOGISTICS_WORDS = {
    "today", "tonight", "tomorrow", "weekend", "monday", "tuesday", "wednesday",
    "thursday", "friday", "saturday", "sunday", "pm", "am", "o'clock", "meet",
    "meetup", "let's", "lets",
}
STOPWORDS = {
    "that", "this", "with", "have", "what", "your", "about", "just", "like",
    "when", "then", "from", "they", "them", "been", "were", "would", "could",
    "really", "actually", "because", "some", "which", "there", "here", "doing",
}

WORD_RE = re.compile(r"[a-z']+")
DECAY = 0.6

DISCLAIMER = "This is a fruit-fly courtship cartoon sitting on your chat stats. It is not destiny."

_history = []  # list of (role, text, warmth), most recent last
_running = {"shared": 0.0, "spark": 0.0, "mismatch": 0.0, "stall": 0.0}
_regions = {
    "optic_lobe": 0.0,
    "mushroom_body": 0.0,
    "courtship_center": 0.0,
    "decision_center": 0.0,
    "nerve_cord": 0.0,
    "dead_synapse": 0.0,
}


def _save_state():
    with open(STATE_PATH, "w") as f:
        json.dump({"history": _history, "running": _running, "regions": _regions}, f)


def _load_state():
    if not os.path.exists(STATE_PATH):
        return
    try:
        with open(STATE_PATH) as f:
            data = json.load(f)
        _history[:] = [tuple(item) for item in data.get("history", [])]
        _running.update(data.get("running", {}))
        _regions.update(data.get("regions", {}))
    except (json.JSONDecodeError, ValueError):
        pass


_load_state()


def reset():
    _history.clear()
    for d in (_running, _regions):
        for k in d:
            d[k] = 0.0
    _save_state()


def _significant_words(text):
    words = WORD_RE.findall(text.lower())
    return {w for w in words if len(w) >= 4 and w not in STOPWORDS}


def _contains_any(text, vocab):
    words = set(WORD_RE.findall(text.lower()))
    return not words.isdisjoint(vocab)


def _ema(key, store, instant):
    store[key] = DECAY * store[key] + (1 - DECAY) * instant
    return store[key]


def update(role, text, scores):
    warmth = scores.get("warmth", 0.0)
    humor = scores.get("humor", 0.0)
    reciprocity = scores.get("reciprocity", 0.0)

    other_recent = [(r, t, w) for r, t, w in _history[-6:] if r != role]
    other_words = set()
    for _, t, _ in other_recent:
        other_words |= _significant_words(t)
    current_words = _significant_words(text)

    shared_instant = 1.0 if current_words and other_words and not current_words.isdisjoint(other_words) else 0.0
    spark_instant = (warmth + humor) / 2.0

    word_count = len(text.split())
    stall_instant = (0.7 if word_count <= 2 else 0.0) + 0.3 * (1 - reciprocity)
    stall_instant = max(0.0, min(1.0, stall_instant))

    last_other_warmth = other_recent[-1][2] if other_recent else warmth
    mismatch_instant = abs(warmth - last_other_warmth)

    older_words = set()
    for _, t, _ in _history[:-1][-8:-1]:
        older_words |= _significant_words(t)
    callback_instant = 1.0 if current_words and older_words and not current_words.isdisjoint(older_words) else 0.0

    decision_instant = 1.0 if _contains_any(text, DECISION_WORDS) and "?" in text else 0.0
    nerve_cord_instant = 1.0 if _contains_any(text, LOGISTICS_WORDS) else 0.0

    shared = _ema("shared", _running, shared_instant)
    spark = _ema("spark", _running, spark_instant)
    mismatch = _ema("mismatch", _running, mismatch_instant)
    stall = _ema("stall", _running, stall_instant)

    optic_lobe = _ema("optic_lobe", _regions, shared_instant)
    courtship_center = _ema("courtship_center", _regions, spark_instant)
    dead_synapse = _ema("dead_synapse", _regions, stall_instant)
    mushroom_body = _ema("mushroom_body", _regions, callback_instant)
    decision_center = _ema("decision_center", _regions, decision_instant)
    nerve_cord = _ema("nerve_cord", _regions, nerve_cord_instant)

    _history.append((role, text, warmth))
    del _history[:-12]
    _save_state()

    return {
        "shared": shared,
        "spark": spark,
        "mismatch": mismatch,
        "stall": stall,
        "regions": {
            "optic_lobe": optic_lobe,
            "mushroom_body": mushroom_body,
            "courtship_center": courtship_center,
            "decision_center": decision_center,
            "nerve_cord": nerve_cord,
            "dead_synapse": dead_synapse,
        },
        "disclaimer": DISCLAIMER,
    }
