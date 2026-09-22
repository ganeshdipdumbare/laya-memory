"""Minimal laya client. Stdlib only — no pip install in the agent's path.

Server: https://github.com/ganeshdipdumbare/laya
  GET  /healthz     503 while loading, 200 once warm
  POST /v1/predict  {state, questions, model?} -> {answers, checkpoint, latency_ms}
"""

import json
import os
import urllib.error
import urllib.request

BASE = os.environ.get("LAYA_URL", "http://127.0.0.1:8080").rstrip("/")
MODEL = os.environ.get("LAYA_SKILLS_MODEL")  # e.g. "auto" if multilingual is loaded
TIMEOUT = float(os.environ.get("LAYA_TIMEOUT", "30"))

# Server-side truncation limits (English checkpoint). State is cut from the END,
# so decisive material goes first in every state we build.
STATE_TOKEN_BUDGET = 512
CHARS_PER_TOKEN = 4


class LayaUnavailable(RuntimeError):
    """laya is down, still loading, or rejected the request."""


def clip(text, tokens):
    """Trim to an approximate token budget. Cheap and deliberately conservative."""
    text = " ".join((text or "").split())
    limit = int(tokens * CHARS_PER_TOKEN)
    return text if len(text) <= limit else text[: limit - 1] + "\u2026"


def healthy():
    try:
        with urllib.request.urlopen(BASE + "/healthz", timeout=5) as resp:
            return resp.status == 200
    except Exception:
        return False


def predict(state, questions, model=None):
    """One forward pass. Independent questions batch into a single request."""
    payload = {"state": state, "questions": questions}
    chosen = model or MODEL
    if chosen:
        payload["model"] = chosen
    body = json.dumps(payload).encode()
    if len(body) > 1_000_000:  # server body cap is 1 MiB
        raise LayaUnavailable("payload exceeds laya's 1 MiB body cap")
    req = urllib.request.Request(
        BASE + "/v1/predict", data=body, headers={"Content-Type": "application/json"}
    )
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT) as resp:
            return json.load(resp)
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", "replace")[:400]
        if exc.code == 422:
            raise LayaUnavailable(
                "422 from laya \u2014 a question's instructions + criteria exceeded "
                "192 tokens. Shorten them. " + detail
            ) from exc
        raise LayaUnavailable("laya returned %s: %s" % (exc.code, detail)) from exc
    except urllib.error.URLError as exc:
        raise LayaUnavailable(
            "cannot reach laya at %s (%s). Start it with `docker compose up -d` "
            "in the laya repo, then wait for /healthz to return 200." % (BASE, exc.reason)
        ) from exc


def noul(answers, key):
    """P(true) for a noul question. 0.5 is a coin flip."""
    return float(answers[key]["noul"])


def choice(answers, key):
    """(label, probability_of_that_label).

    Gate on probabilities[choice], NOT on `confidence` — confidence describes how
    peaked the distribution is, which is a different question.
    """
    ans = answers[key]
    label = ans["choice"]
    return label, float(ans["probabilities"][label])


def score(answers, key):
    return float(answers[key]["score"])
