"""Chapter 1: load the validated entities into the agent's memory graph.

Two layers write to the same Neo4j database, and they are kept separate on
purpose.

1. Entities go in through neo4j-agent-memory (`long_term.add_entity`), so the
   agent's memory API can find them later: every Business, Person, Facility,
   Application and adverse event becomes an `:Entity` node with its POLE+O
   type (ORGANIZATION, PERSON, OBJECT, EVENT) as a label.

2. Typed edges go in through plain Cypher, because the facts that matter live
   on the edge. In neo4j-agent-memory 0.8.0 `add_relationship` stores
   `(:Entity)-[:RELATED_TO {type: ...}]->(:Entity)` and does not write the
   `attributes` argument (see CREATE_ENTITY_RELATIONSHIP in its
   graph/queries.py). An OWNS edge without its percentage cannot answer a
   control or beneficial-owner question, so we write `OWNS {pct}` ourselves
   and label the nodes with the LinkML class names.

The graph holds what the agent needs to reason and explain. It does not hold
dates of birth (data minimization: nothing downstream needs them) or ledger
months (the semantic layer computes over those in Postgres).
"""

from __future__ import annotations

import asyncio
import csv
import os
import time
from pathlib import Path
from typing import Any

from . import ontology

ROOT = Path(__file__).resolve().parents[2]
QUERIES = ROOT / "queries"


class NoEmbeddings:
    """An embedding provider that refuses to embed.

    Ch 1 loads structured records and needs no vectors, but neo4j-agent-memory
    sizes its vector indexes from the provider at connect time. 384 matches
    the embedding model Ch 7 serves on TEI, so those indexes never need
    rebuilding. Any accidental call fails loudly instead of reaching a cloud
    API.
    """

    model = "none"
    dimensions = 384

    async def embed(self, texts):  # noqa: ANN001
        raise RuntimeError("embeddings are not configured until Ch 7")

    async def embed_one(self, text):  # noqa: ANN001
        raise RuntimeError("embeddings are not configured until Ch 7")


def embedder():
    """Ch 7: a local OpenAI-compatible embeddings server (Ollama on Apple Silicon, TEI on NVIDIA).

    Set BRIGHTWATER_EMBED_URL (e.g. http://localhost:11434/v1) and BRIGHTWATER_EMBED_MODEL
    (default all-minilm, i.e. sentence-transformers/all-MiniLM-L6-v2, Apache-2.0, 384 dimensions).
    Without them, the refusing stub: nothing is ever embedded by a cloud API by accident.
    """
    url = os.getenv("BRIGHTWATER_EMBED_URL")
    if not url:
        return NoEmbeddings()
    from neo4j_agent_memory.llm.adapters.openai import OpenAIEmbeddingProvider

    return OpenAIEmbeddingProvider(model=os.getenv("BRIGHTWATER_EMBED_MODEL", "all-minilm"), api_base=url,
                                   api_key=os.getenv("BRIGHTWATER_EMBED_API_KEY", "local"), dimensions=384)


def settings():
    from neo4j_agent_memory import MemorySettings
    from neo4j_agent_memory.config.settings import ExtractionConfig, ExtractorType

    return MemorySettings(
        backend="bolt",
        neo4j={
            "uri": os.getenv("BRIGHTWATER_NEO4J_URI", "bolt://localhost:7687"),
            "username": os.getenv("BRIGHTWATER_NEO4J_USER", "neo4j"),
            "password": os.getenv("BRIGHTWATER_NEO4J_PASSWORD", "brightwater"),
            "database": os.getenv("BRIGHTWATER_NEO4J_DATABASE", "neo4j"),
        },
        llm=None,
        embedding=embedder(),
        extraction=ExtractionConfig(extractor_type=ExtractorType.NONE, enable_llm_fallback=False),
    )


def _read(src: Path, table: str) -> list[dict[str, str]]:
    with (src / f"{table}.csv").open(newline="", encoding="utf-8") as f:
        return list(csv.DictReader(f))


CONSTRAINTS = [f"CREATE CONSTRAINT {label.lower()}_key IF NOT EXISTS FOR (n:{label}) REQUIRE n.record_key IS UNIQUE"
               for label in ("Business", "Person", "Facility", "Application", "AdverseEvent")] + [
    "CREATE INDEX payment_key IF NOT EXISTS FOR (n:Payment) ON (n.facility_key, n.due_date)",
]

