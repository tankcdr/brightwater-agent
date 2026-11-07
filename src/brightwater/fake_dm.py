"""A stand-in decision model server, for tests and for checking the plumbing with no GPU.

It speaks the slice of the OpenAI chat completions API the endpoint resolver
uses (one token, `logprobs`, `top_logprobs`) and answers the way a
well-trained classifier would: it reads the case out of the prompt, applies
the rubric, and puts most of the probability on the rubric's letter, with
seeded noise. Its numbers are never results; they prove the resolver,
calibration and AIP attribution work end to end.

    python -m brightwater.fake_dm --port 8081
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from . import rubric

LETTERS = "ABCDEFGHIJ"


WRONG_EVERY = 17   # one prompt in WRONG_EVERY is answered wrongly; lower means a worse model


def _answer(prompt: str, wrong_every: int = WRONG_EVERY) -> tuple[list[str], str, float]:
    name = re.search(r"Question \((\w+)\)", prompt).group(1)
    letters = re.findall(r"^([A-J])\) ", prompt, re.M)
    case = json.loads(re.search(r"Case: (\{.*\})", prompt).group(1))
    state = {"facts": {"dscr": case["dscr"], "group_exposure": case["group_exposure"], "kyc_current": case["kyc_current"],
                       "days_since_last_adverse_event": case["days_since_last_adverse_event"],
                       "related_facilities": [{"owner_stake_pct": r["stake_pct"], "days_past_due": r["days_past_due"]}
                                              for r in case["related_facilities"]]},
             "monthly_net_cash_flow": case["monthly_net_cash_flow"], "monthly_debt_service": case["monthly_debt_service"]}
    lab = rubric.label(state)[name]
    if name == "cash_flow_stable":
        idx = 0 if lab else 1
    elif name == "risk_tier":
        idx = int(lab)
    else:
        idx = ["approve", "approve_with_conditions", "decline"].index(lab)
    # Seeded per prompt: the same prompt always gets the same answer.
    h = int(hashlib.sha256(prompt.encode()).hexdigest(), 16)
    margin = 0.2 + (h % 480) / 100.0          # 0.2 to 5.0 nats: sometimes unsure, mostly sure
    if h % wrong_every == 0:                  # about 6% wrong by default, like a real small model
        idx = (idx + 1) % len(letters)
    return letters, letters[idx], margin


class Handler(BaseHTTPRequestHandler):
    wrong_every = WRONG_EVERY

    def log_message(self, *a: Any) -> None:
        pass

    def do_POST(self) -> None:  # noqa: N802
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        prompt = body["messages"][-1]["content"]
        letters, best, margin = _answer(prompt, self.wrong_every)
        top = [{"token": l, "logprob": (0.0 if l == best else -margin - i * 0.3)} for i, l in enumerate(letters)]
        z = math.log(sum(math.exp(t["logprob"]) for t in top))
        top = [{**t, "logprob": t["logprob"] - z} for t in top]
        out = {"id": "fake", "object": "chat.completion", "model": body.get("model"),
               "choices": [{"index": 0, "message": {"role": "assistant", "content": best},
                            "logprobs": {"content": [{"token": best, "logprob": next(t["logprob"] for t in top if t["token"] == best),
                                                      "top_logprobs": top}]}, "finish_reason": "length"}],
               "usage": {"prompt_tokens": len(prompt) // 4, "completion_tokens": 1}}
        data = json.dumps(out).encode()
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)


def serve(port: int = 8081, wrong_every: int = WRONG_EVERY) -> ThreadingHTTPServer:
    handler = type("H", (Handler,), {"wrong_every": wrong_every})
    return ThreadingHTTPServer(("127.0.0.1", port), handler)


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8081)
    serve(ap.parse_args().port).serve_forever()
