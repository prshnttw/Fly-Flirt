"""Local, dependency-free chat scorer.

Used when every API in the chain is unavailable or saturated, and as a source of
plausible defaults if a model returns incomplete JSON. It is deliberately simple
(lexicons + punctuation + overlap with the partner's last message) but deterministic,
instant and always available, so the neural simulation never stalls.
"""
from __future__ import annotations

import math
import re
import zlib

PARAM_NAMES = ("warmth", "humor", "reciprocity", "curiosity", "disclosure", "energy", "tension")

_WORD_RE = re.compile(r"[a-z']+")
_EMOJI_RE = re.compile("[\U0001F300-\U0001FAFF☀-➿❤✨]")

WARM_WORDS = {
    "love", "loved", "like", "likes", "appreciate", "thanks", "thank", "sweet", "kind", "glad", "happy",
    "lovely", "adorable", "cute", "miss", "care", "wonderful", "amazing", "great", "awesome", "nice",
    "beautiful", "gorgeous", "enjoy", "enjoyed", "welcome", "heart", "friend", "proud", "grateful",
    "delightful", "charming", "hug", "cozy", "fun", "best", "perfect", "adore", "support", "congrats",
}
WARM_EMOJI = set("❤💕😊😍🥰😘☺🤗💖✨💛💜🌸😌")
HUMOR_WORDS = {
    "lol", "lmao", "lmfao", "haha", "hahaha", "hehe", "jk", "joke", "funny", "hilarious", "rofl", "pun",
    "kidding", "silly", "ridiculous", "absurd", "banter", "roast", "meme", "giggle", "chuckle",
}
HUMOR_EMOJI = set("😂🤣😆😜😝😉🙃😹😏🤪")
TENSION_WORDS = {
    "hate", "stupid", "annoying", "shut", "whatever", "boring", "ugh", "wtf", "idiot", "worse", "sucks",
    "dumb", "pathetic", "useless", "rude", "ridiculous", "angry", "furious", "disgusting", "creep",
    "leave", "blocked", "block", "fine", "nvm", "nevermind", "seriously", "unbelievable", "liar",
}
DISMISSIVE = {"k", "ok", "okay", "fine", "whatever", "idc", "sure", "nah", "meh", "hm", "hmm"}
QUESTION_WORDS = {"what", "why", "how", "where", "when", "who", "which", "tell", "wondering", "curious"}
ACK_WORDS = {"yeah", "yes", "right", "same", "true", "totally", "exactly", "agree", "definitely", "absolutely", "ha", "oh", "wow"}
DISCLOSE_PHRASES = ("i feel", "i think", "i'm", "i am", "i was", "i've", "i love", "i hate", "i used to", "my ", "honestly", "to be honest", "growing up")
STOPWORDS = {
    "the", "a", "an", "and", "or", "but", "to", "of", "in", "on", "at", "is", "it", "that", "this", "with", "for",
    "you", "your", "i", "me", "my", "we", "are", "was", "be", "so", "do", "have", "has", "just", "not", "no",
    "yes", "what", "how", "why", "about", "as", "if", "then", "than", "too", "very", "can", "will", "would",
}

DIARY = {
    "warmth": ["the fly's wings go all soft at that one", "that message smelled like fresh fruit", "kindness detected, antennae twitching"],
    "humor": ["the fly is vibrating with suppressed giggles", "a joke landed, buzzing intensifies", "banter circuit: lit"],
    "reciprocity": ["you two are finishing each other's wingbeats", "call-and-response detected, the fly approves", "that was a proper reply, not just noise"],
    "curiosity": ["a question! the fly leans in with both eyes", "curiosity spike, visual cells locking on", "asking is basically flirting for flies"],
    "disclosure": ["a personal confession, the fly goes quiet and listens", "vulnerability registered in the lateral horn", "that felt real, the hub noticed"],
    "energy": ["high-voltage message, the fly does a loop", "so much energy the fly nearly left orbit", "enthusiasm broadcast at full amplitude"],
    "tension": ["the fly senses friction and holds its brake on", "a chill in the air, mAL neurons tighten", "hmm, that one raised the fly's guard"],
    "calm": ["quiet signal, the fly waits patiently", "small talk, the circuit hums at idle", "nothing dramatic yet, still listening"],
}
TIPS = {
    # Advice for the person who will REPLY to the message.
    "warmth": ["They are being kind. Answer warmly and add one specific detail about yourself.", "Return the compliment with a genuine question of your own."],
    "humor": ["They are joking. Ride it: build on the joke instead of switching topics.", "Tease back a little, they clearly enjoy the banter."],
    "reciprocity": ["They are engaging with you. Add something new so the conversation moves forward.", "Pick up one thing they said and go deeper."],
    "curiosity": ["They asked something. Answer it with a story, not just a fact.", "Answer, then ask them something back."],
    "disclosure": ["They opened up. Thank them, then share something real in return.", "Match their openness with a genuine detail of your own."],
    "energy": ["They are excited. Match the energy, then ask one question.", "Channel their excitement into a concrete idea."],
    "tension": ["That came out a bit tense. Acknowledge it gently before replying.", "Try a light, friendly line to reset the tone."],
    "calm": ["Ask them an open question about something they enjoy.", "Share one small personal detail to warm things up."],
}


