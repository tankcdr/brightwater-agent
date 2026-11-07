"""Decision clients for the `assess` step (Ch 6): options B, C and D.

AIP asks an injected decision client `system_one(state=..., questions=...)`
and expects TypeSafe's answer shapes back:

    noul:   {"type": "noul", "noul": p_yes}
    choice: {"type": "choice", "choice": label, "confidence": p, "probabilities": {label: p}}
    score:  {"type": "score", "score": level, "confidence": p, "probabilities": {level: p}, "legend": {...}}

Option A has no client: every decision pauses and the driving agent answers.
Option B is AIP's native TypeSafe client (`TYPESAFE_API_KEY`); nothing here.
Option C, `FrontierDecisionClient`, asks a frontier model for a probability
per option: self-reported, uncalibrated.
Option D, `EndpointDecisionClient`, asks our fine-tuned model on any
OpenAI-compatible server (mlx-lm on Apple Silicon, vLLM on NVIDIA) for one
token, reads the log-probabilities of the allowed answer letters, and
temperature-scales them with the calibration fitted on the validation split.
"""

from __future__ import annotations

import json
import math
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

ROOT = Path(__file__).resolve().parents[2]
LETTERS = "ABCDEFGHIJ"


# ------------------------------------------------------------------ question helpers
def q_dict(q: Any) -> dict[str, Any]:
    return q.model_dump(mode="json") if hasattr(q, "model_dump") else dict(q)


def options(q: dict[str, Any]) -> list[tuple[str, Any, str]]:
    """(letter, answer value, description) for each allowed answer, in a fixed order."""
    t = q["type"]
    crit = q.get("criteria")
    if t == "noul":
        crit = crit or {}
        vals = [(True, crit.get("true") or crit.get(True) or "yes"), (False, crit.get("false") or crit.get(False) or "no")]
    elif t == "choice":
        vals = list(crit.items())
    elif t == "score":
        vals = [(i, _text(c)) for i, c in enumerate(crit)]
    else:
        raise ValueError(f"unsupported question type {t!r}")
    return [(LETTERS[i], v, str(d)) for i, (v, d) in enumerate(vals)]


def _text(c: Any) -> str:
    if isinstance(c, dict):
        return str(c.get("text") or c.get("value") or c)
    return str(c)


def compact(state: dict[str, Any]) -> dict[str, Any]:
    """The case as the decision model sees it: figures only, short."""
    f = state["facts"]
    return {
        "dscr": round(float(f["dscr"]), 3), "group_exposure": round(float(f["group_exposure"])),
        "kyc_current": f["kyc_current"], "days_since_last_adverse_event": f["days_since_last_adverse_event"],
        "related_facilities": [{"stake_pct": round(float(r["owner_stake_pct"])), "days_past_due": int(r["days_past_due"])}
                               for r in f["related_facilities"]],
        "monthly_net_cash_flow": [round(float(x)) for x in state["monthly_net_cash_flow"]],
        "monthly_debt_service": round(float(state["monthly_debt_service"])),
        "rule_outcome": state["rule_outcome"], "rules_fired": [r["rule_id"] for r in state["fired"]],
    }


def prompt(state: dict[str, Any], name: str, q: dict[str, Any]) -> str:
    opts = "\n".join(f"{letter}) {desc}" for letter, _, desc in options(q))
    return (f"Question ({name}): {q.get('instructions', '').strip()}\nOptions:\n{opts}\n"
            f"Case: {json.dumps(compact(state), separators=(',', ':'))}\n"
            f"Answer with the letter only.")


def answer_shape(q: dict[str, Any], probs: dict[str, float]) -> dict[str, Any]:
    """TypeSafe's answer shape from a probability per letter."""
    opts = options(q)
    total = sum(probs.get(letter, 0.0) for letter, _, _ in opts) or 1.0
    p = {letter: probs.get(letter, 0.0) / total for letter, _, _ in opts}
    best = max(opts, key=lambda o: p[o[0]])
    if q["type"] == "noul":
        return {"type": "noul", "noul": round(p["A"], 6)}
    if q["type"] == "choice":
        return {"type": "choice", "choice": best[1], "confidence": round(p[best[0]], 6),
                "probabilities": {str(v): round(p[letter], 6) for letter, v, _ in opts}}
    return {"type": "score", "score": float(best[1]), "confidence": round(p[best[0]], 6),
            "probabilities": {int(v): round(p[letter], 6) for letter, v, _ in opts},
            "legend": {int(v): d for _, v, d in opts}}


@dataclass
class Result:
    answers: dict[str, Any]
    model: str
    usage: dict[str, Any] = field(default_factory=dict)

    def model_dump(self, mode: str = "json") -> dict[str, Any]:
        return {"answers": self.answers, "model": self.model, "usage": self.usage}


