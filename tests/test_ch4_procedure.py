"""Chapter 4: the compiled AIP procedure.

With the scripted driver these tests check the procedure itself: it
validates, every hand-built applicant gets the policy's outcome, repeated runs
take identical steps and read identical records, the person-only step never
reaches the answerer, and the filed decision carries who answered and the AIP
run id. The live proof (a frontier driver, Ch 3 vs Ch 4 table) is in
docs/ch4-compiled-procedure.md.
"""

from __future__ import annotations

import pytest

from brightwater import procedure

pytestmark = pytest.mark.cube


def test_skill_validates():
    from aip_spec import validate_skill
    loaded, issues = validate_skill(str(procedure.SKILL_DIR))
    assert loaded is not None and not [i for i in issues if getattr(i, "severity", None) != "warning"], issues


class NoPersonSteps(procedure.ScriptedAnswerer):
    """Fails the test if the procedure ever hands it a person-only task."""

    def rationale(self, pause, errors):
        assert pause.step not in procedure.PERSON_STEPS
        return super().rationale(pause, errors)


@pytest.fixture(scope="module")
def ready(loaded_db):
    from brightwater import db
    from brightwater.metrics_cube import Cube
    try:
        Cube().load({"measures": ["application_month.dscr"], "limit": 1})
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Cube not reachable: {e}")
    with db.session() as conn:
        conn.run((db.SCHEMA_DIR / "003_decisions.sql").read_text())


@pytest.fixture()
def local_runs(tmp_path, monkeypatch):
    monkeypatch.setenv("AIP_HOME", str(tmp_path / "aip"))
    monkeypatch.setenv("BRIGHTWATER_RESULTS", str(tmp_path / "results"))
    monkeypatch.delenv("AIP_SERVER", raising=False)


@pytest.mark.parametrize("app_id", [f"APP-H{i}" for i in range(1, 7)])
def test_procedure_outcome_and_person_routing(ready, local_runs, expectations, app_id):
    asked = []
    person = lambda pause: asked.append(pause.step) or {"confirmed": True, "confirmed_by": "officer-7",  # noqa: E731
                                                         "confirmation_note": "ok"}
    r = procedure.run(app_id, NoPersonSteps(), person, configuration="test-procedure", skill=str(procedure.SKILL_DIR))
    expect = expectations[app_id]
    assert r.ctx.decision["outcome"] == expect["outcome"]
    assert asked == (["confirm-with-person"] if expect["requires_person"] else [])
    assert r.ctx.decision["answered_by"] == "agent:scripted"
    assert r.ctx.decision["run_id"] == r.aip_run_id
    if expect["requires_person"]:
        assert r.ctx.decision["confirmed_by"] == "user:officer-7"


def test_repeated_runs_take_identical_steps_and_read_identical_records(ready, local_runs):
    runs = [procedure.run("APP-H4", procedure.ScriptedAnswerer(), procedure.auto_person(), configuration="test",
                          skill=str(procedure.SKILL_DIR)) for _ in range(3)]
    assert len({tuple(r.steps) for r in runs}) == 1
    assert len({tuple(sorted(r.ctx.records)) for r in runs}) == 1
    assert runs[0].steps == ["gather", "apply-policy", "assess", "draft-rationale", "file", "needs-person"]


@pytest.mark.neo4j
def test_procedure_run_writes_its_steps_to_the_memory_graph(ready, local_runs, monkeypatch):
    """With memory on (the default outside tests), a procedure run records the same reasoning trace
    a Ch 3 agent run does, steps in order, which is what query 03 compares."""
    from neo4j import GraphDatabase

    from brightwater import graph

    s = graph.settings().neo4j
    auth = (s.username, s.password.get_secret_value())
    try:
        with GraphDatabase.driver(s.uri, auth=auth) as d:
            d.verify_connectivity()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Neo4j not reachable: {e}")
    monkeypatch.setenv("BRIGHTWATER_MEMORY", "on")
    r = procedure.run("APP-H1", procedure.ScriptedAnswerer(), procedure.auto_person(), configuration="test-memory",
                      skill=str(procedure.SKILL_DIR))
    with GraphDatabase.driver(s.uri, auth=auth) as d:
        records, _, _ = d.execute_query(
            "MATCH (rt:ReasoningTrace {session_id: $id})-[hs:HAS_STEP]->(:ReasoningStep)-[:USES_TOOL]->(:ToolCall)"
            "-[:INSTANCE_OF]->(t:Tool) WITH hs.order AS o, t.name AS name ORDER BY o RETURN collect(name) AS seq",
            id=r.ctx.run_id)
    assert records[0]["seq"] == r.steps


class UncitedFigures(procedure.ScriptedAnswerer):
    """A model that never cites its figures: every rationale it writes fails the linter."""

    def rationale(self, pause, errors):
        return f"Outcome: {pause.state['rule_outcome']}\nDSCR is 1.17."


def test_a_rationale_that_never_passes_the_linter_is_not_filed_and_the_run_returns(ready, local_runs):
    import os

    from brightwater import db
    r = procedure.run("APP-H4", UncitedFigures(), procedure.auto_person(), configuration="test-unfiled",
                      skill=str(procedure.SKILL_DIR))
    assert r.ctx.decision is None and "file" not in r.steps
    assert r.ctx.lint_rejections == procedure.MAX_RATIONALE_ATTEMPTS
    assert "BRIGHTWATER_CONFIGURATION" not in os.environ          # the run's environment was restored
    with db.session() as conn:
        assert conn.run("SELECT count(*) FROM agent.decision WHERE configuration = 'test-unfiled'")[0][0] == 0


def test_export_traces_writes_outside_the_skill(tmp_path, monkeypatch):
    """The recorded runs are evidence to check the procedure against, not part of it: exporting them
    must not change the skill folder, or every reader's revision id would differ."""
    import importlib
    import json

    from brightwater import bench

    monkeypatch.setenv("BRIGHTWATER_RESULTS", str(tmp_path))
    importlib.reload(bench)
    try:
        (tmp_path / "runs.jsonl").write_text(json.dumps({
            "configuration": "ch3-freeform", "application_id": "APP-H1", "outcome": "approve", "agrees": True,
            "lint_rejections": 0, "tool_sequence": ["get_figures", "file_decision"]}) + "\n")
        out = procedure.export_traces()
        assert out == tmp_path / "traces.md"
        assert "get_figures > file_decision" in out.read_text()
        assert procedure.SKILL_DIR not in out.parents
    finally:
        monkeypatch.undo()
        importlib.reload(bench)


def test_json_from_tolerates_prose_fences_and_braces():
    from brightwater.procedure import _json_from

    assert _json_from('{"a": 1}') == {"a": 1}
    assert _json_from('Using {risk_tier} from the rubric:\n```json\n{"risk_tier": 2}\n```') == {"risk_tier": 2}
    assert _json_from('{"assessment": {"tier": 1}, "ok": true} That is my answer.') == {"assessment": {"tier": 1}, "ok": True}
    assert _json_from('Draft {"x": 1}. Final: {"x": 2}') == {"x": 2}
    with pytest.raises(ValueError):
        _json_from("no object {here}")
