"""Chapter 3 plumbing: the free-form agent loop, recorded, with no model API.

The scripted analyst stands in for a frontier model so CI can check that the
tools return governed figures with record keys, the linter rejects and the
agent retries, decisions are filed, spans carry the GenAI attributes, and the
results table is computed from the run records. The Ch 3 proof itself (the
baseline table and differing step orders) needs a real model; see
docs/ch3-free-form-agent.md and RUNBOOK.md.
"""

from __future__ import annotations

import json

import pytest

pytestmark = pytest.mark.cube


@pytest.fixture()
def results_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("BRIGHTWATER_RESULTS", str(tmp_path))
    import importlib

    from brightwater import bench, tracing
    importlib.reload(bench)
    tracing._provider = None
    return tmp_path


@pytest.fixture(scope="module")
def cube_up(loaded_db):
    from brightwater.metrics_cube import Cube
    try:
        Cube().load({"measures": ["application_month.dscr"], "limit": 1})
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Cube not reachable: {e}")
    from brightwater import db
    with db.session() as conn:
        conn.run((db.SCHEMA_DIR / "003_decisions.sql").read_text())


@pytest.mark.parametrize("app_id", [f"APP-H{i}" for i in range(1, 7)])
def test_scripted_agent_files_the_policy_outcome(cube_up, results_dir, expectations, app_id):
    from brightwater import agent, bench
    from brightwater.scripted import ScriptedAnalyst

    ctx = agent.run(app_id, configuration="test-scripted", model=ScriptedAnalyst(application_id=app_id),
                    model_name="scripted", use_memory=False)
    assert ctx.decision["outcome"] == expectations[app_id]["outcome"]
    assert ctx.decision["status"] == ("pending_person" if expectations[app_id]["requires_person"] else "filed")
    assert ctx.lint_rejections == 1                      # the scripted first draft leaves DSCR uncited
    assert ctx.tool_calls[-2:] == ["file_decision", "file_decision"]
    row = bench.record(ctx)
    assert row["agrees"] and row["turns"] == 8 and row["wall_s"] > 0


def test_spans_follow_genai_conventions(cube_up, results_dir):
    from brightwater import agent, tracing
    from brightwater.scripted import ScriptedAnalyst

    ctx = agent.run("APP-H4", configuration="test-scripted", model=ScriptedAnalyst(application_id="APP-H4"),
                    model_name="scripted", use_memory=False)
    spans = tracing.read_spans(tracing.spans_file(), ctx.trace_id)
    ops = [s["attributes"].get("gen_ai.operation.name") for s in spans]
    assert ops.count("invoke_agent") == 1 and ops.count("chat") == 8 and ops.count("execute_tool") == 7
    chat = next(s for s in spans if s["attributes"].get("gen_ai.operation.name") == "chat")
    assert chat["attributes"]["gen_ai.usage.input_tokens"] > 0
    assert chat["attributes"]["gen_ai.request.model"] == "scripted"


def test_figures_carry_record_keys_and_definition_version(cube_up):
    from brightwater import tools

    ctx = tools.RunContext(application_id="APP-H5", configuration="test")
    out = tools.get_figures(ctx, "APP-H5")
    assert out["figures"]["group_exposure"] == {"value": 1722000, "record": "cube:group_member.group_exposure/APP-H5"}
    assert len(out["semantic_version"]) == 12
    assert "cube:application_month.dscr/APP-H5" in ctx.records


def test_results_table(cube_up, results_dir):
    from brightwater import agent, bench
    from brightwater.scripted import ScriptedAnalyst

    for _ in range(2):
        ctx = agent.run("APP-H4", configuration="test-scripted", model=ScriptedAnalyst(application_id="APP-H4"),
                        model_name="scripted", use_memory=False)
        bench.record(ctx)
    text = bench.table()
    assert "| Outcome agrees with policy |" in text
    assert "test-scripted" in text


@pytest.mark.neo4j
def test_memory_on_without_embeddings_still_records_the_trace(cube_up, results_dir, monkeypatch):
    """Memory is on by default. Before Ch 7 there is no embedder, so the run must still complete
    (the middleware is skipped) and write its reasoning trace to the graph."""
    from neo4j import GraphDatabase

    from brightwater import agent, graph
    from brightwater.scripted import ScriptedAnalyst

    monkeypatch.delenv("BRIGHTWATER_EMBED_URL", raising=False)
    s = graph.settings().neo4j
    auth = (s.username, s.password.get_secret_value())
    try:
        with GraphDatabase.driver(s.uri, auth=auth) as d:
            d.verify_connectivity()
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Neo4j not reachable: {e}")
    ctx = agent.run("APP-H1", configuration="test-scripted", model=ScriptedAnalyst(application_id="APP-H1"),
                    model_name="scripted", use_memory=True)
    assert ctx.decision["outcome"]
    with GraphDatabase.driver(s.uri, auth=auth) as d:
        records, _, _ = d.execute_query(
            "MATCH (rt:ReasoningTrace {session_id: $id})-[:HAS_STEP]->() RETURN count(*) AS n", id=ctx.run_id)
    assert records[0]["n"] > 0


def test_trace_keeps_call_order_when_tools_run_at_the_same_time(monkeypatch):
    """A model can ask for several tools in one turn; LangGraph then runs them concurrently. The
    reasoning trace (ctx.events, read by query 03) must keep the order the calls were made in, not
    the order they finished in. Needs no services: the two tools are stand-ins."""
    import threading

    from brightwater import agent, tools

    slow_started, fast_done = threading.Event(), threading.Event()

    def slow(ctx, **kwargs):
        slow_started.set()
        fast_done.wait(5)
        return {"records": ["slow"]}

    def fast(ctx, **kwargs):
        fast_done.set()
        return {"records": ["fast"]}

    monkeypatch.setitem(tools.TOOLS, "get_application", slow)
    monkeypatch.setitem(tools.TOOLS, "get_figures", fast)
    ctx = tools.RunContext(application_id="APP-H1", configuration="test")
    lc = {t.name: t for t in agent.langchain_tools(ctx)}
    first = threading.Thread(target=lc["get_application"].invoke, args=({"application_id": "APP-H1"},))
    first.start()
    assert slow_started.wait(5)
    lc["get_figures"].invoke({"application_id": "APP-H1"})
    first.join(5)
    assert ctx.tool_calls == ["get_application", "get_figures"]
    assert [e["tool"] for e in ctx.events] == ctx.tool_calls
    assert [e["records"] for e in ctx.events] == [["slow"], ["fast"]]
