"""Chapter 5: a decision records the rules its revision applied, not the ones installed now.

The package ships P1 to P7 after Chapter 5, but a run of revision 1 applied P1 to P6 from that
revision's own policy asset. The filed decision, and so the audit packet, must say P1 to P6.
"""

from __future__ import annotations

import pytest

from brightwater import procedure

pytestmark = pytest.mark.cube

R1 = procedure.ROOT / "skills" / "revisions" / "r1" / "brightwater-loan-assessment"


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


@pytest.mark.parametrize("skill, expected", [
    (R1, "P1v1.P2v1.P3v1.P4v1.P5v1.P6v1"),
    (procedure.SKILL_DIR, "P1v1.P2v1.P3v1.P4v1.P5v1.P6v1.P7v1"),
])
def test_a_decision_records_the_rules_its_revision_applied(ready, local_runs, skill, expected):
    r = procedure.run("APP-H1", procedure.ScriptedAnswerer(), procedure.auto_person(), configuration="test",
                      skill=str(skill))
    assert r.ctx.decision["policy_version"] == expected
