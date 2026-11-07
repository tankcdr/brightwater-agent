"""Chapter 4: drive the compiled AIP procedure.

The procedure decides the order of steps; the driver only acts at pauses:

* `decision` (no decision model configured, option A): the driving model
  answers the typed questions. AIP records these answers as `manual`, the
  same as a person's, so the driver also records who actually answered
  (`answered_by`) on the filed decision and in the memory graph.
* `client_task` `draft-rationale`: the driving model writes the rationale. The
  driver lints it before answering and asks again with the errors until it
  passes, so the `file` step never sees a bad rationale.
* `client_task` `confirm-with-person`: routed to a person, never to a model.
  This is the P6 (and from Ch 5, P7) guarantee.

Runs go to an AIP server (`AIP_SERVER`, or `aip config --server`) when a
published name is used, or run locally from the skill folder.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from . import linter, rubric, tools, tracing

ROOT = Path(__file__).resolve().parents[2]
SKILL_NAME = "brightwater-loan-assessment"
SKILL_DIR = ROOT / "skills" / SKILL_NAME
PERSON_STEPS = {"confirm-with-person"}
MAX_RATIONALE_ATTEMPTS = 4


def _json_from(text: str) -> dict[str, Any]:
    """The answer object in a model's reply: the last complete top-level JSON object.

    Models sometimes write a brace in prose, wrap the JSON in a code fence, or explain after it, so
    each `{` is tried in turn and anything that does not parse is skipped.
    """
    decoder = json.JSONDecoder()
    found, i = None, text.find("{")
    while i != -1:
        try:
            obj, end = decoder.raw_decode(text, i)
        except json.JSONDecodeError:
            i = text.find("{", i + 1)
            continue
        if isinstance(obj, dict):
            found = obj
        i = text.find("{", end)
    if found is None:
        raise ValueError(f"no JSON object in model output: {text[:200]!r}")
    return found


# ------------------------------------------------------------------ who answers
class Answerer:
    """Answers decision pauses and writes rationales. Subclasses: a chat model, or the scripted rubric."""

    name = "unknown"

    def decide(self, pause: Any) -> dict[str, Any]:
        raise NotImplementedError

    def rationale(self, pause: Any, errors: list[str]) -> str:
        raise NotImplementedError


class ModelAnswerer(Answerer):
    def __init__(self, model: Any, name: str, callbacks: list[Any] | None = None):
        self.model, self.name, self.callbacks = model, name, callbacks or []

    def _ask(self, prompt: str) -> str:
        msg = self.model.invoke([("user", prompt)], config={"callbacks": self.callbacks})
        text = msg.content if isinstance(msg.content, str) else "".join(
            p.get("text", "") for p in msg.content if isinstance(p, dict))
        return re.sub(r"<think>.*?</think>", "", text, flags=re.S)  # local reasoning models (Ch 7)

    def decide(self, pause: Any) -> dict[str, Any]:
        prompt = ("Answer each question about this loan application from the state alone. Return one JSON object "
                  "with one key per question: a noul is true or false, a choice is one of its criteria labels, a "
                  "score is the level number (its position in criteria, from 0).\n\n"
                  f"Questions:\n{json.dumps(pause.questions, indent=2)}\n\nState:\n{json.dumps(pause.state, indent=2)}")
        return _json_from(self._ask(prompt))

    def rationale(self, pause: Any, errors: list[str]) -> str:
        prompt = pause.task
        if errors:
            prompt += "\n\nYour previous rationale was rejected. Fix every error:\n- " + "\n- ".join(errors)
        return _json_from(self._ask(prompt))["rationale"]


class ScriptedAnswerer(Answerer):
    """The rubric's answers and a templated rationale: plumbing checks and CI, never a baseline."""

    name = "scripted"

    def decide(self, pause: Any) -> dict[str, Any]:
        return rubric.label(pause.state)

    def rationale(self, pause: Any, errors: list[str]) -> str:
        s = pause.state
        f = s["facts"]
        app = s["application_id"]
        lines = [f"Outcome: {s['rule_outcome']}",
                 f"DSCR is {float(f['dscr']):.2f} [cube:application_month.dscr/{app}].",
                 f"Group exposure is ${float(f['group_exposure']):,.0f} [cube:group_member.group_exposure/{app}]."]
        lines += [f"Rule {r['rule_id']} applies [policy:{r['rule_id']}v1]." for r in s["fired"]]
        if s["rule_outcome"] == "review":
            lines.append(f"For the reviewer: risk tier {s['risk_tier']} [cube:application_month.dscr/{app}], "
                         f"recommendation {s['recommendation'].replace('_', ' ')}.")
        return "\n".join(lines)


Person = Callable[[Any], dict[str, Any]]


def console_person(pause: Any) -> dict[str, Any]:
    """Ask a person at the terminal."""
    print("\n" + pause.task)
    ok = input("Confirm this decision? [y/N] ").strip().lower() == "y"
    who = input("Your user id: ").strip() or os.getenv("USER", "officer")
    note = input("Note: ").strip()
    return {"confirmed": ok, "confirmed_by": who, "confirmation_note": note}


