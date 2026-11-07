"""The agent's tools (Ch 3 onward).

Each tool is a plain function that returns JSON-able data plus the record
keys behind it. The same functions back the `brightwater tool ...` CLI, the
LangGraph agent in Ch 3, and the AIP procedure's scripts in Ch 4, so every
configuration reads the same governed figures the same way.

A `RunContext` follows one run: which records its tools returned (the
linter's provenance set), the order tools were called in, and how many times
a filing was rejected.

Record keys look like `system.table:key` for systems of record,
`cube:dataset.member/APP-ID` for governed figures, `policy:P3v1` for a rule
version, and `graph:...` for a path found in the memory graph.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import uuid
from dataclasses import dataclass, field
from decimal import Decimal
from pathlib import Path
from typing import Any, Callable

from . import linter, policy

ROOT = Path(__file__).resolve().parents[2]
OSSIE = ROOT / "semantic" / "brightwater.ossie.yaml"


def semantic_version() -> str:
    """The definitions in force: a hash of the Ossie model file."""
    return hashlib.sha256(OSSIE.read_bytes()).hexdigest()[:12]


def _num(v: Any) -> Any:
    if v is None:
        return None
    try:
        d = Decimal(str(v))
        return float(d) if d != d.to_integral_value() else int(d)
    except Exception:  # noqa: BLE001
        return v


@dataclass
class RunContext:
    application_id: str
    configuration: str
    model: str = "unknown"
    run_id: str = field(default_factory=lambda: uuid.uuid4().hex[:16])
    trace_id: str | None = None
    records: set[str] = field(default_factory=set)
    tool_calls: list[str] = field(default_factory=list)
    events: list[dict[str, Any]] = field(default_factory=list)
    lint_rejections: int = 0
    lint_messages: list[str] = field(default_factory=list)
    decision: dict[str, Any] | None = None
    assessment: dict[str, Any] | None = None

    def saw(self, *keys: str) -> None:
        self.records.update(keys)


# ------------------------------------------------------------------ backends
class Backends:
    """Where tools get data. Swappable so tests can run without services."""

    def cube(self, query: dict[str, Any]) -> list[dict[str, Any]]:
        from .metrics_cube import Cube
        return Cube().load(query)

    def sql(self, sql: str, **params: Any) -> list[dict[str, Any]]:
        from . import db
        with db.session() as conn:
            return db.rows(conn, sql, **params)

    def graph(self, name: str, **params: Any) -> list[dict[str, Any]]:
        from . import graph
        return asyncio.run(graph.run_named_query(name, [f"{k}={v}" for k, v in params.items()]))

    def file(self, row: dict[str, Any]) -> str:
        from . import db
        decision_id = row.get("decision_id") or str(uuid.uuid4())
        with db.session() as conn:
            return str(conn.run(
                "INSERT INTO agent.decision (decision_id, application_id, outcome, status, requires_person, rationale, "
                "cited_records, records_read, assessment, configuration, run_id, trace_id, answered_by, "
                "semantic_version, policy_version) VALUES (CAST(:decision_id AS uuid), "
                ":application_id, :outcome, :status, :requires_person, :rationale, CAST(:cited_records AS jsonb), "
                "CAST(:records_read AS jsonb), CAST(:assessment AS jsonb), :configuration, :run_id, :trace_id, "
                ":answered_by, :semantic_version, :policy_version) RETURNING decision_id",
                **{**{k: v for k, v in row.items() if k != "records_read"}, "decision_id": decision_id,
                   "cited_records": json.dumps(row["cited_records"]),
                   "records_read": json.dumps(sorted(row.get("records_read", []))),
                   "assessment": json.dumps(row.get("assessment"))})[0][0])


BACKENDS = Backends()


def _where(cube: str, app: str) -> list[dict[str, Any]]:
    return [{"member": f"{cube}.application_id", "operator": "equals", "values": [app]}]


# ------------------------------------------------------------------ tools
def get_application(ctx: RunContext, application_id: str, b: Backends = BACKENDS) -> dict[str, Any]:
    """The application and the applicant's identity (no ledger, no figures)."""
    rows = b.sql("SELECT a.application_id, a.business_id, b.legal_name, a.requested_amount, a.term_months, "
                 "a.annual_rate, a.submitted_on, a.purpose FROM credit.application a "
                 "JOIN ledger.business b USING (business_id) WHERE a.application_id = :app", app=application_id)
    if not rows:
        return {"error": f"no application {application_id}"}
    r = {k: (str(v) if k == "submitted_on" else _num(v)) for k, v in rows[0].items()}
    keys = [f"credit.application:{application_id}", f"ledger.business:{r['business_id']}"]
    ctx.saw(*keys)
    return {"application": r, "records": keys}


