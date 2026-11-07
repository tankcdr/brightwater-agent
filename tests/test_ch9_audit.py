"""Chapter 9 proof: reconstruct any decision from its id; a tampered packet
fails chain verification; the two governance queries."""

from __future__ import annotations

import threading

import pytest

from brightwater import audit, db, fake_dm, procedure, resolver

pytestmark = pytest.mark.cube


@pytest.fixture(scope="module")
def ready(loaded_db):
    from brightwater.metrics_cube import Cube
    try:
        Cube().load({"measures": ["application_month.dscr"], "limit": 1})
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Cube not reachable: {e}")
    for f in ("003_decisions.sql", "004_audit.sql"):
        loaded_db.run((db.SCHEMA_DIR / f).read_text())
    loaded_db.run("SELECT 1")
    from brightwater import cli
    cli.main(["policy", "sync"])


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("AIP_HOME", str(tmp_path / "aip"))
    monkeypatch.setenv("BRIGHTWATER_RESULTS", str(tmp_path / "results"))
    monkeypatch.delenv("AIP_SERVER", raising=False)


def run(app, **kw):
    return procedure.run(app, procedure.ScriptedAnswerer(), procedure.auto_person("officer-9"),
                         configuration="test-audit", skill=str(procedure.SKILL_DIR), **kw)


def test_packet_reconstructs_the_decision(ready, env):
    r = run("APP-H6")
    p = audit.packet(r.ctx.decision["decision_id"])["packet"]
    assert p["procedure"]["name"] == "brightwater-loan-assessment"  # revision is set on published runs
    assert p["steps"][-3:] == ["confirm-with-person", "record-confirmation", "end"]
    assert p["who"]["confirmed_by"] == "user:officer-9" and p["who"]["answered_by"] == "agent:scripted"
    assert p["outcome"]["outcome"] == "decline" and p["outcome"]["lint"] == "pass"
    assert p["judgment"]["attribution"]["resolver"] == "manual"
    assert "cube:application_month.dscr/APP-H6" in p["records"]["read"]
    assert {r["rule_id"] for r in p["policy"]} == {f"P{i}" for i in range(1, 8)}   # every rule in force, none missing
    # The judgment step saw figures only.
    assert "legal_name" not in str(p["judgment"]["input"]) and "date_of_birth" not in str(p["judgment"]["input"])
    assert audit.verify()["ok"]


def test_tampering_breaks_the_chain(ready, env):
    b, a = run("APP-H1"), run("APP-H3")          # tamper with APP-H3: a review, rewritten as an approval
    assert audit.verify()["ok"]
    with db.session() as conn:
        conn.run("""UPDATE audit.packet SET packet = jsonb_set(packet, '{outcome,outcome}', '"approve"')
                    WHERE decision_id = CAST(:d AS uuid)""", d=a.ctx.decision["decision_id"])
    v = audit.verify()
    assert not v["ok"] and v["decision_id"] == a.ctx.decision["decision_id"] and v["why"] == "packet edited"
    # Rewriting the stored hash too only moves the failure to the chain.
    with db.session() as conn:
        p = db.rows(conn, "SELECT packet FROM audit.packet WHERE decision_id = CAST(:d AS uuid)",
                    d=a.ctx.decision["decision_id"])[0]["packet"]
        conn.run("UPDATE audit.packet SET packet_sha256 = :h WHERE decision_id = CAST(:d AS uuid)",
                 h=audit.sha256(audit.canonical(p)), d=a.ctx.decision["decision_id"])
    v = audit.verify()
    assert not v["ok"] and v["why"] == "chain broken"
    with db.session() as conn:  # leave a clean chain for other tests
        conn.run("TRUNCATE audit.packet")


def test_under_threshold_query_finds_unsure_model_answers(ready, env):
    srv = fake_dm.serve(18082)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        client = resolver.EndpointDecisionClient("http://127.0.0.1:18082/v1", "fake", version="dm@test")
        for app in [f"APP-{i:05d}" for i in range(1, 25)]:
            procedure.run(app, procedure.ScriptedAnswerer(), procedure.auto_person(), configuration="test-audit",
                          skill=str(procedure.SKILL_DIR), decision_client=client)
    finally:
        srv.shutdown()
    rows = audit.under_threshold()
    assert rows and all(r["resolver"] == "endpoint" and r["reviewed"] for r in rows)
    assert audit.verify()["ok"]


def test_audit_output_is_valid_json_with_status_on_stderr(monkeypatch, capsys):
    """RUNBOOK saves `brightwater audit ... | tee results/live/ch9-*.json`, so stdout must be JSON alone."""
    import argparse
    import json

    from brightwater import cli
    monkeypatch.setattr(audit, "packet", lambda d: {"packet": {"who": {}}, "seq": 1, "chain_hash": "ab" * 32})
    monkeypatch.setattr(audit, "verify", lambda: {"ok": True, "packets": 1, "head": "ab" * 32})
    monkeypatch.setattr(audit, "overridden", lambda: [])
    cli.cmd_audit(argparse.Namespace(query=None, seal=None, decision_id="x"))
    out = capsys.readouterr()
    assert json.loads(out.out) == {"who": {}} and "chain verified: 1 packet(s)" in out.err
    cli.cmd_audit(argparse.Namespace(query="overridden", seal=None, decision_id=None))
    out = capsys.readouterr()
    assert json.loads(out.out) == [] and "0 decision(s)" in out.err