# Domain labels and properties, per LinkML class. Dates become Neo4j dates.
SET_PROPS = {
    "Business": """
        UNWIND $rows AS r MATCH (e:Entity {id: r.id})
        SET e:Business, e.record_key = r.key, e.legal_name = r.legal_name, e.naics = r.naics, e.state = r.state,
            e.established = date(r.established), e.customer_since = date(r.customer_since),
            e.kyc_status = r.kyc_status, e.kyc_next_review_due = date(r.kyc_next_review_due)""",
    "Person": """
        UNWIND $rows AS r MATCH (e:Entity {id: r.id})
        SET e:Person, e.record_key = r.key""",
    "Facility": """
        UNWIND $rows AS r MATCH (e:Entity {id: r.id})
        SET e:Facility, e.record_key = r.key, e.kind = r.kind, e.opened = date(r.opened),
            e.original_amount = toFloat(r.original_amount), e.outstanding_balance = toFloat(r.outstanding_balance),
            e.monthly_payment = toFloat(r.monthly_payment)""",
    "Application": """
        UNWIND $rows AS r MATCH (e:Entity {id: r.id})
        SET e:Application, e.record_key = r.key, e.requested_amount = toFloat(r.requested_amount),
            e.term_months = toInteger(r.term_months), e.annual_rate = toFloat(r.annual_rate),
            e.submitted_on = date(r.submitted_on), e.purpose = r.purpose""",
    "AdverseEvent": """
        UNWIND $rows AS r MATCH (e:Entity {id: r.id})
        SET e:AdverseEvent, e.record_key = r.key, e.occurred_on = date(r.occurred_on), e.kind = r.kind""",
}

EDGES = {
    "OWNS": ("registry.ownership", """
        UNWIND $rows AS r
        MATCH (p:Person {record_key: r.person_id}), (b:Business {record_key: r.business_id})
        MERGE (p)-[o:OWNS]->(b) SET o.pct = toFloat(r.pct)"""),
    "HAS_FACILITY": ("credit.facility", """
        UNWIND $rows AS r
        MATCH (b:Business {record_key: r.business_id}), (f:Facility {record_key: r.facility_id})
        MERGE (b)-[:HAS_FACILITY]->(f)"""),
    "HAS_PAYMENT": ("credit.payment", """
        UNWIND $rows AS r
        MATCH (f:Facility {record_key: r.facility_id})
        MERGE (p:Payment {facility_key: r.facility_id, due_date: date(r.due_date)})
        SET p.amount_due = toFloat(r.amount_due),
            p.paid_date = CASE r.paid_date WHEN '' THEN null ELSE date(r.paid_date) END,
            p.amount_paid = CASE r.amount_paid WHEN '' THEN null ELSE toFloat(r.amount_paid) END
        MERGE (f)-[:HAS_PAYMENT]->(p)"""),
    "APPLIED_FOR": ("credit.application", """
        UNWIND $rows AS r
        MATCH (b:Business {record_key: r.business_id}), (a:Application {record_key: r.application_id})
        MERGE (b)-[:APPLIED_FOR]->(a)"""),
    "HAS_ADVERSE_EVENT": ("compliance.adverse_event", """
        UNWIND $rows AS r
        MATCH (b:Business {record_key: r.business_id}), (e:AdverseEvent {record_key: r.event_id})
        MERGE (b)-[:HAS_ADVERSE_EVENT]->(e)"""),
}


def _entity_specs(src: Path) -> dict[str, list[dict[str, Any]]]:
    """What to hand to add_entity, grouped by LinkML class."""
    kyc = {r["business_id"]: r for r in _read(src, "compliance.kyc")}
    specs: dict[str, list[dict[str, Any]]] = {k: [] for k in SET_PROPS}
    for r in _read(src, "ledger.business"):
        k = kyc.get(r["business_id"], {})
        specs["Business"].append({
            "name": r["legal_name"], "type": "ORGANIZATION", "subtype": "BUSINESS", "key": r["business_id"],
            "props": {**r, "kyc_status": k.get("status"), "kyc_next_review_due": k.get("next_review_due")}})
    for r in _read(src, "registry.person"):
        specs["Person"].append({"name": r["full_name"], "type": "PERSON", "subtype": None,
                                "key": r["person_id"], "props": {}})
    for r in _read(src, "credit.facility"):
        specs["Facility"].append({"name": r["facility_id"], "type": "OBJECT", "subtype": "CREDIT_FACILITY",
                                  "key": r["facility_id"], "props": r})
    for r in _read(src, "credit.application"):
        specs["Application"].append({"name": r["application_id"], "type": "OBJECT", "subtype": "LOAN_APPLICATION",
                                     "key": r["application_id"], "props": r})
    for r in _read(src, "compliance.adverse_event"):
        specs["AdverseEvent"].append({"name": r["event_id"], "type": "EVENT", "subtype": "ADVERSE_EVENT",
                                      "key": r["event_id"], "props": r})
    return specs


async def _add_entities(memory: Any, specs: list[dict[str, Any]], concurrency: int = 16) -> list[dict[str, Any]]:
    sem = asyncio.Semaphore(concurrency)

    async def one(s: dict[str, Any]) -> dict[str, Any]:
        async with sem:
            result = await memory.long_term.add_entity(
                s["name"], s["type"], subtype=s["subtype"],
                attributes={"record_key": s["key"]},
                resolve=False, deduplicate=False, generate_embedding=False, geocode=False, enrich=False)
        entity = result[0] if isinstance(result, tuple) else result
        return {"id": str(entity.id), "key": s["key"], **s["props"]}

    return await asyncio.gather(*(one(s) for s in specs))


