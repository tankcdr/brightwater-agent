"""Chapter 10 proof: one full cycle on new data, with a promotion or a recorded
rejection, and the model version on every decision it made.

Training is stubbed (Apple Silicon only); the stand-in server plays a good
candidate and a degraded one.
"""

from __future__ import annotations

import json
import threading

import pytest

from brightwater import decision_model as dm
from brightwater import fake_dm, loop, procedure, resolver

pytestmark = pytest.mark.cube


@pytest.fixture(scope="module")
def setup(loaded_db, tmp_path_factory):
    from brightwater.metrics_cube import Cube
    try:
        Cube().load({"measures": ["application_month.dscr"], "limit": 1})
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Cube not reachable: {e}")
    d = tmp_path_factory.mktemp("loop")
    dm.DATA, dm.ART = d / "data", d / "artifacts"
    dm.write_jsonl(dm.DATA / "states.jsonl", dm.build_states())
    dm.label_all("rubric")
    servers = {}
    for name, port, wrong in (("good", 18091, 17), ("worse", 18092, 3)):
        srv = fake_dm.serve(port, wrong_every=wrong)
        threading.Thread(target=srv.serve_forever, daemon=True).start()
        servers[name] = (srv, f"http://127.0.0.1:{port}/v1")
    yield servers
    for srv, _ in servers.values():
        srv.shutdown()


def test_first_cycle_promotes_then_a_worse_candidate_is_rejected(setup):
    servers = setup
    quality = {"next": "good"}

    def train(candidate_dir):
        (candidate_dir / "adapters.safetensors").write_bytes(b"stub")
        return f"brightwater-dm@{candidate_dir.name}-{quality['next']}"

    def serve(version, adapters):
        url = servers["worse" if version.endswith("worse") else "good"][1]
        return resolver.EndpointDecisionClient(url, "fake", version=version)

    first = loop.cycle(train, serve, limit=60)
    assert first["status"] == "promoted" and first["reason"] == "first model"
    quality["next"] = "worse"
    import time
    time.sleep(1.1)  # candidate folders are named by second
    second = loop.cycle(train, serve, limit=60)
    assert second["status"] == "rejected" and "agreement" in second["reason"]
    assert loop.current()["version"] == first["version"]
    reg = [json.loads(line) for line in loop.registry_path().read_text().splitlines()]
    assert [r["status"] for r in reg] == ["promoted", "rejected"]


def test_the_promoted_version_is_on_every_decision_it_makes(setup, tmp_path, monkeypatch):
    monkeypatch.setenv("AIP_HOME", str(tmp_path / "aip"))
    monkeypatch.setenv("BRIGHTWATER_RESULTS", str(tmp_path / "results"))
    monkeypatch.delenv("AIP_SERVER", raising=False)
    version = loop.current()["version"]
    client = resolver.EndpointDecisionClient(setup["good"][1], "fake", version=version)
    r = procedure.run("APP-00007", procedure.ScriptedAnswerer(), procedure.auto_person(), configuration="test-loop",
                      skill=str(procedure.SKILL_DIR), decision_client=client)
    assert r.ctx.decision["answered_by"] == f"model:endpoint/{version}"


def test_the_chapter6_model_is_registered_before_the_first_cycle(tmp_path, monkeypatch):
    """Production before any cycle is the Ch 6 model: the first candidate is compared with it."""
    monkeypatch.setattr(dm, "ART", tmp_path / "artifacts")
    (dm.ART / "adapters").mkdir(parents=True)
    (dm.ART / "model.json").write_text(json.dumps({"version": "brightwater-dm@ch6", "trained_at": "2026-11-01T00:00:00Z"}))
    entry = loop._register_chapter6_model()
    assert entry["version"] == "brightwater-dm@ch6" and loop.current()["version"] == "brightwater-dm@ch6"
    assert loop._register_chapter6_model() is None          # only once


def test_automatic_review_answers_are_not_training_labels(tmp_path, monkeypatch):
    from brightwater import procedure

    monkeypatch.setenv("BRIGHTWATER_RESULTS", str(tmp_path))
    pause = type("P", (), {"step": "assess", "proposed_answers": {"risk_tier": 2}, "review": [{"question": "risk_tier"}]})()
    procedure._record_review("APP-H3", pause, {}, "auto:chris")
    procedure._record_review("APP-H3", pause, {"risk_tier": 3}, "user:chris")
    assert [r["answered_by"] for r in loop.new_review_answers(None)] == ["user:chris"]


def test_training_progress_stays_off_stdout(monkeypatch, tmp_path):
    """RUNBOOK saves `brightwater dm cycle | tee results/live/ch10-cycle.json`, so stdout is the JSON alone."""
    import sys
    seen = {}

    def fake_run(cmd, **kw):
        seen.update(kw)
        (tmp_path / "model.json").write_text('{"version": "brightwater-dm@test"}')

    monkeypatch.setattr(loop.subprocess, "run", fake_run)
    assert loop.mlx_trainer(tmp_path) == "brightwater-dm@test"
    assert seen["stdout"] is sys.stderr
