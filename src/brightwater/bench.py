"""The measurement plan: one results table that grows across the series.

Every run, in every configuration, appends one line to `results/runs.jsonl`.
The table in `results/RESULTS.md` is computed from those lines alone, so any
number in the write-up can be traced to the runs behind it.

Per run we record, from the run's own OpenTelemetry spans:

* wall clock: prompt to filed decision (the root span);
* model time: the sum of model-call spans; model turns; tokens; list-price cost;
* the filed outcome, and whether it agrees with the deterministic policy
  engine on the same governed facts;
* linter rejections before the filing passed;
* the tool sequence and the records cited, for run-to-run consistency.
"""

from __future__ import annotations

import json
import os
import statistics
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml

from . import policy, tracing

ROOT = Path(__file__).resolve().parents[2]
RESULTS = Path(os.getenv("BRIGHTWATER_RESULTS", ROOT / "results"))
RUNS = RESULTS / "runs.jsonl"
TABLE = RESULTS / "RESULTS.md"
PRICES = ROOT / "results" / "prices.yaml"


def cost(model: str, input_tokens: int, output_tokens: int, cache_read: int = 0) -> float | None:
    prices = yaml.safe_load(PRICES.read_text())
    p = prices.get(model)
    if not p:
        return None
    fresh = max(input_tokens - cache_read, 0)
    return (fresh * p["input"] + cache_read * p["cache_read"] + output_tokens * p["output"]) / 1_000_000


def expected(application_id: str) -> policy.Decision:
    from . import metrics_cube
    return policy.evaluate(metrics_cube.facts_for(application_id))


def record(ctx: Any, spans_path: Path | None = None, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    spans = tracing.read_spans(spans_path or tracing.spans_file(), ctx.trace_id or "")
    root = next((s for s in spans if s["name"].startswith("invoke_agent") or s["name"].startswith("run_procedure")), None)
    chats = [s for s in spans if s["attributes"].get("gen_ai.operation.name") == "chat"]
    tin = sum(int(s["attributes"].get("gen_ai.usage.input_tokens", 0)) for s in chats)
    tout = sum(int(s["attributes"].get("gen_ai.usage.output_tokens", 0)) for s in chats)
    tcache = sum(int(s["attributes"].get("gen_ai.usage.cache_read_input_tokens", 0)) for s in chats)
    want = expected(ctx.application_id)
    got = (ctx.decision or {}).get("outcome")
    row = {
        "configuration": ctx.configuration, "application_id": ctx.application_id, "run_id": ctx.run_id,
        "trace_id": ctx.trace_id, "model": ctx.model,
        "wall_s": round((root["end_ns"] - root["start_ns"]) / 1e9, 2) if root else getattr(ctx, "wall_s", None),
        "model_s": round(sum(s["end_ns"] - s["start_ns"] for s in chats) / 1e9, 2),
        "turns": len(chats), "input_tokens": tin, "output_tokens": tout, "cache_read_tokens": tcache,
        "cost_usd": cost(ctx.model, tin, tout, tcache),
        "outcome": got, "expected_outcome": want.outcome.value, "agrees": got == want.outcome.value,
        "requires_person": (ctx.decision or {}).get("requires_person"), "expected_requires_person": want.requires_person,
        "lint_rejections": ctx.lint_rejections, "tool_sequence": ctx.tool_calls,
        "cited_records": (ctx.decision or {}).get("cited_records", []),
        "records_read": sorted(ctx.records),
        "decision_id": (ctx.decision or {}).get("decision_id"),
        **(extra or {}),
    }
    RUNS.parent.mkdir(parents=True, exist_ok=True)
    with RUNS.open("a", encoding="utf-8") as f:
        f.write(json.dumps(row, default=str) + "\n")
    return row


def _mr(values: list[float], digits: int = 1) -> str:
    values = [v for v in values if v is not None]
    if not values:
        return "n/a"
    f = lambda v: f"{v:.{digits}f}"  # noqa: E731
    return f"{f(statistics.median(values))} ({f(min(values))} to {f(max(values))})"


def table(runs_path: Path = RUNS, out: Path = TABLE) -> str:
    rows = [json.loads(line) for line in runs_path.read_text().splitlines()] if runs_path.exists() else []
    by_conf: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_conf[r["configuration"]].append(r)
    confs = list(by_conf)
    metrics = [
        ("Runs", lambda rs: str(len(rs))),
        ("Wall clock per decision, s", lambda rs: _mr([r["wall_s"] for r in rs])),
        ("Model time per decision, s", lambda rs: _mr([r["model_s"] for r in rs])),
        ("Model turns", lambda rs: _mr([r["turns"] for r in rs], 0)),
        ("Cost per decision, USD", lambda rs: _mr([r["cost_usd"] for r in rs], 4)),
        ("Outcome agrees with policy", lambda rs: f"{sum(r['agrees'] for r in rs)}/{len(rs)}"),
        ("Linter first-pass rate", lambda rs: f"{sum(r['lint_rejections'] == 0 for r in rs if r['outcome'])}/"
                                              f"{sum(1 for r in rs if r['outcome'])}"),
        ("Run-to-run consistency (same steps, same records read)", _consistency),
        ("Human pauses", lambda rs: str(sum(int(r.get("human_pauses", 0)) for r in rs))),
    ]
    lines = ["| Metric | " + " | ".join(confs) + " |", "|---|" + "---|" * len(confs)]
    for name, fn in metrics:
        lines.append(f"| {name} | " + " | ".join(fn(by_conf[c]) for c in confs) + " |")
    body = ("# Results\n\nGenerated by `brightwater results table` from `results/runs.jsonl`. Median (range) "
            "over every run of every applicant in each configuration.\n\n" + "\n".join(lines) + "\n")
    out.write_text(body)
    return body


def _consistency(rs: list[dict[str, Any]]) -> str:
    by_app: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rs:
        by_app[r["application_id"]].append(r)
    repeated = {a: v for a, v in by_app.items() if len(v) > 1}
    if not repeated:
        return "n/a"
    same = sum(1 for v in repeated.values()
               if len({tuple(r["tool_sequence"]) for r in v}) == 1
               and len({tuple(r.get("records_read", [])) for r in v}) == 1)
    return f"{same}/{len(repeated)} applicants"
