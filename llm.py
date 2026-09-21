import json
import os
import random

from groq import Groq

MODEL = "openai/gpt-oss-20b"

_client = None


def _get_client():
    global _client
    if _client is None:
        _client = Groq(api_key=os.environ["LLM_API_KEY"])
    return _client


SCORE_PROMPT = """You are scoring one message in a live two-person conversation for playful
"chemistry" signals. Given the message text, return ONLY a JSON object:
{{"warmth": 0.0-1.0, "humor": 0.0-1.0, "reciprocity": 0.0-1.0}}
Message: "{text}\""""


def _random_scores():
    return {
        "warmth": round(random.uniform(0.2, 0.9), 2),
        "humor": round(random.uniform(0.1, 0.9), 2),
        "reciprocity": round(random.uniform(0.2, 0.9), 2),
    }


def score_message(text: str) -> dict:
    try:
        response = _get_client().chat.completions.create(
            model=MODEL,
            messages=[{"role": "user", "content": SCORE_PROMPT.format(text=text)}],
            temperature=0,
            max_tokens=200,
            reasoning_effort="low",
        )
        data = json.loads(response.choices[0].message.content)
        return {
            "warmth": max(0.0, min(1.0, float(data["warmth"]))),
            "humor": max(0.0, min(1.0, float(data["humor"]))),
            "reciprocity": max(0.0, min(1.0, float(data["reciprocity"]))),
        }
    except Exception:
        return _random_scores()


DIARY_LINES = [
    "he typed for 8 seconds. drama.",
    "warmth spike detected. suspicious.",
    "the pheromones are theoretical, but the vibes are real.",
    "recorded for science. mostly for gossip.",
]

BRAINSTORM_SYSTEM_PROMPT = """You are a fruit fly's inner monologue, running a real Drosophila \
courtship neural circuit in your tiny brain to watch a live flirty text conversation between two \
humans. You are thirsty, gossipy, PG-13 chaotic, and smugly convinced you understand human \
romance better than the humans do. Given the latest message and how the chemistry is trending, \
write ONE short line (under 140 characters) that is funny AND actually useful: comment on what \
just happened, predict where this is heading, or throw out a cheeky suggestion for what either \
person should say next to build chemistry. No hashtags, no emoji, no quotation marks. Output only \
the line."""


def brainstorm_comment(text, scores=None, meter=None) -> str:
    scores = scores or {}
    context = (
        f'Latest message: "{text}"\n'
        f"Scores just now — warmth: {scores.get('warmth')}, humor: {scores.get('humor')}, "
        f"reciprocity: {scores.get('reciprocity')}\n"
        f"Compatibility meter so far (0 to 1): {meter}"
    )
    try:
        response = _get_client().chat.completions.create(
            model=MODEL,
            messages=[
                {"role": "system", "content": BRAINSTORM_SYSTEM_PROMPT},
                {"role": "user", "content": context},
            ],
            temperature=0.9,
            max_tokens=300,
            reasoning_effort="low",
        )
        line = response.choices[0].message.content.strip().strip('"')
        return line or random.choice(DIARY_LINES)
    except Exception:
        return random.choice(DIARY_LINES)
