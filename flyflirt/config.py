"""Application configuration, driven entirely by environment variables."""
from __future__ import annotations

import os
from dataclasses import dataclass, field

ROOT_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

try:  # a local .env is a convenience for development; real deployments use real env vars
    from dotenv import load_dotenv

    load_dotenv(os.path.join(ROOT_DIR, ".env"))
except ImportError:  # pragma: no cover
    pass


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    return value if value not in (None, "") else default


def _int(name: str, default: int) -> int:
    try:
        return int(_env(name, str(default)))
    except (TypeError, ValueError):
        return default


def _float(name: str, default: float) -> float:
    try:
        return float(_env(name, str(default)))
    except (TypeError, ValueError):
        return default


def _bool(name: str, default: bool) -> bool:
    value = _env(name)
    if value is None:
        return default
    return value.strip().lower() in ("1", "true", "yes", "on")


def _bool_or_none(name: str) -> bool | None:
    """Like ``_bool``, but returns ``None`` (rather than a fixed default) when unset, so a caller can
    tell "explicitly set" apart from "use whatever the situation implies" — see RETAIN_MESSAGE_TEXT."""
    value = _env(name)
    return None if value is None else value.strip().lower() in ("1", "true", "yes", "on")


@dataclass
class LLMModelSpec:
    """One entry in the LLM fallback chain, e.g. ``groq:qwen/qwen3.8-27b``."""

    provider: str
    model: str
    extra: dict = field(default_factory=dict)


# Free-tier limits per model (Groq, per organisation). RPM/TPD are not exposed in
# response headers, so we enforce them locally to avoid ever hitting a 429 mid-chat.
GROQ_FREE_LIMITS = {
    "qwen/qwen3.8-27b": {"rpm": 30, "rpd": 1000, "tpm": 8000, "tpd": 200_000},
    "openai/gpt-oss-20b": {"rpm": 30, "rpd": 1000, "tpm": 8000, "tpd": 200_000},
    "openai/gpt-oss-120b": {"rpm": 30, "rpd": 1000, "tpm": 8000, "tpd": 200_000},
    "allam-2-7b": {"rpm": 30, "rpd": 7000, "tpm": 6000, "tpd": 300_000},
}

DEFAULT_CHAIN = (
    "groq:qwen/qwen3.8-27b,"
    "groq:openai/gpt-oss-20b,"
    "groq:openai/gpt-oss-120b,"
    "groq:allam-2-7b"
)

# Extra request parameters some models need (reasoning models burn tokens otherwise).
MODEL_EXTRA = {
    "openai/gpt-oss-20b": {"reasoning_effort": "low"},
    "openai/gpt-oss-120b": {"reasoning_effort": "low"},
    "qwen/qwen3.8-27b": {"reasoning_effort": "none"},
}


