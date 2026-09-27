"""Small, dependency-free helpers: ids, input sanitising, token-bucket rate limiting."""
from __future__ import annotations

import re
import secrets
import threading
import time
import unicodedata

# 32 unambiguous symbols (no 0/O/1/I): 6 chars => ~1.07e9 codes.
INVITE_ALPHABET = "23456789ABCDEFGHJKLMNPQRSTUVWXYZ"
INVITE_LENGTH = 6

_CONTROL_RE = re.compile(r"[\x00-\x1f\x7f-\x9f​-‏ -‮⁠-⁤﻿]")
_WS_RE = re.compile(r"\s+")
# A closed, curated set of single-codepoint emoji a nickname may carry (matches the client's random
# generator). Deliberately not "any emoji": open-ended emoji input invites ZWJ/skin-tone/variation-selector
# sequences and visual-spoofing homoglyphs, which is more trouble than it's worth for a nickname.
NICKNAME_EMOJI = ("🦋", "🐝", "🦗", "🐛", "🦟", "🐞", "✨", "🌙", "🔥", "💫", "🌟", "🐜")
MAX_NICK_LEN = 24  # keep in sync with the `maxlength` on every nickname <input>
_NICK_RE = re.compile(r"[^\w \-." + re.escape("".join(NICKNAME_EMOJI)) + r"]", re.UNICODE)


def new_room_id() -> str:
    return secrets.token_urlsafe(9)


def new_share_token() -> str:
    return secrets.token_urlsafe(16)


def new_client_id() -> str:
    return secrets.token_urlsafe(18)


def new_invite_code() -> str:
    return "".join(secrets.choice(INVITE_ALPHABET) for _ in range(INVITE_LENGTH))


def normalise_code(raw: str | None) -> str | None:
    """Uppercase, strip separators, and validate an invite code. Returns None if invalid."""
    if not raw:
        return None
    code = re.sub(r"[\s\-_]", "", str(raw)).upper()
    if len(code) != INVITE_LENGTH or any(ch not in INVITE_ALPHABET for ch in code):
        return None
    return code


def clean_text(raw: object, max_len: int) -> str:
    """Strip control/zero-width characters, collapse whitespace, cap the length."""
    if not isinstance(raw, str):
        return ""
    text = unicodedata.normalize("NFKC", raw)
    text = _CONTROL_RE.sub(" ", text)
    text = _WS_RE.sub(" ", text).strip()
    return text[:max_len]


def clean_nickname(raw: object, fallback: str = "") -> str:
    if not isinstance(raw, str):
        return fallback
    text = _NICK_RE.sub("", unicodedata.normalize("NFKC", raw))
    text = _WS_RE.sub(" ", _CONTROL_RE.sub(" ", text)).strip()[:MAX_NICK_LEN]
    return text or fallback


class TokenBucket:
    """Thread-safe token bucket keyed by an arbitrary string (client id, ip...)."""

    def __init__(self, capacity: float, refill_per_s: float, max_keys: int = 20000):
        self.capacity = float(capacity)
        self.refill = float(refill_per_s)
        self.max_keys = max_keys
        self._state: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def allow(self, key: str, cost: float = 1.0) -> bool:
        now = time.monotonic()
        with self._lock:
            tokens, last = self._state.get(key, (self.capacity, now))
            tokens = min(self.capacity, tokens + (now - last) * self.refill)
            allowed = tokens >= cost
            if allowed:
                tokens -= cost
            self._state[key] = (tokens, now)
            if len(self._state) > self.max_keys:
                self._prune(now)
            return allowed

    def _prune(self, now: float) -> None:
        horizon = self.capacity / max(self.refill, 1e-9) + 60
        stale = [k for k, (_, last) in self._state.items() if now - last > horizon]
        for k in stale:
            self._state.pop(k, None)


def parse_duration(text: str | None) -> float | None:
    """Parse durations like '1m26.4s', '592ms', '12.3s', '2h3m' into seconds."""
    if not text:
        return None
    total = 0.0
    matched = False
    for value, unit in re.findall(r"([\d.]+)\s*(ms|h|m|s)", str(text)):
        try:
            number = float(value)
        except ValueError:
            continue
        matched = True
        total += number * {"ms": 0.001, "s": 1.0, "m": 60.0, "h": 3600.0}[unit]
    if matched:
        return total
    try:
        return float(text)
    except (TypeError, ValueError):
        return None