def _sat(x: float, k: float = 1.0) -> float:
    return 1.0 - math.exp(-k * max(x, 0.0))


def _pick(pool: list[str], text: str) -> str:
    return pool[zlib.crc32(text.encode("utf-8", "ignore")) % len(pool)]


def _content_words(text: str) -> set[str]:
    return {w for w in _WORD_RE.findall(text.lower()) if len(w) > 2 and w not in STOPWORDS}


def score_text(text: str, prev_text: str = "", mode: str = "friends") -> dict:
    """Return the same structure the LLM path produces."""
    low = text.lower()
    words = _WORD_RE.findall(low)
    wset = set(words)
    n_words = max(len(words), 1)
    emoji = _EMOJI_RE.findall(text)
    n_q = text.count("?")
    n_ex = text.count("!")
    caps = sum(1 for ch in text if ch.isupper())
    alpha = sum(1 for ch in text if ch.isalpha()) or 1
    stretched = len(re.findall(r"([a-z])\1{2,}", low))

    warm_hits = len(wset & WARM_WORDS) + sum(1 for e in emoji if e in WARM_EMOJI)
    humor_hits = len(wset & HUMOR_WORDS) + sum(1 for e in emoji if e in HUMOR_EMOJI) + len(re.findall(r"\b(ha){2,}\b|\b(he){2,}\b", low))
    tension_hits = len(wset & TENSION_WORDS) + (1 if "!!" in text and wset & TENSION_WORDS else 0)
    dismissive = 1.0 if (n_words <= 2 and wset & DISMISSIVE) else 0.0

    overlap = 0.0
    if prev_text:
        a, b = _content_words(text), _content_words(prev_text)
        if a and b:
            overlap = len(a & b) / max(min(len(a), len(b)), 1)
    you = len(wset & {"you", "your", "u", "ur", "youre", "you're"})
    ack = len(wset & ACK_WORDS)

    disclose_hits = sum(1 for p in DISCLOSE_PHRASES if p in low)
    first_person = len(wset & {"i", "my", "me", "i'm", "i've", "i'd"})

    warmth = 0.06 + 0.9 * _sat(warm_hits * 0.7 + 0.15 * (you > 0) + 0.1 * (n_ex > 0), 0.9)
    humor = 0.04 + 0.92 * _sat(humor_hits * 0.85 + 0.2 * stretched, 0.9)
    reciprocity = 0.10 + 0.85 * _sat(1.4 * overlap + 0.35 * (n_q > 0) + 0.25 * (you > 0) + 0.25 * (ack > 0) + 0.004 * len(text), 1.1)
    curiosity = 0.04 + 0.9 * _sat(0.7 * n_q + 0.5 * len(wset & QUESTION_WORDS), 1.0)
    disclosure = 0.04 + 0.9 * _sat(0.6 * disclose_hits + 0.12 * first_person + 0.003 * len(text), 0.9)
    energy = 0.06 + 0.9 * _sat(0.5 * n_ex + 0.3 * len(emoji) + 0.25 * stretched + 1.5 * (caps / alpha > 0.3 and alpha > 3) + 0.003 * len(text), 0.9)
    tension = 0.02 + 0.9 * _sat(0.8 * tension_hits + 0.6 * dismissive, 1.0)
    if dismissive:
        warmth *= 0.4
        reciprocity *= 0.5

    params = {
        "warmth": warmth, "humor": humor, "reciprocity": reciprocity, "curiosity": curiosity,
        "disclosure": disclosure, "energy": energy, "tension": tension,
    }
    params = {k: round(max(0.0, min(1.0, v)), 2) for k, v in params.items()}

    positive = {k: v for k, v in params.items() if k != "tension"}
    top, top_val = max(positive.items(), key=lambda kv: kv[1])
    if params["tension"] >= 0.4:
        key = "tension"
    elif top_val < 0.3:
        key = "calm"
    else:
        key = top

    content = [w for w in words if len(w) > 3 and w not in STOPWORDS]
    topic = (content[0] if content else "small talk")[:24]
    return {
        "params": params,
        "topic": topic,
        "diary": _pick(DIARY[key], text),
        "tip": _pick(TIPS[key], text + "tip"),
    }