async def _write_batches(graph: Any, query: str, rows: list[dict[str, Any]], size: int = 2000) -> None:
    for i in range(0, len(rows), size):
        await graph.execute_write(query, {"rows": rows[i:i + size]})


async def load(src: Path, skip_validation: bool = False) -> dict[str, int]:
    from neo4j_agent_memory import MemoryClient

    if skip_validation:
        print("SHACL: skipped (--skip-validation)")
    else:
        report = ontology.validate_dir(src)
        print(report.summary())
        if not report.conforms:
            raise SystemExit("refusing to load a batch that does not conform; nothing was written to Neo4j")

    t0 = time.perf_counter()
    counts: dict[str, int] = {}
    async with MemoryClient(settings()) as memory:
        for stmt in CONSTRAINTS:
            await memory.graph.execute_write(stmt)
        for label, specs in _entity_specs(src).items():
            rows = await _add_entities(memory, specs)
            await _write_batches(memory.graph, SET_PROPS[label], rows)
            counts[label] = len(rows)
            print(f"  {label:<13} {len(rows):>7,} entities")
        for rel, (table, query) in EDGES.items():
            rows = _read(src, table)
            await _write_batches(memory.graph, query, rows)
            counts[rel] = len(rows)
            print(f"  {rel:<17} {len(rows):>7,} edges")
    print(f"loaded in {time.perf_counter() - t0:.1f}s")
    return counts


def _query_text(name: str) -> str:
    matches = sorted(QUERIES.glob(f"{name}*.cypher"))
    if not matches:
        raise SystemExit(f"no query matching {name!r} in {QUERIES}")
    return matches[0].read_text()


async def run_named_query(name: str, params: list[str]) -> list[dict[str, Any]]:
    from neo4j_agent_memory import MemoryClient

    kv = dict(p.split("=", 1) for p in params)
    async with MemoryClient(settings()) as memory:
        return await memory.graph.execute_read(_query_text(name), kv)


RECORD_DECISION = """
MATCH (app:Application {record_key: $application_id})
MATCH (rt:ReasoningTrace {id: $trace_id})
MERGE (d:Decision {decision_id: $decision_id})
SET d.outcome = $outcome, d.status = $status, d.configuration = $configuration, d.run_id = $run_id,
    d.answered_by = $answered_by, d.semantic_version = $semantic_version, d.policy_version = $policy_version,
    d.cited_records = $cited_records, d.filed_at = datetime()
MERGE (d)-[:DECIDES]->(app)
MERGE (rt)-[:PRODUCED]->(d)
"""


async def record_run(ctx: Any) -> str:
    """Write one run as a reasoning trace (steps in call order) and link its filed decision.

    neo4j-agent-memory stores the trace as (:ReasoningTrace)-[:HAS_STEP {order}]->(:ReasoningStep)
    -[:USES_TOOL]->(:ToolCall)-[:INSTANCE_OF]->(:Tool), which is what query 03 reads to compare
    step orders across runs.
    """
    from neo4j_agent_memory import MemoryClient

    async with MemoryClient(settings()) as memory:
        rt = await memory.reasoning.start_trace(
            ctx.run_id, f"assess {ctx.application_id}", generate_embedding=False,
            metadata={"configuration": ctx.configuration, "application_id": ctx.application_id,
                      "model": ctx.model, "otel_trace_id": ctx.trace_id})
        for i, e in enumerate(ctx.events):
            step = await memory.reasoning.add_step(
                rt.id, action=e["tool"], observation=f"{len(e['records'])} record(s); ok={e['ok']}",
                generate_embedding=False, metadata={"order": i})
            await memory.reasoning.record_tool_call(step.id, e["tool"], {k: str(v)[:500] for k, v in e["args"].items()},
                                                    result={"records": e["records"][:50]}, duration_ms=e["ms"])
        outcome = (ctx.decision or {}).get("outcome", "not filed")
        await memory.reasoning.complete_trace(rt.id, outcome=outcome, success=ctx.decision is not None)
        await memory.graph.execute_write(
            "MATCH (rt:ReasoningTrace {id: $id}) SET rt.application_id = $app, rt.configuration = $conf",
            {"id": str(rt.id), "app": ctx.application_id, "conf": ctx.configuration})
        if ctx.decision:
            d = ctx.decision
            await memory.graph.execute_write(RECORD_DECISION, {
                **{k: d[k] for k in ("application_id", "decision_id", "outcome", "status", "configuration", "run_id",
                                     "answered_by", "semantic_version", "policy_version", "cited_records")},
                "trace_id": str(rt.id)})
        return str(rt.id)
