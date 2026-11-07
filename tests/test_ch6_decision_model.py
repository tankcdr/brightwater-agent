"""Chapter 6 plumbing: dataset, labels, fine-tuning export, calibration, the
endpoint resolver, AIP attribution, and the A-to-D evaluation harness.

A stand-in server (`brightwater.fake_dm`) plays the fine-tuned model so this
runs with no GPU. Real training and serving are on Apple Silicon with MLX-LM;
see docs/ch6-own-the-decision-model.md.
"""

from __future__ import annotations

import json
import threading

import pytest

from brightwater import decision_model as dm
from brightwater import fake_dm, procedure, resolver

pytestmark = pytest.mark.cube


@pytest.fixture(scope="module")
def server():
    srv = fake_dm.serve(18081)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield "http://127.0.0.1:18081/v1"
    srv.shutdown()


@pytest.fixture(scope="module")
def data(loaded_db, tmp_path_factory):
    from brightwater.metrics_cube import Cube
    try:
        Cube().load({"measures": ["application_month.dscr"], "limit": 1})
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Cube not reachable: {e}")
    d = tmp_path_factory.mktemp("dm")
    dm.DATA, dm.ART = d / "data", d / "artifacts"
    rows = dm.build_states()
    dm.write_jsonl(dm.DATA / "states.jsonl", rows)
    return rows


def test_splits_hold_out_h6_and_never_split_an_application(data):
    split = {r["application_id"]: r["split"] for r in data}
    assert split["APP-H6"] == "test"
    counts = {s: sum(1 for v in split.values() if v == s) for s in ("train", "valid", "test")}
    assert counts["train"] > counts["test"] > counts["valid"] > 100


def test_labels_and_export(data):
    out = dm.label_all("rubric")
    assert out["labelled"] == len(data) and out["agrees_with_reference_rubric"] == len(data)
    sizes = dm.export()
    assert sizes["train"] == 3 * sum(1 for r in data if r["split"] == "train")
    first = json.loads((dm.DATA / "mlx" / "train.jsonl").read_text().splitlines()[0])
    assert first["messages"][1]["content"] in "ABCD" and "Answer with the letter only." in first["messages"][0]["content"]


def test_temperature_scaling_lowers_validation_nll(data, server):
    """Temperature scaling minimizes NLL. It usually lowers ECE too, but is not guaranteed to."""
    client = resolver.EndpointDecisionClient(server, "fake")
    report = dm.calibrate(client)
    for name, r in report.items():
        assert r["nll_after"] <= r["nll_before"] + 1e-9, (name, r)
    assert json.loads((dm.ART / "calibration.json").read_text())["temperature"].keys() == report.keys()


def test_endpoint_resolver_is_recorded_under_its_own_name(data, server, tmp_path, monkeypatch):
    from brightwater import aip_patch
    aip_patch.apply()
    monkeypatch.setenv("AIP_HOME", str(tmp_path / "aip"))
    monkeypatch.setenv("BRIGHTWATER_RESULTS", str(tmp_path / "results"))
    monkeypatch.delenv("AIP_SERVER", raising=False)
    sure = {q: 0.05 for q in ("risk_tier", "cash_flow_stable", "recommendation")}  # sharpens: no review
    client = resolver.EndpointDecisionClient(server, "fake", calibration=sure, version="brightwater-dm@test")
    r = procedure.run("APP-H3", procedure.ScriptedAnswerer(), procedure.auto_person(), configuration="test-D",
                      skill=str(procedure.SKILL_DIR), decision_client=client)
    assert r.decision_pauses == 0 and r.review_pauses == 0   # the model answered; no agent, no person
    assert r.ctx.decision["answered_by"] == "model:endpoint/brightwater-dm@test"
    import aip
    record = aip.Client().run_record(r.aip_run_id)
    assess = next(h for h in record.history if h["step"] == "assess")
    assert assess["attribution"]["resolver"] == "endpoint"
    assert set(assess["attribution"]["confidence"]) == {"risk_tier", "cash_flow_stable", "recommendation"}


