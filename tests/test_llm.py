import json
import threading
import time
import unittest
from types import SimpleNamespace

from flyflirt.llm import heuristic
from flyflirt.llm.providers import BaseProvider, ProviderError, ProviderResult
from flyflirt.llm.router import LLMRouter, extract_json, normalise

GOOD = {"warmth": 0.7, "humor": 0.4, "reciprocity": 0.6, "curiosity": 0.5, "disclosure": 0.2, "energy": 0.6,
        "tension": 0.05, "topic": "bread", "diary": "the fly hums", "tip": "ask a follow-up"}


def cfg(**kw):
    base = dict(LLM_ENABLED=True, LLM_TIMEOUT_S=2.0, LLM_TOTAL_BUDGET_S=6.0, LLM_MAX_CONCURRENCY=4,
                LLM_QUEUE_WAIT_S=0.2, LLM_QUOTA_MARGIN=0.92)
    base.update(kw)
    return SimpleNamespace(**base)


class Fake(BaseProvider):
    name = "fake"

    def __init__(self, model, script, limits=None, delay=0.0):
        super().__init__(model, {"reasoning_effort": "low"}, limits or {})
        self.script = list(script)
        self.calls = 0
        self.delay = delay

    def complete(self, system, user, timeout, max_tokens):
        self.calls += 1
        if self.delay:
            time.sleep(self.delay)
        step = self.script.pop(0) if len(self.script) > 1 else self.script[0]
        if isinstance(step, Exception):
            raise step
        text = json.dumps(GOOD) if step == "ok" else step  # "ok" means: return valid JSON
        return ProviderResult(text=text, tokens=400, latency_ms=5, limits={})


class RouterTests(unittest.TestCase):
    def test_uses_primary_when_healthy(self):
        a, b = Fake("a", ["ok"]), Fake("b", ["ok"])
        r = LLMRouter([a, b], cfg())
        result = r.analyse("hello there", "", "flirt")
        self.assertFalse(result.degraded)
        self.assertEqual(result.provider, "fake:a")
        self.assertEqual((a.calls, b.calls), (1, 0))
        self.assertEqual(result.params["warmth"], 0.7)

    def test_falls_through_on_rate_limit_and_remembers_cooldown(self):
        a = Fake("a", [ProviderError("rate_limit", "slow down", retry_after=30, status=429)])
        b = Fake("b", ["ok"])
        r = LLMRouter([a, b], cfg())
        first = r.analyse("hello", "", "flirt")
        self.assertEqual(first.provider, "fake:b")
        second = r.analyse("hello again", "", "flirt")
        self.assertEqual(second.provider, "fake:b")
        self.assertEqual(a.calls, 1, "a cooling-down model must not be hammered")
        state = r.status()["chain"][0]
        self.assertEqual(state["state"], "cooling")
        self.assertGreater(state["cooldown_s"], 20)

    def test_daily_limit_opens_breaker_until_next_day(self):
        a = Fake("a", [ProviderError("daily_limit", "tokens per day (TPD)", retry_after=None, status=429)])
        r = LLMRouter([a], cfg())
        r.analyse("x1", "", "friends")
        self.assertGreater(r.status()["chain"][0]["cooldown_s"], 60)

    def test_all_providers_failing_falls_back_to_local_scorer(self):
        errs = [ProviderError("server", "boom", status=500)]
        r = LLMRouter([Fake("a", errs), Fake("b", [ProviderError("timeout", "slow")])], cfg())
        result = r.analyse("haha you're funny", "tell me a joke", "friends")
        self.assertTrue(result.degraded)
        self.assertEqual(result.provider, "local")
        self.assertEqual(set(result.params), set(heuristic.PARAM_NAMES))
        self.assertEqual(r.served["fallback"], 1)

    def test_invalid_json_moves_to_next_provider(self):
        a, b = Fake("a", ["I am sorry, I cannot do that"]), Fake("b", ["ok"])
        result = LLMRouter([a, b], cfg()).analyse("hello", "", "flirt")
        self.assertEqual(result.provider, "fake:b")

    def test_bad_request_retries_once_without_optional_params(self):
        a = Fake("a", [ProviderError("bad_request", "unsupported parameter", status=400), "ok"])
        result = LLMRouter([a], cfg()).analyse("hello", "", "flirt")
        self.assertFalse(result.degraded)
        self.assertEqual(a.extra, {})
        self.assertEqual(a.calls, 2)

    def test_daily_token_budget_skips_model_before_it_can_429(self):
        a = Fake("a", ["ok"], limits={"tpd": 1000, "rpm": 100, "tpm": 100000, "rpd": 1000})
        b = Fake("b", ["ok"])
        r = LLMRouter([a, b], cfg())
        r.states[0].tokens_today = 950  # nearly out of quota
        r.states[0].day = time.strftime("%Y-%m-%d", time.gmtime())
        result = r.analyse("hello", "", "flirt")
        self.assertEqual(result.provider, "fake:b")
        self.assertEqual(a.calls, 0)

    def test_local_rpm_limit_protects_the_quota(self):
        a = Fake("a", ["ok"], limits={"rpm": 5, "tpm": 10**6, "tpd": 10**7, "rpd": 10**6})
        b = Fake("b", ["ok"])
        r = LLMRouter([a, b], cfg())
        providers = [r.analyse(f"message {i}", "", "flirt").provider for i in range(8)]
        self.assertGreater(providers.count("fake:a"), 0)
        self.assertGreater(providers.count("fake:b"), 0)
        self.assertLessEqual(a.calls, 5)

    def test_cache_returns_identical_results_without_calling_a_model(self):
        a = Fake("a", ["ok"])
        r = LLMRouter([a], cfg())
        r.analyse("same text", "prev", "flirt")
        cached = r.analyse("same text", "prev", "flirt")
        self.assertEqual(cached.provider, "cache")
        self.assertEqual(a.calls, 1)

    def test_busy_router_degrades_instead_of_queueing_forever(self):
        slow = Fake("slow", ["ok"], delay=0.6)
        r = LLMRouter([slow], cfg(LLM_MAX_CONCURRENCY=1, LLM_QUEUE_WAIT_S=0.05))
        out = []
        threads = [threading.Thread(target=lambda i=i: out.append(r.analyse(f"m{i}", "", "flirt"))) for i in range(3)]
        started = time.time()
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertLess(time.time() - started, 1.6)
        self.assertTrue(any(o.degraded and "busy" in o.note for o in out))
        self.assertTrue(any(not o.degraded for o in out))

    def test_time_budget_prevents_a_slow_chain_from_stalling_chat(self):
        chain = [Fake(f"m{i}", ["ok"], delay=0.7) for i in range(4)]
        r = LLMRouter(chain, cfg(LLM_TOTAL_BUDGET_S=1.0, LLM_TIMEOUT_S=0.5))
        started = time.time()
        r.analyse("hello", "", "flirt")
        self.assertLess(time.time() - started, 2.5)

    def test_disabled_router_uses_local_scorer(self):
        result = LLMRouter([Fake("a", ["ok"])], cfg(LLM_ENABLED=False)).analyse("hi", "", "flirt")
        self.assertTrue(result.degraded)

    def test_status_never_exposes_secrets(self):
        status = json.dumps(LLMRouter([Fake("a", ["ok"])], cfg()).status())
        self.assertNotIn("key", status.lower())


