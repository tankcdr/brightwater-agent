"""Chapter 9: the audit packet.

Every filed decision can be reconstructed from one id, and the reconstruction
is tamper-evident. Each layer already records its own part; this module joins
them on the decision id and seals the result:

| An auditor asks                         | From                                              |
|-----------------------------------------|---------------------------------------------------|
| Who asked, as which user and agent?     | the decision row (user_subject, answered_by)      |
| Was that user allowed to see it?        | audit.authz (OpenFGA and OPA decisions)           |
| Which records were read?                | records_read on the decision; pgaudit lines       |
| Which metric definitions?               | semantic_version (hash of the Ossie model)        |
| Which procedure ran, exactly?           | the AIP run record: name@revision, every step     |
| What did the judgment step see?         | the assess step's stored input (figures only)     |
| What did the model answer, how sure?    | the assess step's answers and attribution         |
| Did a person override, and who?         | review answers; confirmed_by                      |
| Why this outcome?                       | outcome, rules fired, rationale and its lint      |
| What did the model calls look like?     | OpenTelemetry spans for the trace                 |
| Which policy was in force?              | compliance.policy_rule rows for each rule version |

Sealing appends the packet to `audit.packet`, where each row's chain hash is
SHA-256 over the previous row's chain hash and this packet's own hash. Edit
one packet and its hash no longer matches; edit both the packet and its stored
hash and every later chain hash breaks.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

from . import db, linter, tracing

GENESIS = "genesis"


def canonical(obj: Any) -> str:
    """One byte-exact serialization: sorted keys, no whitespace, round-tripped through JSON."""
    return json.dumps(json.loads(json.dumps(obj, default=str)), sort_keys=True, separators=(",", ":"))


def sha256(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


# ------------------------------------------------------------------ assembling
def _decision(conn: Any, decision_id: str) -> dict[str, Any]:
    rows = db.rows(conn, "SELECT * FROM agent.decision WHERE decision_id = CAST(:d AS uuid)", d=decision_id)
    if not rows:
        raise KeyError(f"no decision {decision_id}")
    return {k: (str(v) if k in ("decision_id", "filed_at") else v) for k, v in rows[0].items()}


def _aip_run(run_id: str) -> dict[str, Any] | None:
    try:
        import aip
        r = aip.Client().run_record(run_id)
    except Exception:  # noqa: BLE001  (a Ch 3 free-form run has no AIP record)
        return None
    steps = []
    for h in r.history:
        entry = {"step": h["step"], "kind": h["kind"]}
        if h["kind"] == "decision":
            entry.update(input=h.get("input"), answers=(h.get("result") or {}).get("answers"),
                         attribution=h.get("attribution"))
        elif h["kind"] == "client_task" and h["step"] == "confirm-with-person":
            entry.update(answer=(h.get("result") or h.get("output")))
        steps.append(entry)
    return {"name": r.name, "revision": r.revision, "status": r.status, "steps": steps}


def _policy_rows(conn: Any, policy_version: str) -> list[dict[str, Any]]:
    pairs = re.findall(r"(P\d+)v(\d+)", policy_version)
    out = []
    for rule_id, version in pairs:
        rows = db.rows(conn, "SELECT rule_id, version, title, body, params FROM compliance.policy_rule "
                             "WHERE rule_id = :r AND version = :v", r=rule_id, v=int(version))
        out.append(rows[0] if rows else {"rule_id": rule_id, "version": int(version), "missing": True})
    return out


def _spans(trace_id: str | None) -> list[dict[str, Any]]:
    if not trace_id:
        return []
    out = []
    for s in tracing.read_spans(tracing.spans_file(), trace_id):
        a = s["attributes"]
        out.append({"name": s["name"], "ms": round((s["end_ns"] - s["start_ns"]) / 1e6, 1),
                    **{k: a[k] for k in ("gen_ai.request.model", "gen_ai.response.model", "gen_ai.usage.input_tokens",
                                         "gen_ai.usage.output_tokens", "gen_ai.tool.name", "brightwater.step",
                                         "brightwater.resolver") if k in a}})
    return out


def _pgaudit(decision_id: str) -> list[str] | None:
    """Relations read under this decision's connections, from the Postgres log (when it is readable here)."""
    path = os.getenv("BRIGHTWATER_PGAUDIT_LOG")
    if not path or not Path(path).exists():
        return None
    tag = f"brightwater:{decision_id}"
    rels = set()
    for line in Path(path).read_text(errors="replace").splitlines():
        if tag in line and "AUDIT: SESSION" in line:
            parts = line.split("AUDIT: SESSION,", 1)[1].split(",")
            if len(parts) > 5 and parts[4] == "TABLE":
                rels.add(parts[5])
    return sorted(rels)


def _reviews(decision_id: str) -> list[dict[str, Any]]:
    """Who answered the judgment step's review pause for this decision, and what they changed."""
    from .procedure import review_answers_path
    path = review_answers_path()
    if not path.exists():
        return []
    rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    return [{"answered_by": r["answered_by"], "at": r["at"], "step": r["step"], "flagged": r["flagged"],
             "changed": {k: v for k, v in r["final"].items() if r["proposed"].get(k) != v}}
            for r in rows if r.get("decision_id") == decision_id]