def get_figures(ctx: RunContext, application_id: str, b: Backends = BACKENDS) -> dict[str, Any]:
    """Every governed figure the policy uses, from the semantic layer."""
    p = b.cube({"dimensions": ["application_profile.kyc_current", "application_profile.days_since_last_adverse_event",
                               "application_profile.tenure_years"], "filters": _where("application_profile", application_id)})
    m = b.cube({"measures": ["application_month.dscr", "application_month.cash_available",
                             "application_month.debt_service_total"], "filters": _where("application_month", application_id)})
    gx = b.cube({"measures": ["group_member.group_exposure"], "filters": _where("group_member", application_id)})
    g = b.cube({"dimensions": ["group_member.member_business_id", "group_member.is_applicant", "group_member.exposure"],
                "filters": _where("group_member", application_id)})
    if not p or not m:
        return {"error": f"no figures for {application_id}"}
    p, m = p[0], m[0]
    k = lambda member: f"cube:{member}/{application_id}"  # noqa: E731
    figures = {
        "kyc_current": {"value": p["application_profile.kyc_current"] in (True, "true"),
                        "record": k("application_profile.kyc_current")},
        "days_since_last_adverse_event": {"value": _num(p["application_profile.days_since_last_adverse_event"]),
                                          "record": k("application_profile.days_since_last_adverse_event")},
        "tenure_years": {"value": _num(p["application_profile.tenure_years"]),
                         "record": k("application_profile.tenure_years")},
        "dscr": {"value": round(float(m["application_month.dscr"]), 4), "record": k("application_month.dscr")},
        "cash_available": {"value": _num(m["application_month.cash_available"]),
                           "record": k("application_month.cash_available")},
        "debt_service_total": {"value": round(float(m["application_month.debt_service_total"]), 2),
                               "record": k("application_month.debt_service_total")},
        "group_exposure": {"value": _num(gx[0]["group_member.group_exposure"]) if gx else None,
                           "record": k("group_member.group_exposure")},
    }
    members = [{"business_id": r["group_member.member_business_id"], "is_applicant": r["group_member.is_applicant"] in (True, "true"),
                "exposure": _num(r["group_member.exposure"])} for r in g]
    keys = [f["record"] for f in figures.values()]
    ctx.saw(*keys)
    return {"figures": figures, "group_members": members, "semantic_version": semantic_version(), "records": keys}


def get_related_facilities(ctx: RunContext, application_id: str, b: Backends = BACKENDS) -> dict[str, Any]:
    """Facilities at the applicant and at businesses its owners hold stakes in."""
    rows = b.cube({"dimensions": ["related_facility.facility_id", "related_facility.business_id",
                                  "related_facility.owner_stake_pct", "related_facility.days_past_due"],
                   "filters": _where("related_facility", application_id),
                   "order": {"related_facility.facility_id": "asc"}})
    out = []
    for r in rows:
        key = f"cube:related_facility/{application_id}/{r['related_facility.facility_id']}"
        out.append({"facility_id": r["related_facility.facility_id"], "business_id": r["related_facility.business_id"],
                    "owner_stake_pct": _num(r["related_facility.owner_stake_pct"]),
                    "days_past_due": _num(r["related_facility.days_past_due"]), "record": key})
    ctx.saw(*(f["record"] for f in out))
    return {"related_facilities": out, "records": [f["record"] for f in out]}


