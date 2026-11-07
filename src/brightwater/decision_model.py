"""Chapter 6: own the decision model.

The pipeline, one command each:

    brightwater dm dataset          # assess-step states for every application, split
    brightwater dm label            # rubric labels: frontier:<model> (default) or rubric
    brightwater dm spot-check       # a person checks a sample of labels
    brightwater dm export           # chat-format JSONL for MLX-LM (or TRL) fine-tuning
    (train)                         # mlx_lm.lora on Apple Silicon; see docs/ch6
    brightwater dm calibrate        # fit a temperature per question on the validation split
    brightwater dm evaluate         # options A to D on the held-out split

Splits are by application, never by question, so no held-out case leaks into
training through a sibling question. APP-H6 is always held out.
"""

from __future__ import annotations

import hashlib
import json
import math
import random
import statistics
import time
from pathlib import Path
from typing import Any, Callable

from . import policy, procedure_steps, resolver, rubric, semantic

ROOT = Path(__file__).resolve().parents[2]
DATA = ROOT / "decision_model" / "data"
ART = ROOT / "decision_model" / "artifacts"
RUBRIC = ROOT / "decision_model" / "rubric.md"


def questions() -> dict[str, dict[str, Any]]:
    """The assess step's questions, read from the skill so the model answers exactly what the procedure asks."""
    from aip_spec import load_skill

    from .procedure import SKILL_DIR
    spec = load_skill(str(SKILL_DIR)).spec
    step = next(s for s in spec.steps if s.name == "assess")
    return {name: q.model_dump(mode="json") for name, q in step.questions.items()}


def split_of(app_id: str, held_out: set[str]) -> str:
    if app_id in held_out:
        return "test"
    h = int(hashlib.sha256(app_id.encode()).hexdigest(), 16) % 100
    return "test" if h < 20 else "valid" if h < 32 else "train"


# ------------------------------------------------------------------ dataset
def build_states() -> list[dict[str, Any]]:
    """The exact state the `assess` step sees, for every application, computed in bulk."""
    from . import db

    model = semantic.load(semantic.ROOT / "semantic" / "brightwater.ossie.yaml")
    with db.session() as conn:
        ids = [r[0] for r in conn.run("SELECT application_id FROM credit.application ORDER BY 1")]
        months = db.rows(conn, semantic.direct_sql(model, "application_month", metrics=False), ids=ids)
    facts = semantic.facts_direct(model, ids)
    series: dict[str, list[tuple[str, float, float]]] = {}
    for m in months:
        series.setdefault(m["application_id"], []).append((str(m["month"]), float(m["net_cash_flow"]), float(m["debt_service"])))
    rules_asset = {"policy": (procedure_steps_skill_asset())}
    meta = json.loads((ROOT / "fixture" / "out" / "manifest.json").read_text()) if (ROOT / "fixture" / "out" / "manifest.json").exists() else {}
    held = set(meta.get("held_out", ["APP-H6"]))
    out = []
    for i in ids:
        s = sorted(series.get(i, []))
        state = {"application_id": i, "facts": facts[i].to_dict(),
                 "monthly_net_cash_flow": [round(x[1], 2) for x in s],
                 "monthly_debt_service": round(s[0][2], 2) if s else 0.0}
        state.update(procedure_steps.apply_policy({"facts": state["facts"]}, rules_asset))
        out.append({"application_id": i, "split": split_of(i, held), "state": state})
    return out


def procedure_steps_skill_asset() -> str:
    from .procedure import SKILL_DIR
    return (SKILL_DIR / "assets" / "policy.json").read_text()


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(r, default=str) + "\n" for r in rows))


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines()] if path.exists() else []


