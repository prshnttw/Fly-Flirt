"""LLM provider adapters. Each turns every failure into a typed :class:`ProviderError`."""
from __future__ import annotations

import json
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field

from ..util import parse_duration


class ProviderError(Exception):
    """kind is one of: rate_limit, daily_limit, auth, model_missing, bad_request, timeout, server, network, empty"""

    def __init__(self, kind: str, message: str = "", retry_after: float | None = None, status: int | None = None):
        super().__init__(f"{kind}: {message}" if message else kind)
        self.kind = kind
        self.retry_after = retry_after
        self.status = status
        self.detail = message


@dataclass
class ProviderResult:
    text: str
    tokens: int
    latency_ms: int
    limits: dict = field(default_factory=dict)


class BaseProvider:
    name = "base"

    def __init__(self, model: str, extra: dict | None = None, limits: dict | None = None):
        self.model = model
        self.extra = dict(extra or {})
        self.limits = dict(limits or {})

    @property
    def id(self) -> str:
        return f"{self.name}:{self.model}"

    def complete(self, system: str, user: str, timeout: float, max_tokens: int) -> ProviderResult:  # pragma: no cover
        raise NotImplementedError

    def drop_extra(self) -> bool:
        """Remove optional request parameters after the API rejected them. True if anything changed."""
        if self.extra:
            self.extra = {}
            return True
        return False


_TRY_AGAIN_RE = re.compile(r"try again in ([0-9hms.]+)", re.IGNORECASE)


def _daily(message: str) -> bool:
    low = message.lower()
    return "per day" in low or "(tpd)" in low or "(rpd)" in low or "daily" in low


class GroqProvider(BaseProvider):
    name = "groq"

    def __init__(self, api_key: str, model: str, extra: dict | None = None, limits: dict | None = None):
        super().__init__(model, extra, limits)
        from groq import Groq  # imported lazily so the package is optional at import time

        self._client = Groq(api_key=api_key, max_retries=0)

    def complete(self, system: str, user: str, timeout: float, max_tokens: int) -> ProviderResult:
        import groq

        started = time.monotonic()
        try:
            raw = self._client.chat.completions.with_raw_response.create(
                model=self.model,
                messages=[{"role": "system", "content": system}, {"role": "user", "content": user}],
                temperature=0.4,
                max_tokens=max_tokens,
                timeout=timeout,
                **self.extra,
            )
            completion = raw.parse()
        except groq.APITimeoutError as exc:
            raise ProviderError("timeout", str(exc)) from exc
        except groq.APIConnectionError as exc:
            raise ProviderError("network", str(exc)) from exc
        except groq.APIStatusError as exc:
            raise self._map_status(exc) from exc
        except Exception as exc:  # defensive: never let an SDK quirk escape to the chat path
            raise ProviderError("server", f"{type(exc).__name__}: {exc}") from exc

        text = (completion.choices[0].message.content or "").strip() if completion.choices else ""
        if not text:
            raise ProviderError("empty", "model returned no visible content")
        usage = getattr(completion, "usage", None)
        tokens = int(getattr(usage, "total_tokens", 0) or 0)
        return ProviderResult(
            text=text,
            tokens=tokens,
            latency_ms=int((time.monotonic() - started) * 1000),
            limits=self._read_limits(raw.headers),
        )

    @staticmethod
    def _read_limits(headers) -> dict:
        def num(name):
            try:
                return int(headers.get(name))
            except (TypeError, ValueError):
                return None

        return {
            "remaining_requests": num("x-ratelimit-remaining-requests"),
            "remaining_tokens": num("x-ratelimit-remaining-tokens"),
            "limit_requests": num("x-ratelimit-limit-requests"),
            "limit_tokens": num("x-ratelimit-limit-tokens"),
            "reset_requests_s": parse_duration(headers.get("x-ratelimit-reset-requests")),
            "reset_tokens_s": parse_duration(headers.get("x-ratelimit-reset-tokens")),
        }

    @staticmethod
    def _map_status(exc) -> ProviderError:
        status = getattr(exc, "status_code", None)
        message = str(getattr(exc, "message", "") or exc)
        headers = getattr(getattr(exc, "response", None), "headers", None) or {}
        retry_after = parse_duration(headers.get("retry-after"))
        if retry_after is None:
            match = _TRY_AGAIN_RE.search(message)
            retry_after = parse_duration(match.group(1)) if match else None
        if status == 429:
            return ProviderError("daily_limit" if _daily(message) else "rate_limit", message[:200], retry_after, status)
        if status in (401, 403):
            return ProviderError("auth", message[:200], None, status)
        if status == 404:
            return ProviderError("model_missing", message[:200], None, status)
        if status in (400, 422):
            return ProviderError("bad_request", message[:200], None, status)
        return ProviderError("server", message[:200], retry_after, status)


class GeminiProvider(BaseProvider):
    """Optional Google Gemini adapter (needs a valid AI Studio API key)."""

    name = "gemini"
    BASE = "https://generativelanguage.googleapis.com/v1beta/models"

    def __init__(self, api_key: str, model: str, extra: dict | None = None, limits: dict | None = None):
        super().__init__(model, extra, limits)
        self._key = api_key

    def complete(self, system: str, user: str, timeout: float, max_tokens: int) -> ProviderResult:
        body = {
            "systemInstruction": {"parts": [{"text": system}]},
            "contents": [{"role": "user", "parts": [{"text": user}]}],
            "generationConfig": {"temperature": 0.4, "maxOutputTokens": max_tokens, "responseMimeType": "application/json"},
        }
        request = urllib.request.Request(
            f"{self.BASE}/{self.model}:generateContent",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json", "x-goog-api-key": self._key},
        )
        started = time.monotonic()
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                payload = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", "ignore")[:200]
            status = exc.code
            if status == 429:
                raise ProviderError("daily_limit" if _daily(detail) else "rate_limit", detail, None, status) from exc
            if status in (401, 403):
                raise ProviderError("auth", detail, None, status) from exc
            if status == 404:
                raise ProviderError("model_missing", detail, None, status) from exc
            if status in (400, 422):
                raise ProviderError("bad_request", detail, None, status) from exc
            raise ProviderError("server", detail, None, status) from exc
        except TimeoutError as exc:
            raise ProviderError("timeout", str(exc)) from exc
        except (urllib.error.URLError, OSError) as exc:
            raise ProviderError("network", str(exc)) from exc
        except ValueError as exc:
            raise ProviderError("server", f"invalid JSON: {exc}") from exc

        try:
            text = payload["candidates"][0]["content"]["parts"][0]["text"].strip()
        except (KeyError, IndexError, TypeError, AttributeError):
            raise ProviderError("empty", "no candidate text") from None
        if not text:
            raise ProviderError("empty", "empty candidate")
        tokens = int((payload.get("usageMetadata") or {}).get("totalTokenCount", 0) or 0)
        return ProviderResult(text=text, tokens=tokens, latency_ms=int((time.monotonic() - started) * 1000))
