"""Chapter 1 proof, part 2: one graph query walks applicant, owner, owner's
other business, late facility. Needs the neo4j-memory service."""

from __future__ import annotations

import asyncio

import pytest

from brightwater import graph

pytestmark = pytest.mark.neo4j


@pytest.fixture(scope="module")
def loaded_graph(fixture_dir):
    from neo4j import GraphDatabase

    s = graph.settings().neo4j
    try:
        with GraphDatabase.driver(s.uri, auth=(s.username, s.password.get_secret_value())) as d:
            d.verify_connectivity()
            d.execute_query("MATCH (n) DETACH DELETE n")
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Neo4j not reachable: {e}")
    return asyncio.run(graph.load(fixture_dir))


def q(name, **params):
    return asyncio.run(graph.run_named_query(name, [f"{k}={v}" for k, v in params.items()]))


def test_counts_match_the_systems_of_record(loaded_graph):
    assert loaded_graph["Application"] == 1206
    assert loaded_graph["OWNS"] == 2798


def test_northpine_path_reaches_ridgeback_late_facility(loaded_graph):
    rows = q("01", app="APP-H4")
    assert rows[0] == {
        "application": "APP-H4",
        "applicant": "Northpine Freight LLC",
        "owner": "Priya Lindqvist",
        "stake_in_applicant": 40.0,
        "other_business": "Ridgeback Tile Co",
        "stake_in_other": 35.0,
        "facility": "FAC-H90-1",
        "days_past_due": 80,
    }


def test_clean_applicant_has_no_such_path(loaded_graph):
    assert q("01", app="APP-H1") == []