# ------------------------------------------------------------------ labels
def frontier_labeller(model_name: str) -> Callable[[dict[str, Any]], dict[str, Any]]:
    from .agent import chat_model
    from .procedure import _json_from

    model = chat_model(model_name)
    rubric_text = RUBRIC.read_text()
    qs = questions()

    def label(state: dict[str, Any]) -> dict[str, Any]:
        text = (f"{rubric_text}\n\nApply the rubric above to this case. Return JSON only with keys "
                f"risk_tier (0-3), cash_flow_stable (true/false) and recommendation "
                f"({'|'.join(qs['recommendation']['criteria'])}).\n\nCase: {json.dumps(resolver.compact(state))}")
        msg = model.invoke([("user", text)])
        raw = _json_from(msg.content if isinstance(msg.content, str) else str(msg.content))
        return {"risk_tier": int(raw["risk_tier"]), "cash_flow_stable": bool(raw["cash_flow_stable"]),
                "recommendation": str(raw["recommendation"])}
    return label


def label_all(labeller: str = "rubric", limit: int | None = None, workers: int = 8) -> dict[str, Any]:
    """Label every state. A frontier labeller is paid per call, so each label is saved as it
    arrives (`labels.cache.jsonl`) and a re-run reuses it, and one bad reply is retried rather than
    losing the whole run."""
    import threading
    from concurrent.futures import ThreadPoolExecutor

    rows = read_jsonl(DATA / "states.jsonl")[: limit or None]
    fn = (lambda s: rubric.label(s)) if labeller == "rubric" else frontier_labeller(labeller.split(":", 1)[1])
    cache_path = DATA / "labels.cache.jsonl"
    cache = {c["application_id"]: c["labels"] for c in read_jsonl(cache_path) if c["labeller"] == labeller}
    lock = threading.Lock()

    def one(r: dict[str, Any]) -> dict[str, Any]:
        if r["application_id"] in cache:
            return cache[r["application_id"]]
        for attempt in range(3):
            try:
                lab = fn(r["state"])
                break
            except Exception as e:  # noqa: BLE001  (a malformed reply or a transient API error)
                if attempt == 2:
                    raise RuntimeError(f"labelling {r['application_id']} failed 3 times; labels so far are "
                                       f"kept in {cache_path} and reused on the next run: {e}") from e
        if labeller != "rubric":
            with lock, cache_path.open("a") as f:
                f.write(json.dumps({"application_id": r["application_id"], "labeller": labeller, "labels": lab}) + "\n")
        return lab

    with ThreadPoolExecutor(workers) as pool:
        labels = list(pool.map(one, rows))
    out, agree = [], 0
    for r, lab in zip(rows, labels):
        ref = rubric.label(r["state"])
        agree += lab == ref
        out.append({"application_id": r["application_id"], "split": r["split"], "labeller": labeller,
                    "labels": lab, "rubric_reference": ref})
    write_jsonl(DATA / "labels.jsonl", out)
    return {"labelled": len(out), "labeller": labeller, "agrees_with_reference_rubric": agree}


def spot_check(n: int = 30, seed: int = 11, answer: Callable[[dict[str, Any]], dict[str, Any]] | None = None) -> dict[str, Any]:
    """A person reviews a random sample of labels; corrections override the label in training."""
    labels = read_jsonl(DATA / "labels.jsonl")
    states = {r["application_id"]: r["state"] for r in read_jsonl(DATA / "states.jsonl")}
    sample = random.Random(seed).sample(labels, min(n, len(labels)))
    checks = []
    for row in sample:
        case = resolver.compact(states[row["application_id"]])
        verdict = answer(row) if answer else _console_check(row, case)
        checks.append({"application_id": row["application_id"], **verdict})
    write_jsonl(DATA / "spot_checks.jsonl", checks)
    return {"checked": len(checks), "accepted": sum(c["accepted"] for c in checks)}


def _console_check(row: dict[str, Any], case: dict[str, Any]) -> dict[str, Any]:
    print(json.dumps({"case": case, "labels": row["labels"]}, indent=1))
    ok = input("Accept these labels? [Y/n] ").strip().lower() != "n"
    fix = {} if ok else json.loads(input("Corrected labels as JSON: "))
    return {"accepted": ok, "corrected": fix, "checked_by": input("Your user id: ").strip() or "reviewer"}


