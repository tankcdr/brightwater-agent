"""The assessment rubric (decision_model/rubric.md), as code.

Used three ways: by the scripted driver, as the cross-check on frontier
labels in Ch 6, and to build questions' expected answers in tests. The
frontier labeller never sees this code; it gets the prose.
"""

from __future__ import annotations

import statistics
from typing import Any

RISK_LABELS = ["Low", "Moderate", "Elevated", "High"]


def risk_tier(facts: dict[str, Any]) -> int:
    dscr = float(facts["dscr"])
    exposure = float(facts["group_exposure"])
    days_adv = facts.get("days_since_last_adverse_event")
    related = [r for r in facts.get("related_facilities", []) if float(r["owner_stake_pct"]) >= 25]
    worst = max((int(r["days_past_due"]) for r in related), default=0)
    if dscr < 1.10 or not facts["kyc_current"] or (days_adv is not None and int(days_adv) <= 365) or worst >= 60:
        return 3
    if dscr < 1.25 or worst >= 30 or exposure >= 1_500_000:
        return 2
    if dscr < 1.50 or exposure >= 750_000:
        return 1
    return 0


def cash_flow_stable(monthly_net: list[float], monthly_debt_service: float) -> bool:
    if not monthly_net:
        return False
    if min(monthly_net) < monthly_debt_service:
        return False
    mean = statistics.fmean(monthly_net)
    if mean <= 0:
        return False
    return statistics.pstdev(monthly_net) / mean <= 0.25


def recommendation(tier: int, stable: bool) -> str:
    if tier == 3:
        return "decline"
    if tier == 2 or not stable:
        return "approve_with_conditions"
    return "approve"


def label(state: dict[str, Any]) -> dict[str, Any]:
    """The rubric's answers for an `assess` step state."""
    tier = risk_tier(state["facts"])
    stable = cash_flow_stable(state["monthly_net_cash_flow"], float(state["monthly_debt_service"]))
    return {"risk_tier": tier, "cash_flow_stable": stable, "recommendation": recommendation(tier, stable)}
