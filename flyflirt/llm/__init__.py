"""LLM analysis of chat messages with a resilient multi-provider fallback chain."""
from __future__ import annotations

import logging

from ..config import GROQ_FREE_LIMITS
from .router import Analysis, LLMRouter, build_user_prompt, extract_json, normalise  # noqa: F401

log = logging.getLogger("flyflirt.llm")


def build_router(cfg, usage_store=None) -> LLMRouter:
    """Build the ordered fallback chain from ``LLM_CHAIN`` (e.g. ``groq:qwen/...,groq:openai/...``)."""
    from .providers import GeminiProvider, GroqProvider

    providers = []
    for spec in cfg.chain_specs():
        limits = GROQ_FREE_LIMITS.get(spec.model, {}) if spec.provider == "groq" else {}
        try:
            if spec.provider == "groq":
                if not cfg.GROQ_API_KEY:
                    log.warning("skipping %s: no GROQ_API_KEY/LLM_API_KEY configured", spec.model)
                    continue
                providers.append(GroqProvider(cfg.GROQ_API_KEY, spec.model, spec.extra, limits))
            elif spec.provider == "gemini":
                if not cfg.GEMINI_API_KEY:
                    continue
                providers.append(GeminiProvider(cfg.GEMINI_API_KEY, spec.model, spec.extra, limits))
            else:
                log.warning("unknown LLM provider %r in LLM_CHAIN", spec.provider)
        except Exception:  # e.g. SDK not installed; the chat must still start
            log.exception("could not initialise provider %s:%s", spec.provider, spec.model)
    return LLMRouter(providers, cfg, usage_store)