def final_labels() -> dict[str, dict[str, Any]]:
    """Labels after spot-check corrections, plus every human answer at a review pause."""
    out = {r["application_id"]: dict(r["labels"]) for r in read_jsonl(DATA / "labels.jsonl")}
    for c in read_jsonl(DATA / "spot_checks.jsonl"):
        if not c["accepted"]:
            out[c["application_id"]].update(c["corrected"])
    from .procedure import review_answers_path
    for r in read_jsonl(review_answers_path()):
        if r.get("answered_by", "").startswith("user:") and r["application_id"] in out:
            out[r["application_id"]].update(r["final"])
    return out


# ------------------------------------------------------------------ fine-tuning data
def export(out_dir: Path | None = None) -> dict[str, int]:
    """One chat example per (application, question): the prompt the resolver sends, the answer letter."""
    out_dir = out_dir or DATA / "mlx"
    qs = questions()
    labels = final_labels()
    rows = {"train": [], "valid": [], "test": []}
    for r in read_jsonl(DATA / "states.jsonl"):
        lab = labels.get(r["application_id"])
        if not lab:
            continue
        for name, q in qs.items():
            letter = next(l for l, v, _ in resolver.options(q) if v == lab[name])
            rows[r["split"]].append({"messages": [{"role": "user", "content": resolver.prompt(r["state"], name, q)},
                                                  {"role": "assistant", "content": letter}],
                                     "application_id": r["application_id"], "question": name})
    for split, items in rows.items():
        write_jsonl(out_dir / f"{split}.jsonl", [{"messages": x["messages"]} for x in items])
        write_jsonl(out_dir / f"{split}.meta.jsonl", [{"application_id": x["application_id"], "question": x["question"]}
                                                      for x in items])
    return {k: len(v) for k, v in rows.items()}


# ------------------------------------------------------------------ calibration
def _nll(logps: list[dict[str, float]], gold: list[str], t: float) -> float:
    total = 0.0
    for lp, g in zip(logps, gold):
        m = max(v / t for v in lp.values())
        z = sum(math.exp(v / t - m) for v in lp.values())
        total -= lp[g] / t - m - math.log(z)
    return total / max(len(gold), 1)


def fit_temperature(logps: list[dict[str, float]], gold: list[str]) -> float:
    """Temperature scaling (Guo et al., 2017): one scalar per question, minimizing validation NLL."""
    lo, hi = 0.05, 20.0
    for _ in range(80):  # golden-section search on log(T)
        a, b = math.log(lo), math.log(hi)
        c, d = b - (b - a) / 1.618, a + (b - a) / 1.618
        if _nll(logps, gold, math.exp(c)) < _nll(logps, gold, math.exp(d)):
            hi = math.exp(d)
        else:
            lo = math.exp(c)
    return round(math.sqrt(lo * hi), 4)


def ece(conf: list[float], correct: list[bool], bins: int = 10) -> float:
    """Expected calibration error: the gap between confidence and accuracy, weighted by bin size."""
    n = len(conf)
    if not n:
        return float("nan")
    total = 0.0
    for b in range(bins):
        idx = [i for i, c in enumerate(conf) if b / bins < c <= (b + 1) / bins or (b == 0 and c == 0)]
        if idx:
            total += len(idx) / n * abs(statistics.fmean(correct[i] for i in idx) - statistics.fmean(conf[i] for i in idx))
    return round(total, 4)


def calibrate(client: resolver.EndpointDecisionClient, out: Path | None = None) -> dict[str, Any]:
    """Raw letter log-probabilities on the validation split, then one temperature per question."""
    qs = questions()
    labels = final_labels()
    states = [r for r in read_jsonl(DATA / "states.jsonl") if r["split"] == "valid"]
    temps, report = {}, {}
    for name, q in qs.items():
        letters = [o[0] for o in resolver.options(q)]
        logps, gold = [], []
        for r in states:
            p = client.probabilities(resolver.prompt(r["state"], name, q), letters, 1.0)
            logps.append({k: math.log(max(v, 1e-12)) for k, v in p.items()})
            gold.append(next(l for l, v, _ in resolver.options(q) if v == labels[r["application_id"]][name]))
        t = fit_temperature(logps, gold)
        before = ece([max(math.exp(v) for v in lp.values()) for lp in logps],
                     [max(lp, key=lp.get) == g for lp, g in zip(logps, gold)])
        scaled = [{k: v / t for k, v in lp.items()} for lp in logps]
        probs = [{k: math.exp(v - max(s.values())) for k, v in s.items()} for s in scaled]
        probs = [{k: v / sum(p.values()) for k, v in p.items()} for p in probs]
        after = ece([max(p.values()) for p in probs], [max(p, key=p.get) == g for p, g in zip(probs, gold)])
        temps[name] = t
        report[name] = {"temperature": t, "nll_before": round(_nll(logps, gold, 1.0), 4),
                        "nll_after": round(_nll(logps, gold, t), 4), "ece_before": before, "ece_after": after,
                        "n": len(gold)}
    out = out or ART / "calibration.json"
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps({"temperature": temps, "report": report}, indent=2) + "\n")
    return report