class Config:
    ENV = _env("APP_ENV", "development")
    DEBUG = _bool("FLASK_DEBUG", False)
    SECRET_KEY = _env("SECRET_KEY")
    # The tiny /admin reports page (see flyflirt/web/admin.py) only exists at all when BOTH of these are
    # set; leave either unset and every /admin/* route 404s, indistinguishable from not existing. It's
    # HTTP Basic Auth, so it only makes sense served over HTTPS (true on Railway/Fly by default).
    ADMIN_USERNAME = _env("ADMIN_USERNAME")
    ADMIN_PASSWORD = _env("ADMIN_PASSWORD")
    PORT = _int("PORT", 5000)

    # storage
    DATA_DIR = _env("DATA_DIR", os.path.join(ROOT_DIR, "instance"))
    DB_PATH = _env("DB_PATH") or os.path.join(DATA_DIR, "flyflirt.db")
    CONNECTOME_PATH = _env("CONNECTOME_PATH", os.path.join(ROOT_DIR, "static", "connectome.json"))
    # Optional bigger "full pathway" subgraph (tools/build_connectome.py --context 4000). Rooms opt in.
    CONNECTOME_FULL_PATH = _env("CONNECTOME_FULL_PATH", os.path.join(ROOT_DIR, "static", "connectome_full.json"))
    FULL_ENABLED = _bool("FULL_ENABLED", True)
    FULL_MAX_ROOMS = _int("FULL_MAX_ROOMS", 8)   # cap on concurrent full-pathway rooms (each step costs ~45 ms of CPU)
    RETENTION_DAYS = _int("RETENTION_DAYS", 7)
    # Privacy-by-design default that adapts to how the app is being run: a real deployment
    # (APP_ENV=production, e.g. the Railway guide below) defaults to never writing your words to disk —
    # only the *numbers* derived from them (LLM params, which neurons/synapses fired) are persisted,
    # which is all the verdict/replay machinery needs. Someone running this locally off their own clone
    # (APP_ENV unset/development) gets the opposite default, since it's their own machine and keeping the
    # transcript is usually more useful there than a privacy risk. None here means "not explicitly set" —
    # see Services.retain_text_default, which resolves the ENV-based default; RETAIN_MESSAGE_TEXT itself
    # stays None/True/False so a subclass (tests) can override ENV and have the default follow it. Any
    # individual chat can override the *result* again at the room level (Room.store_override / the "Save
    # this chat" toggle in the chat page). The live chat (in memory, for as long as the process runs) is
    # unaffected either way; this only controls what a restart/reconnect can recover.
    RETAIN_MESSAGE_TEXT = _bool_or_none("RETAIN_MESSAGE_TEXT")

    # matchmaking
    MATCH_TIMEOUT_S = _int("MATCH_TIMEOUT_S", 120)
    INVITE_TTL_S = _int("INVITE_TTL_S", 1800)
    ROOM_IDLE_TTL_S = _int("ROOM_IDLE_TTL_S", 6 * 3600)
    IDLE_VERDICT_NUDGE_S = _int("IDLE_VERDICT_NUDGE_S", 300)   # gone quiet for this long -> "see your verdict?"
    RECONNECT_GRACE_S = _int("RECONNECT_GRACE_S", 25)

    # chat
    MAX_MESSAGE_LEN = _int("MAX_MESSAGE_LEN", 400)
    MAX_MESSAGES_PER_ROOM = _int("MAX_MESSAGES_PER_ROOM", 300)
    MIN_MESSAGES_FOR_VERDICT = _int("MIN_MESSAGES_FOR_VERDICT", 3)
    MSG_BURST = _int("MSG_BURST", 4)
    MSG_REFILL_PER_S = _float("MSG_REFILL_PER_S", 0.5)

    # request-level abuse control (per IP)
    JOIN_BURST = _int("JOIN_BURST", 12)
    JOIN_REFILL_PER_S = _float("JOIN_REFILL_PER_S", 0.2)
    CODE_GUESS_BURST = _int("CODE_GUESS_BURST", 8)
    CODE_GUESS_REFILL_PER_S = _float("CODE_GUESS_REFILL_PER_S", 0.05)

    # simulation
    SIM_GAIN = _float("SIM_GAIN", 4.0)
    SIM_INPUT_GAIN = _float("SIM_INPUT_GAIN", 1.6)
    SIM_THRESHOLD = _float("SIM_THRESHOLD", 0.14)
    SIM_METER_SCALE = _float("SIM_METER_SCALE", 0.107)
    SIM_METER_SCALE_FULL = _float("SIM_METER_SCALE_FULL", 0.079)   # re-fitted so both graphs read alike
    SIM_SUBSTEPS = _int("SIM_SUBSTEPS", 4)
    SIM_DECAY_START = _float("SIM_DECAY_START", 0.82)
    SIM_DECAY_END = _float("SIM_DECAY_END", 0.95)
    SIM_HABITUATION_HALF = _float("SIM_HABITUATION_HALF", 14.0)
    SIM_ACT_THR = _float("SIM_ACT_THR", 0.10)
    SIM_EDGE_THR = _float("SIM_EDGE_THR", 0.03)

    # LLM
    GROQ_API_KEY = _env("GROQ_API_KEY") or _env("LLM_API_KEY")
    GEMINI_API_KEY = _env("GEMINI_API_KEY")
    LLM_CHAIN = _env("LLM_CHAIN", DEFAULT_CHAIN)
    LLM_TIMEOUT_S = _float("LLM_TIMEOUT_S", 5.0)
    LLM_TOTAL_BUDGET_S = _float("LLM_TOTAL_BUDGET_S", 9.0)
    LLM_MAX_CONCURRENCY = _int("LLM_MAX_CONCURRENCY", 6)
    LLM_QUEUE_WAIT_S = _float("LLM_QUEUE_WAIT_S", 0.75)
    LLM_QUOTA_MARGIN = _float("LLM_QUOTA_MARGIN", 0.92)
    LLM_ENABLED = _bool("LLM_ENABLED", True)

    # web
    ALLOWED_ORIGINS = _env("ALLOWED_ORIGINS")  # comma list; unset => same-origin only
    TRUST_PROXY_HOPS = _int("TRUST_PROXY_HOPS", 1)
    SESSION_COOKIE_NAME = "ff_session"
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    SESSION_COOKIE_SECURE = _bool("SESSION_COOKIE_SECURE", False)
    PERMANENT_SESSION_LIFETIME = 60 * 60 * 24 * 30

    @classmethod
    def chain_specs(cls) -> list[LLMModelSpec]:
        specs: list[LLMModelSpec] = []
        for raw in (cls.LLM_CHAIN or "").split(","):
            raw = raw.strip()
            if not raw or ":" not in raw:
                continue
            provider, model = raw.split(":", 1)
            specs.append(LLMModelSpec(provider.strip().lower(), model.strip(), dict(MODEL_EXTRA.get(model.strip(), {}))))
        return specs


class TestConfig(Config):
    ENV = "test"
    TESTING = True
    DB_PATH = ":memory:"
    SECRET_KEY = "test-secret"
    LLM_ENABLED = False
    # Tests must not inherit whatever happens to be in the developer's real .env (e.g. real admin
    # credentials for local use) — that would make the "admin is off by default" tests flaky depending
    # on what's sitting in .env. Force these off; individual tests opt back in explicitly.
    ADMIN_USERNAME = None
    ADMIN_PASSWORD = None
    MSG_BURST = 50
    MSG_REFILL_PER_S = 50.0
    JOIN_BURST = 500
    CODE_GUESS_BURST = 500
    RECONNECT_GRACE_S = 1