def find_connections(ctx: RunContext, application_id: str, b: Backends = BACKENDS) -> dict[str, Any]:
    """Owners, what else they hold, and any late facility one hop away (memory graph)."""
    try:
        holdings = b.graph("02", app=application_id)
        late = b.graph("01", app=application_id)
    except Exception as e:  # noqa: BLE001
        return {"error": f"memory graph unavailable: {e}"}
    keys = [f"registry.ownership:{h['person_id']}/{h['other_business_id']}" for h in holdings if h.get("other_business_id")]
    keys += [f"graph:path/{application_id}/{p['facility']}" for p in late]
    ctx.saw(*keys)
    return {"owners_and_holdings": holdings, "late_facility_paths": late, "records": keys}


def get_notes(ctx: RunContext, application_id: str, b: Backends = BACKENDS) -> dict[str, Any]:
    """The banker's free-text notes. Context only: never a source for a figure."""
    rows = b.sql("SELECT n.note_id, n.written_on, n.body FROM relationship.note n "
                 "JOIN credit.application a USING (business_id) WHERE a.application_id = :app ORDER BY n.written_on",
                 app=application_id)
    notes = [{**r, "written_on": str(r["written_on"]), "record": f"relationship.note:{r['note_id']}"} for r in rows]
    ctx.saw(*(n["record"] for n in notes))
    return {"notes": notes, "records": [n["record"] for n in notes]}


def get_policy(ctx: RunContext, rules: tuple[policy.Rule, ...] = policy.RULES) -> dict[str, Any]:
    """The lending policy, rule by rule, with parameters."""
    out = [{"rule": f"{r.rule_id}v{r.version}", "title": r.title, "text": r.text, "params": r.params,
            "record": f"policy:{r.rule_id}v{r.version}"} for r in rules]
    ctx.saw(*(r["record"] for r in out))
    return {"rules": out, "policy_version": policy.policy_version(rules), "records": [r["record"] for r in out]}


def file_decision(ctx: RunContext, application_id: str, outcome: str, rationale: str, requires_person: bool,
                  assessment: dict[str, Any] | None = None, answered_by: str | None = None,
                  b: Backends = BACKENDS) -> dict[str, Any]:
    """File the decision. Rejected, with reasons, if the rationale fails the linter."""
    outcome = outcome.strip().lower()
    if outcome not in ("approve", "review", "decline"):
        return {"filed": False, "errors": [f"outcome must be approve, review or decline, not {outcome!r}"]}
    violations = linter.lint(rationale, outcome, ctx.records)
    if violations:
        ctx.lint_rejections += 1
        ctx.lint_messages.extend(str(v) for v in violations)
        return {"filed": False, "errors": [str(v) for v in violations],
                "hint": "Fix every error and call file_decision again."}
    row = {"application_id": application_id, "outcome": outcome,
           "status": "pending_person" if requires_person else "filed", "requires_person": bool(requires_person),
           "rationale": rationale, "cited_records": linter.cited(rationale), "assessment": assessment,
           "configuration": ctx.configuration, "run_id": ctx.run_id, "trace_id": ctx.trace_id,
           "answered_by": answered_by or f"agent:{ctx.model}", "semantic_version": semantic_version(),
           "policy_version": policy.policy_version(), "records_read": sorted(ctx.records)}
    decision_id = b.file(row)
    ctx.decision = {**row, "decision_id": decision_id}
    return {"filed": True, "decision_id": decision_id, "status": row["status"]}


TOOLS: dict[str, Callable[..., dict[str, Any]]] = {
    "get_application": get_application,
    "get_figures": get_figures,
    "get_related_facilities": get_related_facilities,
    "find_connections": find_connections,
    "get_notes": get_notes,
    "get_policy": get_policy,
    "file_decision": file_decision,
}