def auto_person(user: str = "test-officer") -> Person:
    """Confirms every decision. Tests and demos only."""
    def answer(pause: Any) -> dict[str, Any]:
        return {"confirmed": True, "confirmed_by": user, "confirmation_note": "confirmed (test)"}
    return answer


# ------------------------------------------------------------------ driving one run
@dataclass
class ProcedureRun:
    ctx: tools.RunContext
    aip_run_id: str
    steps: list[str]
    human_pauses: int
    decision_pauses: int


def run(application_id: str, answerer: Answerer, person: Person, configuration: str = "ch4-procedure",
        skill: str | None = None) -> ProcedureRun:
    import aip

    ctx = tools.RunContext(application_id=application_id, configuration=configuration, model=answerer.name)
    target = skill or (SKILL_NAME if os.getenv("AIP_SERVER") or _configured_server() else str(SKILL_DIR))
    client = aip.Client()
    tracer = tracing.setup()
    steps: list[str] = []
    human = decisions = 0
    with tracer.start_as_current_span(f"run_procedure {SKILL_NAME}", attributes={
            "gen_ai.operation.name": "invoke_agent", "gen_ai.agent.name": SKILL_NAME,
            "gen_ai.request.model": answerer.name, "brightwater.application_id": application_id,
            "brightwater.configuration": configuration, "brightwater.run_id": ctx.run_id}) as root:
        ctx.trace_id = f"{root.get_span_context().trace_id:032x}"
        if isinstance(answerer, ModelAnswerer):
            answerer.callbacks = [tracing.GenAISpans(tracer, root, answerer.name, ctx.run_id)]
        run_env = {"BRIGHTWATER_CONFIGURATION": configuration, "BRIGHTWATER_DRIVER_RUN": ctx.run_id,
                   "BRIGHTWATER_TRACE_ID": ctx.trace_id, "BRIGHTWATER_DRIVER_MODEL": answerer.name}
        saved_env = {k: os.environ.get(k) for k in run_env}
        os.environ.update(run_env)

        def on_step(line: Any) -> None:
            data = line if isinstance(line, dict) else json.loads(line)
            name = data.get("step")
            if not name:
                return
            steps.append(name)
            end = time.time_ns()
            start = end - int(float(data.get("seconds") or 0) * 1e9)
            attrs = {"brightwater.step": name, "brightwater.step_kind": str(data.get("kind", ""))}
            for k in ("resolver", "to"):
                if data.get(k):
                    attrs[f"brightwater.{k}"] = str(data[k])
            span = tracer.start_span(f"execute_step {name}", start_time=start, attributes=attrs)
            span.end(end_time=end)

        result = client.run(target, {"application_id": application_id}, on_step=on_step)
        while isinstance(result, aip.Paused):
            if result.kind == "decision":
                decisions += 1
                answer = answerer.decide(result)
                ctx.assessment = answer
            elif result.step in PERSON_STEPS:
                human += 1
                answer = person(result)
            elif result.step == "draft-rationale":
                ctx.saw(*result.state["records"], *result.state["policy_records"])
                text = _rationale_until_clean(answerer, result, ctx)
                if text is None:
                    # The model could not write a rationale the linter accepts, so nothing is filed.
                    # The AIP run stays paused at draft-rationale (AIP 0.7.0 has no call to end a run
                    # early), and the run is recorded as not filed, like a Ch 3 agent run that never files.
                    break
                answer = {"rationale": text}
            else:
                raise RuntimeError(f"no handler for pause {result.kind}/{result.step}")
            result = client.resume(result, answer, on_step=on_step)
        root.set_attribute("brightwater.aip_run_id", result.run_id)
        for k, v in saved_env.items():  # the next run mints its own ids
            if v is None:
                os.environ.pop(k, None)
            else:
                os.environ[k] = v

    not_filed = isinstance(result, aip.Paused)  # stopped at draft-rationale: see above
    final = result.state
    ctx.aip_run_id = result.run_id  # type: ignore[attr-defined]
    if not not_filed:
        _finish(ctx, final, answerer, result.run_id, steps)
    if os.getenv("BRIGHTWATER_MEMORY", "on") == "on":
        # The same reasoning trace a Ch 3 agent run writes, with the procedure's steps in order,
        # so query 03 can set the two configurations side by side.
        import asyncio

        from . import graph
        asyncio.run(graph.record_run(ctx))
    return ProcedureRun(ctx, result.run_id, steps, human, decisions)


def _rationale_until_clean(answerer: Answerer, pause: Any, ctx: tools.RunContext) -> str | None:
    """The first rationale that passes the linter, or None after MAX_RATIONALE_ATTEMPTS rejections."""
    known = set(pause.state["records"]) | set(pause.state["policy_records"])
    errors: list[str] = []
    for _ in range(MAX_RATIONALE_ATTEMPTS):
        text = answerer.rationale(pause, errors)
        violations = linter.lint(text, pause.state["rule_outcome"], known)
        if not violations:
            return text
        ctx.lint_rejections += 1
        errors = [str(v) for v in violations]
        ctx.lint_messages.extend(errors)
    return None


