"""Chapter 1: the entity schema and the import gate.

The LinkML schema in `schema/linkml/brightwater.yaml` is the one definition of
what a Business, Person, Ownership, Facility, Payment, Application, KYC review
and adverse event are. From it we generate SHACL shapes; to them we add the
cross-record rules in `schema/shacl/extra.ttl`. Every import into the memory
graph goes through `validate_dir` first, and a batch that does not conform is
not loaded at all.

Why SHACL and not just Postgres constraints? The systems of record are five
separate applications. Postgres can enforce that a payment points at a
facility in the same schema; it cannot enforce that an ownership stake in the
registry points at a business the ledger knows about, or that the stakes in
one business add up to 100% or less across everything we import. Those are
graph-shaped facts, so they are checked where the graph is built.
"""

from __future__ import annotations

import csv
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Any

import pyshacl
import rdflib

ROOT = Path(__file__).resolve().parents[2]
SCHEMA = ROOT / "schema" / "linkml" / "brightwater.yaml"
EXTRA_SHAPES = ROOT / "schema" / "shacl" / "extra.ttl"
DATA_BASE = "https://brightwater.example/id/"
SH = rdflib.Namespace("http://www.w3.org/ns/shacl#")


@lru_cache(maxsize=1)
def _model():
    """Compile the LinkML schema into Python classes once per process."""
    from linkml.generators.pythongen import PythonGenerator
    from linkml_runtime.utils.schemaview import SchemaView

    return PythonGenerator(str(SCHEMA)).compile_module(), SchemaView(str(SCHEMA))


@lru_cache(maxsize=1)
def shapes() -> rdflib.Graph:
    from linkml.generators.shaclgen import ShaclGenerator

    g = rdflib.Graph()
    g.parse(data=ShaclGenerator(str(SCHEMA)).serialize(), format="turtle")
    g.parse(EXTRA_SHAPES, format="turtle")
    g.bind("schema", "http://schema.org/", override=True, replace=True)
    return g


def _read(src: Path, table: str) -> list[dict[str, str]]:
    with (src / f"{table}.csv").open(newline="", encoding="utf-8") as f:
        return [{k: v for k, v in r.items() if v != ""} for r in csv.DictReader(f)]


def bank_from_csv(src: Path) -> Any:
    """Map the systems-of-record CSVs onto the schema's classes.

    This mapping is the only place that knows both vocabularies: column names
    on one side, schema slots on the other.
    """
    m, _ = _model()
    return m.Bank(
        businesses=[m.Business(id=r["business_id"], legal_name=r["legal_name"], naics=r["naics"],
                               state=r["state"], established=r["established"], customer_since=r["customer_since"])
                    for r in _read(src, "ledger.business")],
        people=[m.Person(id=r["person_id"], full_name=r["full_name"], date_of_birth=r["date_of_birth"])
                for r in _read(src, "registry.person")],
        ownerships=[m.Ownership(owner=r["person_id"], business=r["business_id"], pct=r["pct"])
                    for r in _read(src, "registry.ownership")],
        facilities=[m.Facility(id=r["facility_id"], business=r["business_id"], kind=r["kind"], opened=r["opened"],
                               original_amount=r["original_amount"], outstanding_balance=r["outstanding_balance"],
                               monthly_payment=r["monthly_payment"])
                    for r in _read(src, "credit.facility")],
        payments=[m.Payment(facility=r["facility_id"], due_date=r["due_date"], amount_due=r["amount_due"],
                            paid_date=r.get("paid_date"), amount_paid=r.get("amount_paid"))
                  for r in _read(src, "credit.payment")],
        applications=[m.Application(id=r["application_id"], business=r["business_id"],
                                    requested_amount=r["requested_amount"], term_months=int(r["term_months"]),
                                    annual_rate=r["annual_rate"], submitted_on=r["submitted_on"], purpose=r["purpose"])
                      for r in _read(src, "credit.application")],
        kyc_reviews=[m.KycReview(business=r["business_id"], status=r["status"], last_reviewed=r["last_reviewed"],
                                 next_review_due=r["next_review_due"])
                     for r in _read(src, "compliance.kyc")],
        adverse_events=[m.AdverseEvent(id=r["event_id"], business=r["business_id"], occurred_on=r["occurred_on"],
                                       kind=r["kind"], summary=r["summary"])
                        for r in _read(src, "compliance.adverse_event")],
    )


def to_rdf(bank: Any) -> rdflib.Graph:
    from linkml_runtime.dumpers import rdflib_dumper

    _, sv = _model()
    return rdflib_dumper.as_rdf_graph(bank, schemaview=sv, prefix_map={"@base": DATA_BASE})


@dataclass
class Violation:
    focus: str
    path: str
    message: str


@dataclass
class Report:
    conforms: bool
    triples: int
    seconds: float
    violations: list[Violation]

    def summary(self, limit: int = 10) -> str:
        head = (f"SHACL: {'conforms' if self.conforms else 'DOES NOT CONFORM'} "
                f"({self.triples:,} triples, {self.seconds:.1f}s, {len(self.violations)} violation(s))")
        lines = [head]
        for v in self.violations[:limit]:
            lines.append(f"  - {v.focus}  {v.path}  {v.message}")
        if len(self.violations) > limit:
            lines.append(f"  ... and {len(self.violations) - limit} more")
        return "\n".join(lines)


def _short(node: rdflib.term.Node | None) -> str:
    if node is None:
        return ""
    if isinstance(node, rdflib.BNode):
        return "(record)"
    s = str(node)
    for prefix in (DATA_BASE, "https://brightwater.example/schema/"):
        if s.startswith(prefix):
            return s[len(prefix):]
    return s


def validate_graph(data: rdflib.Graph) -> Report:
    t0 = time.perf_counter()
    data.bind("schema", "http://schema.org/", override=True, replace=True)
    data.bind("bw", "https://brightwater.example/schema/", override=True, replace=True)
    conforms, results, _ = pyshacl.validate(data, shacl_graph=shapes(), advanced=True, allow_warnings=False)
    violations = []
    for r in results.subjects(rdflib.RDF.type, SH.ValidationResult):
        focus = results.value(r, SH.focusNode)
        # For blank-node records (an Ownership), name the business it points at.
        if isinstance(focus, rdflib.BNode):
            biz = data.value(focus, rdflib.URIRef("https://brightwater.example/schema/business"))
            owner = data.value(focus, rdflib.URIRef("https://brightwater.example/schema/owner"))
            label = "ownership" if owner is not None else "record"
            focus_s = f"{label}({_short(owner)} -> {_short(biz)})" if biz is not None else "(record)"
        else:
            focus_s = _short(focus)
        violations.append(Violation(focus_s, _short(results.value(r, SH.resultPath)),
                                    str(results.value(r, SH.resultMessage))))
    violations.sort(key=lambda v: (v.focus, v.path, v.message))
    return Report(bool(conforms), len(data), time.perf_counter() - t0, violations)


def validate_dir(src: Path) -> Report:
    return validate_graph(to_rdf(bank_from_csv(src)))
