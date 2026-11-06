"""Chapter 1 proof, part 1: a deliberately broken fixture fails SHACL
validation before it reaches the graph."""

from __future__ import annotations

import asyncio

import pytest

from brightwater import fixture, graph, ontology


@pytest.fixture(scope="module")
def small(tmp_path_factory):
    out = tmp_path_factory.mktemp("small")
    fixture.write_csv(fixture.build(seed=7, n=50), out)
    return out


@pytest.fixture(scope="module")
def broken(small, tmp_path_factory):
    out = tmp_path_factory.mktemp("broken")
    fixture.break_fixture(small, out)
    return out


def test_clean_fixture_conforms(small):
    report = ontology.validate_dir(small)
    assert report.conforms, report.summary()


def test_broken_fixture_is_rejected_with_both_defects_named(broken):
    report = ontology.validate_dir(broken)
    assert not report.conforms
    found = {(v.focus, v.message) for v in report.violations}
    assert ("BUS-H01", "Ownership stakes in this business add up to 135.00%, more than 100%.") in found
    assert ("ownership(PER-H01 -> BUS-99999)", "Value does not have class schema:Organization") in found
    assert len(report.violations) == 2


def test_postgres_would_have_accepted_both_rows():
    """Why the gate is needed: neither defect violates a Postgres constraint."""
    ddl = (ontology.ROOT / "schema" / "postgres" / "001_systems.sql").read_text()
    ownership = ddl.split("CREATE TABLE registry.ownership")[1].split(");")[0]
    assert "REFERENCES ledger.business" not in ownership  # separate systems, no cross-schema FK
    assert "sum(" not in ownership.lower()


def test_load_refuses_before_touching_neo4j(broken, monkeypatch):
    import neo4j_agent_memory

    def explode(*a, **k):
        raise AssertionError("MemoryClient must not be constructed for a non-conforming batch")

    monkeypatch.setattr(neo4j_agent_memory, "MemoryClient", explode)
    with pytest.raises(SystemExit, match="refusing to load"):
        asyncio.run(graph.load(broken))
