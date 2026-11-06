"""Chapter 2 proofs: the semantic layer.

1. Moving the facts from hand-written SQL to Cube changes nothing.
2. Cube's answers reconcile to raw Postgres.
3. Changing the DSCR definition in one file changes the policy result and the
   tool output, and both still reconcile to raw Postgres. The hand-written SQL
   from Ch 0 does not change, which is the drift the semantic layer exists to
   prevent.
"""

from __future__ import annotations

import json
import subprocess
import sys
import time
from pathlib import Path

import pytest

from brightwater import metrics_cube, metrics_sql, policy, semantic

pytestmark = pytest.mark.cube

ROOT = Path(__file__).resolve().parents[1]
MODEL = ROOT / "semantic" / "brightwater.ossie.yaml"
CUBE_MODEL = ROOT / "semantic" / "cube" / "model"
APPS = [f"APP-H{i}" for i in range(1, 7)]
ORIGINAL = "sql: operating_inflow + nonoperating_inflow - outflow"
STRICTER = "sql: operating_inflow - outflow"


def test_model_is_valid_ossie():
    semantic.load(MODEL)  # raises on any JSON Schema violation


def test_generated_cube_files_are_up_to_date(tmp_path):
    semantic.build_cube_model(MODEL, tmp_path)
    for f in (tmp_path / "cubes").glob("*.yml"):
        assert f.read_text() == (CUBE_MODEL / "cubes" / f.name).read_text(), f"rerun `brightwater semantic build` ({f.name})"


@pytest.fixture(scope="module")
def cube(loaded_db):
    c = metrics_cube.Cube()
    try:
        c.load({"measures": ["application_month.dscr"], "limit": 1})
    except Exception as e:  # noqa: BLE001
        pytest.skip(f"Cube not reachable: {e}")
    return c


def test_migration_changes_nothing(loaded_db, cube):
    ids = APPS + [f"APP-{i:05d}" for i in range(1, 1201, 7)]
    for app_id in ids:
        assert metrics_cube.facts_for(app_id, cube) == metrics_sql.facts_for(loaded_db, app_id), app_id


def test_cube_reconciles_to_postgres(cube):
    assert semantic.reconcile(MODEL, APPS + [f"APP-{i:05d}" for i in range(1, 201)], verbose=False)


def _deploy(model_path: Path) -> None:
    semantic.build_cube_model(model_path, CUBE_MODEL)


def _wait_for_dscr(cube, app_id: str, predicate, timeout: float = 30) -> str:
    deadline = time.monotonic() + timeout
    while True:
        v = metrics_cube.facts_for(app_id, cube).dscr
        if predicate(v) or time.monotonic() > deadline:
            return v
        time.sleep(0.5)


def _tool_output(app_id: str) -> dict:
    out = subprocess.run([str(Path(sys.executable).with_name("brightwater")), "assess", app_id, "--json"], capture_output=True, text=True, check=True)
    return json.loads(out.stdout)


def test_one_line_definition_change(tmp_path, cube, loaded_db):
    text = MODEL.read_text()
    assert text.count(ORIGINAL) == 1
    candidate = tmp_path / "brightwater.ossie.yaml"
    candidate.write_text(text.replace(ORIGINAL, STRICTER))

    before = _tool_output("APP-H1")
    assert before["decision"]["outcome"] == "approve"

    try:
        _deploy(candidate)
        dscr = _wait_for_dscr(cube, "APP-H1", lambda v: v < 1.25)
        assert str(dscr) == "1.1800"

        # The policy result changes...
        after = _tool_output("APP-H1")
        assert after["decision"]["outcome"] == "review"
        assert [f["rule_id"] for f in after["decision"]["fired"]] == ["P3"]
        # ...and Cube still reconciles to raw Postgres under the new definition.
        assert semantic.reconcile(candidate, APPS, verbose=False)
        # The Ch 0 hand-written SQL did not hear about the change.
        assert str(metrics_sql.facts_for(loaded_db, "APP-H1").dscr) == "1.3297"
    finally:
        _deploy(MODEL)
        _wait_for_dscr(cube, "APP-H1", lambda v: v >= 1.25)

    assert _tool_output("APP-H1")["decision"]["outcome"] == "approve"


def test_impact_lists_every_changed_application(tmp_path, loaded_db):
    candidate = tmp_path / "brightwater.ossie.yaml"
    candidate.write_text(MODEL.read_text().replace(ORIGINAL, STRICTER))
    changes = semantic.impact(MODEL, candidate)
    ids = [c["application_id"] for c in changes]
    assert "APP-H1" in ids
    assert all(c["application_id"] not in ("APP-H2", "APP-H3", "APP-H4", "APP-H5", "APP-H6") for c in changes)
    assert len(changes) == 20
