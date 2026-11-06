"""The fixture is deterministic: same seed, same bytes."""

from __future__ import annotations

import json

from brightwater import fixture


def test_same_seed_same_bytes(tmp_path):
    a = fixture.write_csv(fixture.build(seed=7, n=200), tmp_path / "a")
    b = fixture.write_csv(fixture.build(seed=7, n=200), tmp_path / "b")
    assert a == b


def test_different_seed_different_bytes(tmp_path):
    a = fixture.write_csv(fixture.build(seed=7, n=200), tmp_path / "a")
    b = fixture.write_csv(fixture.build(seed=8, n=200), tmp_path / "b")
    assert a["credit.application.csv"] != b["credit.application.csv"]


def test_handbuilt_always_present(fixture_dir):
    meta = json.loads((fixture_dir / "manifest.json").read_text())
    assert meta["handbuilt"] == [f"APP-H{i}" for i in range(1, 7)]
    assert meta["held_out"] == ["APP-H6"]
    assert meta["rows"]["credit.application"] == 1206
