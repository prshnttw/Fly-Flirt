"""Message moderation: a small, dependency-free filter.

It is deliberately simple and conservative (this is a first line of defence, not a substitute
for human review; reports, blocks and rate limits back it up):

* ``block``  - hateful slurs, threats, sexual harassment / explicit sexual solicitation, self-harm baiting.
               The message is not delivered and is never sent to the LLM. It counts as a strike.
* ``mask``   - ordinary profanity is starred out, and contact details (emails, phone numbers, links)
               are hidden, because strangers should not swap them and the text goes to an AI service.
* spam       - the same message repeated, or shouting/character floods, is rejected without a strike.

Matching is tolerant of the usual evasions (``f u c k``, ``f.u.c.k``, ``fuuuck``, ``sh1t``, ``@ss``).
"""
from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s", "7": "t", "@": "a", "$": "s", "!": "i", "+": "t"})

# Ordinary profanity: masked, not blocked.
MILD = ["fuck", "fucking", "fucker", "motherfucker", "shit", "bullshit", "bitch", "asshole", "dick", "dickhead",
        "cock", "pussy", "bastard", "crap", "piss", "slut", "whore", "wtf", "stfu", "damn"]

# Hate slurs and sexual harassment: blocked. Patterns work on the *normalised* text (no spaces/punctuation).
SEVERE_TERMS = [
    "nigger", "nigga", "faggot", "fag", "retard", "tranny", "kike", "spic", "chink", "coon", "paki", "wetback",
    "rapeyou", "rapedyou", "iwillrape", "illrape", "gangrape", "raping", "rapist",
    "sendnudes", "sendnude", "sendpics", "showmeyourtits", "showmeyourboobs", "showmeyourpussy", "suckmydick",
    "suckmycock", "blowjob", "handjob", "sexwithme", "fuckme", "fuckyou", "fuckyourself", "gofuckyourself",
    "killyourself", "kys", "hangyourself", "gokillyourself", "iwillkillyou", "illkillyou", "iwillfindyou",
    "iknowwhereyoulive", "ikillyou", "killallyou",
]
# Words that are only offensive as whole words (avoid the Scunthorpe problem for short terms).
SEVERE_WHOLE = {"fag", "coon", "kys", "spic", "paki", "kike", "chink"}

THREAT_RE = re.compile(
    r"\b(i('| a)?m? ?(going to|gonna|will|'ll)|imma|ima)\s+(kill|hurt|find|beat|stab|shoot|rape)\s+(you|u|ur|your)\b", re.I)

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(\.[\w-]+)+")
URL_RE = re.compile(r"(https?://\S+|www\.\S+|\b[\w-]+\.(com|net|org|io|me|co|gg|ly|xyz|app)\b\S*)", re.I)
PHONE_RE = re.compile(r"(?<!\d)(\+?\d[\d\s().-]{7,}\d)(?!\d)")
HANDLE_RE = re.compile(r"\b(snap(chat)?|insta(gram)?|whats ?app|telegram|kik|discord)\b[^\w]{0,3}(id|handle|me|:|@)?\s*[:@]?\s*[\w.]{3,}", re.I)


def _strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def normalise(text: str) -> str:
    """Lower-case, de-leet, drop everything that is not a letter, collapse letter floods."""
    t = _strip_accents(text).lower().translate(LEET)
    t = re.sub(r"[^a-z]", "", t)
    return re.sub(r"(.)\1{2,}", r"\1\1", t)


def _flex(word: str) -> re.Pattern:
    """A regex matching ``word`` with optional separators / repeated letters between its letters."""
    body = r"[\W_]*".join(f"{re.escape(ch)}+" for ch in word)
    return re.compile(rf"(?<![a-z]){body}(?![a-z])", re.I)


_MILD_RES = [_flex(w) for w in sorted(MILD, key=len, reverse=True)]


@dataclass
class Verdict:
    action: str                    # "ok" | "mask" | "block" | "spam"
    text: str                      # the text to deliver (masked if needed)
    reason: str = ""               # machine reason: slur | threat | sexual | spam | contact | profanity
    message: str = ""              # human explanation shown to the sender


_WHOLE_RES = {t: _flex(t) for t in SEVERE_WHOLE}


def _severe_hit(norm: str, lowered: str) -> str | None:
    for term in SEVERE_TERMS:
        if term in SEVERE_WHOLE:
            if _WHOLE_RES[term].search(lowered):
                return term
        elif term in norm:
            return term
    return None


def mask_text(text: str) -> tuple[str, bool]:
    changed = False

    def star(m):
        return m.group(0)[0] + "*" * max(1, len(re.sub(r"[\W_]", "", m.group(0))) - 1)

    out = text
    for rx in _MILD_RES:
        new = rx.sub(star, out)
        changed |= new != out
        out = new
    for rx, label in ((EMAIL_RE, "[email hidden]"), (URL_RE, "[link hidden]"), (HANDLE_RE, "[contact hidden]"), (PHONE_RE, "[number hidden]")):
        new = rx.sub(label, out)
        changed |= new != out
        out = new
    return out, changed


def check_message(text: str) -> Verdict:
    lowered = _strip_accents(text).lower().translate(LEET)
    norm = normalise(text)
    hit = _severe_hit(norm, lowered)
    if hit:
        reason = "slur" if hit in ("nigger", "nigga", "faggot", "fag", "retard", "tranny", "kike", "spic", "chink", "coon", "paki", "wetback") else (
            "threat" if any(k in hit for k in ("kill", "hang", "find", "kys", "livee", "rape")) else "sexual")
        return Verdict("block", "", reason, "That message was blocked because it breaks the community rules. Please keep it respectful.")
    if THREAT_RE.search(text):
        return Verdict("block", "", "threat", "That message was blocked because it reads as a threat.")
    masked, changed = mask_text(text)
    if changed:
        contact = any(x in masked for x in ("[email hidden]", "[link hidden]", "[contact hidden]", "[number hidden]"))
        return Verdict("mask", masked, "contact" if contact else "profanity")
    letters = [c for c in text if c.isalpha()]
    if len(letters) >= 12 and sum(c.isupper() for c in letters) / len(letters) > 0.85:
        return Verdict("mask", text.capitalize(), "shouting")
    if re.search(r"(.)\1{14,}", text):
        return Verdict("spam", "", "spam", "That looks like spam. Try writing a normal message.")
    return Verdict("ok", text)


class RepeatGuard:
    """Rejects a client sending the same message over and over."""

    def __init__(self, limit: int = 3, window: int = 6):
        self.limit, self.window = limit, window
        self._recent: dict[str, list[str]] = {}

    def repeated(self, client: str, text: str) -> bool:
        key = normalise(text) or text
        recent = self._recent.setdefault(client, [])
        count = sum(1 for k in recent if k == key)
        recent.append(key)
        del recent[: -self.window]
        return count >= self.limit - 1