def _finish(ctx: tools.RunContext, final: dict[str, Any], answerer: Answerer, aip_run_id: str,
            steps: list[str]) -> None:
    """Attach who answered and the AIP run id to the filed decision."""
    from . import db

    with db.session() as conn:
        rows = db.rows(conn, "UPDATE agent.decision SET answered_by = :by, run_id = :run, trace_id = :trace "
                             "WHERE decision_id = CAST(:d AS uuid) RETURNING *",
                       by=f"agent:{answerer.name}",
                       run=aip_run_id, trace=ctx.trace_id, d=final["decision_id"])
    row = rows[0]
    ctx.decision = {**row, "decision_id": str(row["decision_id"]), "cited_records": row["cited_records"]}
    ctx.tool_calls = steps
    ctx.events = [{"tool": s, "args": {}, "records": [], "ok": True, "ms": 0} for s in steps]


def _configured_server() -> str | None:
    try:
        import aip
        return aip.settings().server
    except Exception:  # noqa: BLE001
        return None


def build() -> list[Path]:
    """Refresh what the skill carries from the repo: the rules asset and the source material."""
    import shutil

    from . import policy

    written = []
    asset = SKILL_DIR / "assets" / "policy.json"
    asset.write_text(json.dumps(policy.rules_to_json(), indent=2) + "\n")
    written.append(asset)
    for src in (ROOT / "skills" / "source" / "assessment-sop.md", ROOT / "decision_model" / "rubric.md"):
        dst = SKILL_DIR / "source" / src.name
        shutil.copyfile(src, dst)
        written.append(dst)
    return written


# ------------------------------------------------------------------ Ch 5: impact of a new revision
def _rules_of(ref: str):  # noqa: ANN202
    """The lending rules a revision ships with: a skill folder, or a published name@revision."""
    import tempfile

    from . import policy

    path = Path(ref)
    if not path.exists():
        import aip
        out = Path(tempfile.mkdtemp(prefix="aip-rev-"))
        path = Path(aip.Client().get(ref if "@" in ref else f"{SKILL_NAME}@{ref}", out=str(out)).path)
    return policy.rules_from_json(json.loads((path / "assets" / "policy.json").read_text()))


def impact(old: str, new: str) -> dict[str, Any]:
    """Every application whose decision changes between two revisions, and the runs pinned to the old one."""
    from . import db, policy, semantic

    rules_old, rules_new = _rules_of(old), _rules_of(new)
    with db.session() as conn:
        ids = [r[0] for r in conn.run("SELECT application_id FROM credit.application ORDER BY 1")]
    facts = semantic.facts_direct(semantic.load(semantic.ROOT / "semantic" / "brightwater.ossie.yaml"), ids)
    changes = []
    for i in ids:
        a, b = policy.evaluate(facts[i], rules_old), policy.evaluate(facts[i], rules_new)
        if (a.outcome, a.requires_person, a.fired_ids) != (b.outcome, b.requires_person, b.fired_ids):
            changes.append({"application_id": i, "outcome": [a.outcome.value, b.outcome.value],
                            "requires_person": [a.requires_person, b.requires_person],
                            "fired": [a.fired_ids, b.fired_ids], "group_exposure": str(facts[i].group_exposure)})
    pinned = []
    try:
        import aip
        old_rev = old.split("@")[-1]
        pinned = [{"run_id": r.run_id, "revision": r.revision, "status": r.status}
                  for r in aip.Client().runs(name=SKILL_NAME, limit=500) if r.revision == old_rev]
    except Exception:  # noqa: BLE001
        pass
    return {"old": policy.policy_version(rules_old), "new": policy.policy_version(rules_new),
            "changes": changes, "runs_pinned_to_old": pinned}


def export_traces(runs_path: Path | None = None, configuration: str = "ch3-freeform") -> Path:
    """Summarize the recorded free-form runs as compile source: what every run did, and what went wrong."""
    from collections import Counter, defaultdict

    from . import bench

    # Written next to the run records it summarizes, not into the skill: the published procedure was
    # not compiled from your runs, and a revision's id must not depend on them.
    runs_path = runs_path or bench.RUNS
    rows = [json.loads(line) for line in runs_path.read_text().splitlines()] if runs_path.exists() else []
    rows = [r for r in rows if r["configuration"] == configuration]
    out = bench.RESULTS / "traces.md"
    by_app: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for r in rows:
        by_app[r["application_id"]].append(r)
    lines = [f"# Recorded runs ({configuration})", "",
             f"{len(rows)} runs of the free-form agent, exported by `brightwater procedure export-traces`.", ""]
    first = Counter(r["tool_sequence"][0] for r in rows if r["tool_sequence"])
    lines += [f"First tool called: {dict(first)}", "", "| Application | Run | Outcome | Agrees | Lint rejections | Tool order |",
              "|---|---|---|---|---|---|"]
    for app, rs in sorted(by_app.items()):
        for i, r in enumerate(rs, 1):
            lines.append(f"| {app} | {i} | {r['outcome']} | {r['agrees']} | {r['lint_rejections']} | "
                         f"{' > '.join(r['tool_sequence'])} |")
    out.write_text("\n".join(lines) + "\n")
    return out
