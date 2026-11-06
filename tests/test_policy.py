"""The policy engine on its own: no database, just facts in and a decision out."""

from __future__ import annotations

from dataclasses import replace
from decimal import Decimal as D

import pytest

from brightwater.policy import Facts, Outcome, RelatedFacility, evaluate

CLEAN = Facts(
    application_id="T-1",
    kyc_current=True,
    days_since_last_adverse_event=None,
    dscr=D("1.60"),
    group_exposure=D("400000"),
    related_facilities=(RelatedFacility("FAC-T-1", "BUS-T", D("100"), 0),),
    tenure_years=D("3.0"),
)


def late(days: int, stake: str) -> tuple[RelatedFacility, ...]:
    return (RelatedFacility("FAC-X-1", "BUS-X", D(stake), days),)


@pytest.mark.parametrize(
    "change, outcome, fired, person",
    [
        ({}, Outcome.APPROVE, [], False),
        ({"kyc_current": False}, Outcome.DECLINE, ["P1"], False),
        ({"days_since_last_adverse_event": 365}, Outcome.DECLINE, ["P2"], False),
        ({"days_since_last_adverse_event": 366}, Outcome.APPROVE, [], False),
        ({"dscr": D("1.25")}, Outcome.APPROVE, [], False),            # the approve line is inclusive
        ({"dscr": D("1.2499")}, Outcome.REVIEW, ["P3"], False),
        ({"dscr": D("1.10")}, Outcome.REVIEW, ["P3"], False),         # the floor is inside the band
        ({"dscr": D("1.0999")}, Outcome.DECLINE, ["P3"], False),
        ({"group_exposure": D("1499999.99")}, Outcome.APPROVE, [], False),
        ({"group_exposure": D("1500000")}, Outcome.REVIEW, ["P4"], False),
        ({"related_facilities": late(60, "25")}, Outcome.REVIEW, ["P5"], False),
        ({"related_facilities": late(59, "100")}, Outcome.APPROVE, [], False),
        ({"related_facilities": late(90, "24.99")}, Outcome.APPROVE, [], False),
        ({"dscr": D("0.9"), "tenure_years": D("10")}, Outcome.DECLINE, ["P3", "P6"], True),
        ({"dscr": D("0.9"), "tenure_years": D("9.99")}, Outcome.DECLINE, ["P3"], False),
        ({"dscr": D("1.2"), "tenure_years": D("25")}, Outcome.REVIEW, ["P3"], False),  # P6 only guards declines
        # Every rule that holds is reported, not just the one that decided.
        ({"kyc_current": False, "group_exposure": D("2000000"), "related_facilities": late(75, "40")},
         Outcome.DECLINE, ["P1", "P4", "P5"], False),
    ],
)
def test_rules(change, outcome, fired, person):
    d = evaluate(replace(CLEAN, **change))
    assert d.outcome is outcome
    assert d.fired_ids == fired
    assert d.requires_person is person


def test_decision_is_serializable():
    d = evaluate(replace(CLEAN, dscr=D("1.2")))
    assert d.to_dict()["fired"][0]["rule_id"] == "P3"
    assert d.policy_version == "P1v1.P2v1.P3v1.P4v1.P5v1.P6v1"