# ------------------------------------------------------------------ option D
def softmax_letters(top: list[dict[str, Any]], letters: list[str], temperature: float = 1.0) -> dict[str, float]:
    """Probabilities over the allowed letters from a top_logprobs list, temperature-scaled."""
    lp: dict[str, float] = {}
    for t in top:
        tok = str(t.get("token", "")).strip()
        if tok in letters and (tok not in lp or t["logprob"] > lp[tok]):
            lp[tok] = float(t["logprob"])
    floor = min(lp.values()) - 10 if lp else -30.0
    logits = {letter: lp.get(letter, floor) / temperature for letter in letters}
    m = max(logits.values())
    z = sum(math.exp(v - m) for v in logits.values())
    return {letter: math.exp(v - m) / z for letter, v in logits.items()}


def top_logprobs_of(body: dict[str, Any]) -> list[dict[str, Any]]:
    """The first generated token's alternatives, from either OpenAI response format."""
    lp = body["choices"][0].get("logprobs") or {}
    if lp.get("content"):                       # chat completions (mlx-lm, vLLM, OpenAI)
        return lp["content"][0].get("top_logprobs") or []
    if lp.get("top_logprobs"):                  # legacy completions (vLLM)
        return [{"token": k, "logprob": v} for k, v in lp["top_logprobs"][0].items()]
    return []


class EndpointDecisionClient:
    resolver_name = "endpoint"

    def __init__(self, base_url: str, model: str, calibration: dict[str, float] | None = None,
                 version: str | None = None, timeout: float = 30.0):
        self.base_url, self.model, self.calibration = base_url.rstrip("/"), model, calibration or {}
        self.version = version or model
        self.http = httpx.Client(timeout=timeout)
        self.seconds = 0.0

    def probabilities(self, text: str, letters: list[str], temperature: float) -> dict[str, float]:
        t0 = time.perf_counter()
        r = self.http.post(f"{self.base_url}/chat/completions", json={
            "model": self.model, "messages": [{"role": "user", "content": text}],
            "max_tokens": 1, "temperature": 0, "logprobs": True, "top_logprobs": 10})
        r.raise_for_status()
        self.seconds += time.perf_counter() - t0
        return softmax_letters(top_logprobs_of(r.json()), letters, temperature)

    def system_one(self, state: dict[str, Any], questions: dict[str, Any]) -> Result:
        st = state.get("state", state)
        answers = {}
        for name, q in questions.items():
            qd = q_dict(q)
            letters = [o[0] for o in options(qd)]
            answers[name] = answer_shape(qd, self.probabilities(prompt(st, name, qd), letters,
                                                                self.calibration.get(name, 1.0)))
        return Result(answers, self.version)


# ------------------------------------------------------------------ option C
class FrontierDecisionClient:
    """Asks a frontier model for a probability per option. Self-reported, so uncalibrated."""

    resolver_name = "endpoint"

    def __init__(self, model_name: str, callbacks: list[Any] | None = None):
        from .agent import chat_model
        self.model_name, self.model, self.callbacks = model_name, chat_model(model_name), callbacks or []

    def system_one(self, state: dict[str, Any], questions: dict[str, Any]) -> Result:
        st = state.get("state", state)
        qs = {name: q_dict(q) for name, q in questions.items()}
        listing = "\n\n".join(
            f"{name}: {q.get('instructions', '').strip()}\n" + "\n".join(f"{l}) {d}" for l, _, d in options(q))
            for name, q in qs.items())
        text = ("For each question about this loan case, give your probability for every option letter; each "
                "question's probabilities sum to 1. Return JSON only: {question: {letter: probability}}.\n\n"
                f"{listing}\n\nCase: {json.dumps(compact(st))}")
        msg = self.model.invoke([("user", text)], config={"callbacks": self.callbacks})
        from .procedure import _json_from
        raw = _json_from(msg.content if isinstance(msg.content, str) else str(msg.content))
        return Result({name: answer_shape(q, {k: float(v) for k, v in raw.get(name, {}).items()})
                       for name, q in qs.items()}, f"{self.model_name}:self-reported")


def client_for(spec: str) -> Any:
    """`frontier:<model>` (option C) or `local:<base_url>` (option D, reads decision_model/artifacts)."""
    from . import aip_patch
    aip_patch.apply()
    kind, _, rest = spec.partition(":")
    if kind == "frontier":
        return FrontierDecisionClient(rest)
    if kind == "local":
        art = ROOT / "decision_model" / "artifacts"
        meta = json.loads((art / "model.json").read_text()) if (art / "model.json").exists() else {}
        cal = json.loads((art / "calibration.json").read_text()) if (art / "calibration.json").exists() else {}
        return EndpointDecisionClient(rest or os.getenv("BRIGHTWATER_DM_URL", "http://localhost:8081/v1"),
                                      os.getenv("BRIGHTWATER_DM_SERVED_NAME") or meta.get("served_name", "default_model"), cal.get("temperature", {}),
                                      version=meta.get("version"))
    raise SystemExit(f"unknown decision model {spec!r}: use frontier:<model> or local:<url>")
