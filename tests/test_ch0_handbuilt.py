"""Chapter 0 proof: the policy engine returns the expected outcome and fired
rules for all six hand-built applicants, computed from the systems of record."""

from __future__ import annotations

import pytest

from brightwater import metrics_sql, policy

pytestmark = pytest.mark.postgres

APPS = [f"APP-H{i}" for i in range(1, 7)]


@pytest.mark.parametrize("app_id", APPS)
def test_handbuilt_outcome(loaded_db, expectations, app_id):
    decision = policy.evaluate(metrics_sql.facts_for(loaded_db, app_id))
    expect = expectations[app_id]
    assert decision.outcome.value == expect["outcome"]
    assert decision.fired_ids == expect["fired"]
    assert decision.requires_person == expect["requires_person"]


def test_every_rule_fires_somewhere_in_the_generated_set(loaded_db):
    """The generated applicants exercise the whole policy, not just the happy path."""
    ids = [r[0] for r in loaded_db.run(
        "SELECT application_id FROM credit.application WHERE application_id NOT LIKE 'APP-H%' "
        "ORDER BY application_id LIMIT 400")]
    fired = set()
    for app_id in ids:
        fired.update(policy.evaluate(metrics_sql.facts_for(loaded_db, app_id)).fired_ids)
    assert fired == {"P1", "P2", "P3", "P4", "P5", "P6"}