def test_a_reviewed_answer_is_filed_as_the_reviewers(data, server, tmp_path, monkeypatch):
    """When the model is unsure and a person answers the review, the person is who decided."""
    from brightwater import aip_patch
    aip_patch.apply()
    monkeypatch.setenv("AIP_HOME", str(tmp_path / "aip"))
    monkeypatch.setenv("BRIGHTWATER_RESULTS", str(tmp_path / "results"))
    monkeypatch.delenv("AIP_SERVER", raising=False)
    # A very high temperature flattens the model's probabilities, so the answer falls under the threshold.
    unsure = resolver.EndpointDecisionClient(server, "fake", calibration={"cash_flow_stable": 50.0},
                                             version="brightwater-dm@test")
    r = procedure.run("APP-H3", procedure.ScriptedAnswerer(), procedure.auto_person("officer-7"),
                      configuration="test-D-review", skill=str(procedure.SKILL_DIR), decision_client=unsure)
    assert r.review_pauses == 1
    assert r.ctx.decision["answered_by"] == "auto:officer-7"


def test_evaluation_harness(data, server):
    labels = dm.final_labels()
    option_a = lambda state, qs: labels[state["application_id"]]  # noqa: E731  (a perfect agent, for the shape)
    res = dm.evaluate({"A": option_a, "D": resolver.EndpointDecisionClient(server, "fake")}, repeats=2,
                      stability_n=5, limit=60)
    assert res["A"]["pause_share"] == 1.0 and res["A"]["agreement"]["risk_tier"] == 1.0
    assert res["A"]["resolver"] == "manual" and res["D"]["resolver"] == "endpoint"   # as AIP records them
    assert 0.8 < res["D"]["agreement"]["risk_tier"] <= 1.0
    assert res["D"]["max_confidence_spread_across_repeats"] == 0.0   # same prompt, same answer


def test_a_person_can_answer_a_review_at_the_console(monkeypatch):
    """A review pause reaches the terminal as one prompt per flagged answer; enter keeps the model's."""
    from types import SimpleNamespace

    from brightwater import procedure

    pause = SimpleNamespace(kind="review", step="assess",
                            proposed_answers={"risk_tier": 2, "cash_flow_stable": True, "recommendation": "review"},
                            review=[{"question": "risk_tier", "reason": "confidence 0.41 below 0.6"},
                                    {"question": "cash_flow_stable", "reason": "confidence 0.55 below 0.7"}])
    answers = iter(["3", ""])
    monkeypatch.setattr("builtins.input", lambda prompt="": next(answers))
    assert procedure.console_person(pause) == {"risk_tier": 3}



def test_frontier_labels_survive_a_bad_reply_and_are_reused(data, monkeypatch):
    """A paid labeller: one malformed reply is retried, every label is saved as it arrives, and a
    second run makes no calls at all."""
    from brightwater import rubric

    saved = (dm.DATA / "labels.jsonl").read_text() if (dm.DATA / "labels.jsonl").exists() else None
    calls: dict[str, int] = {}

    def flaky(state):
        key = json.dumps(state, sort_keys=True, default=str)
        calls[key] = calls.get(key, 0) + 1
        if calls[key] == 1:
            raise ValueError("no JSON object in model output")
        return rubric.label(state)

    monkeypatch.setattr(dm, "frontier_labeller", lambda name: flaky)
    try:
        out = dm.label_all("frontier:test", limit=3, workers=2)
        assert out["labelled"] == 3 and len(dm.read_jsonl(dm.DATA / "labels.cache.jsonl")) == 3

        def never(state):
            raise AssertionError("a cached label was paid for twice")

        monkeypatch.setattr(dm, "frontier_labeller", lambda name: never)
        assert dm.label_all("frontier:test", limit=3)["labelled"] == 3
    finally:
        (dm.DATA / "labels.cache.jsonl").unlink(missing_ok=True)
        if saved is not None:
            (dm.DATA / "labels.jsonl").write_text(saved)