def assemble(decision_id: str) -> dict[str, Any]:
    with db.session() as conn:
        d = _decision(conn, decision_id)
        authz = db.rows(conn, "SELECT at, engine, subject, action, object, allowed, detail FROM audit.authz "
                              "WHERE decision_id = :d ORDER BY id", d=decision_id)
        policy_rows = _policy_rows(conn, d["policy_version"])
    run = _aip_run(d["run_id"])
    assess = next((s for s in (run or {}).get("steps", []) if s["step"] == "assess"), None)
    known = set(d.get("records_read") or []) | {f"policy:{r['rule_id']}v{r['version']}" for r in policy_rows}
    lint = [str(v) for v in linter.lint(d["rationale"], d["outcome"], known)] if known else ["records_read not stored"]
    return {
        "decision_id": decision_id,
        "who": {"user": d.get("user_subject"), "answered_by": d["answered_by"], "confirmed_by": d.get("confirmed_by"),
                "reviewed_by": _reviews(decision_id), "configuration": d["configuration"]},
        "entitlement": [{**a, "at": str(a["at"])} for a in authz],
        "records": {"read": d.get("records_read"), "cited": d["cited_records"], "pgaudit_relations": _pgaudit(decision_id)},
        "definitions": {"semantic_version": d["semantic_version"]},
        "procedure": {k: run[k] for k in ("name", "revision", "status")} if run else None,
        "judgment": {"input": assess["input"], "answers": assess["answers"], "attribution": assess["attribution"]}
                    if assess else {"assessment": d.get("assessment")},
        "steps": [s["step"] for s in run["steps"]] if run else None,
        "outcome": {"outcome": d["outcome"], "status": d["status"], "requires_person": d["requires_person"],
                    "policy_version": d["policy_version"], "rationale": d["rationale"], "lint": lint or "pass"},
        "policy": policy_rows,
        "model_calls": _spans(d.get("trace_id")),
        "ids": {"aip_run_id": d["run_id"], "trace_id": d.get("trace_id"), "filed_at": d["filed_at"]},
    }


# ------------------------------------------------------------------ sealing and verifying
def seal(decision_id: str) -> dict[str, Any]:
    packet = assemble(decision_id)
    packet_hash = sha256(canonical(packet))
    with db.session() as conn:
        conn.run("BEGIN")
        try:
            conn.run("LOCK TABLE audit.packet IN EXCLUSIVE MODE")
            prev = conn.run("SELECT chain_hash FROM audit.packet ORDER BY seq DESC LIMIT 1")
            prev_hash = prev[0][0] if prev else GENESIS
            chain = sha256(prev_hash + packet_hash)
            seq = conn.run("INSERT INTO audit.packet (decision_id, packet, packet_sha256, prev_hash, chain_hash) "
                           "VALUES (CAST(:d AS uuid), CAST(:p AS jsonb), :ph, :prev, :chain) RETURNING seq",
                           d=decision_id, p=canonical(packet), ph=packet_hash, prev=prev_hash, chain=chain)[0][0]
            conn.run("COMMIT")
        except Exception:
            conn.run("ROLLBACK")
            raise
    return {"seq": seq, "decision_id": decision_id, "chain_hash": chain}


def verify() -> dict[str, Any]:
    """Recompute every packet hash and the whole chain. Reports the first row that does not match."""
    with db.session() as conn:
        rows = db.rows(conn, "SELECT seq, decision_id, packet, packet_sha256, prev_hash, chain_hash "
                             "FROM audit.packet ORDER BY seq")
    prev = GENESIS
    for r in rows:
        if sha256(canonical(r["packet"])) != r["packet_sha256"]:
            return {"ok": False, "seq": r["seq"], "decision_id": str(r["decision_id"]), "why": "packet edited"}
        if r["prev_hash"] != prev or sha256(prev + r["packet_sha256"]) != r["chain_hash"]:
            return {"ok": False, "seq": r["seq"], "decision_id": str(r["decision_id"]), "why": "chain broken"}
        prev = r["chain_hash"]
    return {"ok": True, "packets": len(rows), "head": prev}


def packet(decision_id: str) -> dict[str, Any] | None:
    with db.session() as conn:
        rows = db.rows(conn, "SELECT seq, packet, chain_hash FROM audit.packet WHERE decision_id = CAST(:d AS uuid)",
                       d=decision_id)
    return rows[0] if rows else None


# ------------------------------------------------------------------ governance queries
def _thresholds() -> dict[str, float]:
    from aip_spec import load_skill

    from .procedure import SKILL_DIR
    step = next(s for s in load_skill(str(SKILL_DIR)).spec.steps if s.name == "assess")
    return dict(step.thresholds or {})


def overridden() -> list[dict[str, Any]]:
    """Every decision where a person changed what the decision model proposed."""
    out = []
    with db.session() as conn:
        for r in db.rows(conn, "SELECT decision_id, packet FROM audit.packet ORDER BY seq"):
            att = (r["packet"].get("judgment") or {}).get("attribution") or {}
            proposed, answers = att.get("proposed") or {}, (r["packet"].get("judgment") or {}).get("answers") or {}
            changed = {k: [proposed[k], answers.get(k)] for k in proposed if proposed[k] != answers.get(k)}
            if att.get("reviewed") and changed:
                out.append({"decision_id": str(r["decision_id"]), "changed": changed, "by": r["packet"]["who"]})
    return out


def under_threshold() -> list[dict[str, Any]]:
    """Every decision a model answered with confidence below the procedure author's threshold."""
    th = _thresholds()
    out = []
    with db.session() as conn:
        for r in db.rows(conn, "SELECT decision_id, packet FROM audit.packet ORDER BY seq"):
            att = (r["packet"].get("judgment") or {}).get("attribution") or {}
            conf = att.get("confidence") or {}
            low = {q: c for q, c in conf.items() if q in th and c < (0.5 + th[q] if q == "cash_flow_stable" else th[q])}
            if low:
                out.append({"decision_id": str(r["decision_id"]), "low": low, "resolver": att.get("resolver"),
                            "model": att.get("model"), "reviewed": att.get("reviewed")})
    return out
