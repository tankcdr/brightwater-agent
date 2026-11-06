"""Brightwater Community Bank small-business lending policy, as code.

This module is the single source of truth for the policy. The CLI tools, the
tests, the rule rows in `compliance.policy_rule` and (later) the compiled
procedure all read it from here. Nothing in this file touches a database: the
engine is a pure function from `Facts` to `Decision`, so it can be tested on
its own and replayed against any recorded set of facts.

Where the facts come from is a separate concern. Chapter 0 computes them with
hand-written SQL (`metrics_sql.py`); Chapter 2 moves them to the semantic layer
(`metrics_cube.py`) without touching this file.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from decimal import Decimal
from enum import Enum
from typing import Any


class Outcome(str, Enum):
    APPROVE = "approve"
    REVIEW = "review"
    DECLINE = "decline"


# Severity order: a decline beats a review beats an approval.
_RANK = {Outcome.APPROVE: 0, Outcome.REVIEW: 1, Outcome.DECLINE: 2}


@dataclass(frozen=True)
class Rule:
    rule_id: str
    version: int
    title: str
    text: str
    params: dict[str, Any]


RULES: tuple[Rule, ...] = (
    Rule(
        "P1", 1, "KYC must be current",
        "The applicant's KYC review must be current on the application date, else decline.",
        {},
    ),
    Rule(
        "P2", 1, "No recent adverse events",
        "No adverse event recorded against the applicant in the 365 days before the application date, else decline.",
        {"lookback_days": 365},
    ),
    Rule(
        "P3", 1, "Debt service coverage",
        "DSCR at or above the approve line stands. Inside the judgment band it goes to review. Below the floor it declines.",
        {"approve_line": "1.25", "floor": "1.10"},
    ),
    Rule(
        "P4", 1, "Group exposure cap",
        "Group exposure (the applicant plus businesses its owners control) must stay under the cap, else review.",
        {"cap": "1500000"},
    ),
    Rule(
        "P5", 1, "Related delinquency blocks auto-approval",
        "Any facility 60 or more days past due at a business where an owner of the applicant holds 25% or more blocks auto-approval.",
        {"days_past_due": 60, "owner_stake_pct": "25"},
    ),
    Rule(
        "P6", 1, "Long-tenured declines need a person",
        "Declining a customer of 10 or more years requires a person to confirm before the decision is filed.",
        {"tenure_years": 10},
    ),
)


@dataclass(frozen=True)
class RelatedFacility:
    """A credit facility at the applicant, or at a business one of its owners holds a stake in.

    `owner_stake_pct` is the largest stake any owner of the applicant holds in
    the business that owns the facility (100 for the applicant's own
    facilities). The semantic layer decides which facilities are related; the
    policy decides which stake and which lateness matter.
    """

    facility_id: str
    business_id: str
    owner_stake_pct: Decimal
    days_past_due: int


@dataclass(frozen=True)
class Facts:
    """Everything the policy needs about one application, and nothing else.

    No names, no dates of birth, no free text. This is deliberate: in Ch 4 the
    judgment step sees exactly this shape, and the audit trail in Ch 9 records
    it as the step's input.
    """

    application_id: str
    kyc_current: bool
    days_since_last_adverse_event: int | None   # None: no adverse event on record
    dscr: Decimal
    group_exposure: Decimal
    related_facilities: tuple[RelatedFacility, ...]
    tenure_years: Decimal

    def to_dict(self) -> dict[str, Any]:
        return _jsonable(asdict(self))


def _jsonable(v: Any) -> Any:
    if isinstance(v, Decimal):
        return str(v)
    if isinstance(v, dict):
        return {k: _jsonable(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_jsonable(x) for x in v]
    return v


@dataclass(frozen=True)
class Fired:
    rule_id: str
    effect: str  # decline | review | block_auto_approve | requires_person
    reason: str


@dataclass(frozen=True)
class Decision:
    application_id: str
    outcome: Outcome
    requires_person: bool
    fired: tuple[Fired, ...] = field(default_factory=tuple)
    policy_version: str = ""

    @property
    def fired_ids(self) -> list[str]:
        return [f.rule_id for f in self.fired]

    def to_dict(self) -> dict[str, Any]:
        return {
            "application_id": self.application_id,
            "outcome": self.outcome.value,
            "requires_person": self.requires_person,
            "fired": [asdict(f) for f in self.fired],
            "policy_version": self.policy_version,
        }


def policy_version(rules: tuple[Rule, ...] = RULES) -> str:
    """A compact version string, e.g. 'P1v1.P2v1.P3v1.P4v1.P5v1.P6v1'."""
    return ".".join(f"{r.rule_id}v{r.version}" for r in rules)


def _rule(rules: tuple[Rule, ...], rule_id: str) -> Rule | None:
    return next((r for r in rules if r.rule_id == rule_id), None)


def evaluate(facts: Facts, rules: tuple[Rule, ...] = RULES) -> Decision:
    """Apply the policy to one application's facts.

    Every rule is checked; every rule whose condition holds is reported in
    `fired`, even when a stronger rule already decides the outcome. That is
    what makes the decision explainable: a decline for P1 that would also have
    tripped P5 says so.
    """
    fired: list[Fired] = []
    outcome = Outcome.APPROVE

    def escalate(to: Outcome) -> None:
        nonlocal outcome
        if _RANK[to] > _RANK[outcome]:
            outcome = to

    if (r := _rule(rules, "P1")) and not facts.kyc_current:
        fired.append(Fired(r.rule_id, "decline", "KYC review is not current on the application date"))
        escalate(Outcome.DECLINE)

    if (r := _rule(rules, "P2")) and facts.days_since_last_adverse_event is not None \
            and facts.days_since_last_adverse_event <= int(r.params["lookback_days"]):
        fired.append(Fired(r.rule_id, "decline",
                           f"adverse event {facts.days_since_last_adverse_event} days before the application "
                           f"(lookback {r.params['lookback_days']} days)"))
        escalate(Outcome.DECLINE)

    if r := _rule(rules, "P3"):
        approve_line = Decimal(r.params["approve_line"])
        floor = Decimal(r.params["floor"])
        if facts.dscr < floor:
            fired.append(Fired(r.rule_id, "decline", f"DSCR {facts.dscr:.2f} is below the {floor} floor"))
            escalate(Outcome.DECLINE)
        elif facts.dscr < approve_line:
            fired.append(Fired(r.rule_id, "review",
                               f"DSCR {facts.dscr:.2f} is inside the judgment band [{floor}, {approve_line})"))
            escalate(Outcome.REVIEW)

    if (r := _rule(rules, "P4")) and facts.group_exposure >= Decimal(r.params["cap"]):
        fired.append(Fired(r.rule_id, "review",
                           f"group exposure {facts.group_exposure:,.0f} is at or over the {Decimal(r.params['cap']):,.0f} cap"))
        escalate(Outcome.REVIEW)

    if r := _rule(rules, "P5"):
        late = [f for f in facts.related_facilities
                if f.days_past_due >= int(r.params["days_past_due"])
                and f.owner_stake_pct >= Decimal(r.params["owner_stake_pct"])]
        if late:
            worst = max(late, key=lambda f: (f.days_past_due, f.facility_id))
            fired.append(Fired(r.rule_id, "block_auto_approve",
                               f"{worst.facility_id} at {worst.business_id} is {worst.days_past_due} days past due; "
                               f"an owner of the applicant holds {worst.owner_stake_pct:.0f}% of that business"))
            escalate(Outcome.REVIEW)

    # P7 is added in Chapter 5.

    requires_person = False
    if (r := _rule(rules, "P6")) and outcome is Outcome.DECLINE \
            and facts.tenure_years >= Decimal(r.params["tenure_years"]):
        fired.append(Fired(r.rule_id, "requires_person",
                           f"customer for {facts.tenure_years:.1f} years; a person must confirm the decline"))
        requires_person = True

    return Decision(
        application_id=facts.application_id,
        outcome=outcome,
        requires_person=requires_person,
        fired=tuple(fired),
        policy_version=policy_version(rules),
    )
