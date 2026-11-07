"""The code behind the AIP procedure's execution steps (Ch 4 onward).

The skill's script (`skills/brightwater-loan-assessment/scripts/steps.py`)
reads the step payload from stdin and calls one function here. The lending
rules themselves travel inside the skill as `assets/policy.json`, so a
procedure revision pins the exact rules it applies: publishing revision 2 with
P7 (Ch 5) cannot change what a revision 1 run decided.
"""

from __future__ import annotations

import json
import os
from typing import Any

from . import linter, metrics_cube, policy, tools



def _tool(name: str, ctx: Any, *args: Any, **kwargs: Any) -> dict[str, Any]:
    """Call a tool through its guard (OPA, with enforcement on), and stop the run if policy denies it."""
    out = tools.TOOLS[name](ctx, *args, **kwargs)
    if str(out.get("error", "")).startswith("denied by policy"):
        raise SystemExit(f"{name}: {out['error']}")
    return out

def gather(state: dict[str, Any], assets: dict[str, str]) -> dict[str, Any]:
    """Every governed figure for the application, the monthly series behind DSCR, and the records read."""
    app = state["application_id"]
    from . import identity
    if identity.enforced():
        identity.require_assess(app)   # Ch 8: fail closed before a single figure is read
    ctx = tools.RunContext(application_id=app, configuration="procedure")
    _tool("get_application", ctx, app)
    _tool("get_figures", ctx, app)
    _tool("get_related_facilities", ctx, app)
    facts = metrics_cube.facts_for(app)
    months = metrics_cube.Cube().load({
        "dimensions": ["application_month.month", "application_month.net_cash_flow", "application_month.debt_service"],
        "filters": [{"member": "application_month.application_id", "operator": "equals", "values": [app]}],
        "order": {"application_month.month": "asc"}})
    ctx.saw(f"cube:application_month.net_cash_flow/{app}")
    return {
        "facts": facts.to_dict(),
        "monthly_net_cash_flow": [round(float(m["application_month.net_cash_flow"]), 2) for m in months],
        "monthly_debt_service": round(float(months[0]["application_month.debt_service"]), 2) if months else 0.0,
        "records": sorted(ctx.records),
        "semantic_version": tools.semantic_version(),
    }


def apply_policy(state: dict[str, Any], assets: dict[str, str]) -> dict[str, Any]:
    rules = policy.rules_from_json(json.loads(assets["policy"]))
    d = policy.evaluate(policy.facts_from_dict(state["facts"]), rules)
    return {
        "rule_outcome": d.outcome.value,
        "fired": [{"rule_id": f.rule_id, "effect": f.effect, "reason": f.reason} for f in d.fired],
        "requires_person": d.requires_person,
        "person_reasons": [f.rule_id for f in d.fired if f.effect == "requires_person"],
        "policy_version": d.policy_version,
        "policy_records": [f"policy:{r.rule_id}v{r.version}" for r in rules],
    }


def file(state: dict[str, Any], assets: dict[str, str]) -> dict[str, Any]:
    """Lint the rationale against the records this run read, then file the decision."""
    known = set(state["records"]) | set(state["policy_records"])
    violations = linter.lint(state["rationale"], state["rule_outcome"], known)
    if violations:
        raise SystemExit("rationale failed the linter: " + "; ".join(map(str, violations)))
    ctx = tools.RunContext(application_id=state["application_id"],
                           configuration=os.getenv("BRIGHTWATER_CONFIGURATION", "procedure"),
                           model=os.getenv("BRIGHTWATER_DRIVER_MODEL", "unknown"),
                           run_id=os.getenv("BRIGHTWATER_DRIVER_RUN", "aip"), records=known)
    ctx.trace_id = os.getenv("BRIGHTWATER_TRACE_ID")
    out = _tool("file_decision", ctx, state["application_id"], state["rule_outcome"], state["rationale"],
                              state["requires_person"],
                              assessment={k: state[k] for k in ("risk_tier", "cash_flow_stable", "recommendation")},
                              answered_by="pending",
                              # the rules this revision applied, not whatever the installed package holds
                              policy_version=".".join(r.removeprefix("policy:") for r in state["policy_records"]))
    return {"decision_id": out["decision_id"], "status": out["status"]}


def record_confirmation(state: dict[str, Any], assets: dict[str, str]) -> dict[str, Any]:
    """A person confirmed or rejected a decline the policy would not let the agent file alone."""
    from . import db

    status = "filed" if state["confirmed"] else "returned"
    with db.session(agent=True) as conn:
        conn.run("UPDATE agent.decision SET status = :s, confirmed_by = :by WHERE decision_id = CAST(:d AS uuid)",
                 s="filed" if state["confirmed"] else "pending_person", by=f"user:{state['confirmed_by']}",
                 d=state["decision_id"])
    return {"final_status": status}


STEPS = {"gather": gather, "apply-policy": apply_policy, "file": file, "record-confirmation": record_confirmation}