class ParsingTests(unittest.TestCase):
    def test_extract_json_handles_fences_and_trailing_commas(self):
        raw = "```json\n{\"warmth\": 0.5, \"humor\": 0.1,}\n```"
        self.assertEqual(extract_json(raw)["warmth"], 0.5)

    def test_extract_json_rejects_prose(self):
        with self.assertRaises(ValueError):
            extract_json("no json here")

    def test_normalise_clamps_and_fills_missing_values(self):
        fallback = heuristic.score_text("hello there", "", "friends")
        params, topic, diary, tip = normalise({"warmth": 7, "humor": -3, "topic": "x" * 99, "diary": "  hi\nthere  "}, fallback)
        self.assertEqual(params["warmth"], 1.0)
        self.assertEqual(params["humor"], 0.0)
        self.assertEqual(params["curiosity"], fallback["params"]["curiosity"])
        self.assertLessEqual(len(topic), 24)
        self.assertEqual(diary, "hi there")
        self.assertTrue(tip)


class HeuristicTests(unittest.TestCase):
    def s(self, text, prev=""):
        return heuristic.score_text(text, prev, "flirt")

    def test_scores_are_in_range_and_complete(self):
        out = self.s("Hey! How was your day? 😊")
        self.assertEqual(set(out["params"]), set(heuristic.PARAM_NAMES))
        self.assertTrue(all(0.0 <= v <= 1.0 for v in out["params"].values()))

    def test_humor_and_warmth_are_detected(self):
        self.assertGreater(self.s("haha lol that's hilarious 😂")["params"]["humor"], 0.5)
        self.assertGreater(self.s("I really appreciate you, you're so sweet ❤️")["params"]["warmth"], 0.5)

    def test_questions_drive_curiosity_and_reciprocity(self):
        q = self.s("What about you? Where did you grow up?", "I grew up near the sea")["params"]
        self.assertGreater(q["curiosity"], 0.6)
        self.assertGreater(q["reciprocity"], 0.4)

    def test_dismissive_short_reply_reads_as_tension(self):
        out = self.s("k", "I had such a lovely time talking with you")["params"]
        self.assertGreater(out["tension"], 0.4)
        self.assertLess(out["warmth"], 0.2)

    def test_overlap_with_partner_message_raises_reciprocity(self):
        prev = "I love hiking in the mountains on weekends"
        echoing = self.s("Hiking in the mountains is my favourite too", prev)["params"]["reciprocity"]
        unrelated = self.s("The stock market closed higher today", prev)["params"]["reciprocity"]
        self.assertGreater(echoing, unrelated)

    def test_deterministic(self):
        self.assertEqual(self.s("same text")["diary"], self.s("same text")["diary"])


if __name__ == "__main__":
    unittest.main()