# ------------------------------------------------------------------ evaluation, options A to D
def evaluate(options_: dict[str, Any], repeats: int = 3, stability_n: int = 20, limit: int | None = None) -> dict[str, Any]:
    """Agreement with labels, ECE, stability, pause share, latency and cost, on the held-out split.

    `options_` maps a label ("A", "B", ...) to a decision client with `system_one`, or, for option A,
    to a callable(state, questions) -> answers dict (no probabilities).
    """
    from aip.model.steps import Decision, flag

    from .procedure import SKILL_DIR
    from aip_spec import load_skill
    spec = load_skill(str(SKILL_DIR)).spec
    step = next(s for s in spec.steps if s.name == "assess")
    thresholds = dict(step.thresholds or {})
    qs = questions()
    labels = final_labels()
    test = [r for r in read_jsonl(DATA / "states.jsonl") if r["split"] == "test"][: limit or None]
    results: dict[str, Any] = {}
    for opt, client in options_.items():
        agree = {n: 0 for n in qs}
        conf: dict[str, list[float]] = {n: [] for n in qs}
        correct: dict[str, list[bool]] = {n: [] for n in qs}
        paused = 0
        t0 = time.perf_counter()
        for r in test:
            if callable(client) and not hasattr(client, "system_one"):
                answers, raw = client(r["state"], qs), None
                paused += 1                               # option A: every decision pauses
            else:
                raw = client.system_one(state={"state": r["state"]}, questions=qs).model_dump()["answers"]
                answers = {n: Decision.collapse(a) for n, a in raw.items()}
                paused += bool(flag(raw, thresholds))
            for n in qs:
                ok = answers[n] == labels[r["application_id"]][n]
                agree[n] += ok
                if raw:
                    c = raw[n]["noul"] if raw[n]["type"] == "noul" else raw[n]["confidence"]
                    c = max(c, 1 - c) if raw[n]["type"] == "noul" else c
                    conf[n].append(c)
                    correct[n].append(ok)
        seconds = time.perf_counter() - t0
        stab = None
        if hasattr(client, "system_one") and repeats > 1:
            spreads = []
            for r in test[:stability_n]:
                runs = [client.system_one(state={"state": r["state"]}, questions=qs).model_dump()["answers"]
                        for _ in range(repeats)]
                for n in qs:
                    vals = [a[n]["noul"] if a[n]["type"] == "noul" else a[n]["confidence"] for a in runs]
                    spreads.append(max(vals) - min(vals))
            stab = round(max(spreads), 4) if spreads else None
        results[opt] = {
            "n": len(test),
            "agreement": {n: round(agree[n] / len(test), 4) for n in qs},
            "ece": {n: (ece(conf[n], correct[n]) if conf[n] else None) for n in qs},
            "max_confidence_spread_across_repeats": stab,
            "pause_share": round(paused / len(test), 4),
            "seconds_per_decision": round(seconds / len(test), 3),
            # Option A is a plain callable: the driving model answers at a pause, which AIP records as
            # `manual`. Option B is AIP's own TypeSafe client, which carries no resolver_name.
            "resolver": getattr(client, "resolver_name",
                                "typesafe" if hasattr(client, "system_one") else "manual"),
        }
    ART.mkdir(parents=True, exist_ok=True)
    (ART / "evaluation.json").write_text(json.dumps(results, indent=2) + "\n")
    return results


def policy_version() -> str:
    return policy.policy_version()
