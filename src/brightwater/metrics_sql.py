"""Chapter 0: the policy facts, computed with hand-written SQL.

This is the version most teams start with and many never leave: each fact is
a query someone wrote once, in application code, against the systems of
record. It works. It is also the reason two dashboards disagree about DSCR.

Ch 2 replaces this module with `metrics_cube.py`, which asks the semantic
layer instead. This file stays in the repo as the reference implementation:
the Ch 2 migration test asserts that both return identical facts for every
application before the semantic layer is allowed to take over.

Definitions used here (and carried into the semantic layer unchanged):

* DSCR window: the 12 full calendar months before the month of application.
* Cash available for debt service: operating inflow + non-operating inflow
  - outflow, summed across all of the business's deposit accounts.
* Debt service: monthly payments on facilities open before the application
  date, plus the level payment on the requested loan, times 12.
* Group: the applicant plus every business in which any owner of the
  applicant holds more than 50%.
* Group exposure: requested amount + outstanding balances across the group.
* Related facilities: the applicant's own facilities, plus facilities at any
  business in which an owner of the applicant holds a stake, tagged with the
  largest such stake.
* Days since last adverse event: from the most recent event on or before the
  application date; None when there is none. The lookback is policy (P2).
* Days past due: days from the oldest scheduled payment still unpaid on the
  application date to the application date; 0 if nothing is overdue.
"""

from __future__ import annotations

from decimal import Decimal

import pg8000.native

from .db import rows
from .policy import Facts, RelatedFacility

PROFILE_SQL = """
SELECT a.application_id,
       (k.status = 'current' AND k.next_review_due >= a.submitted_on)         AS kyc_current,
       a.submitted_on - (SELECT max(e.occurred_on) FROM compliance.adverse_event e
                          WHERE e.business_id = a.business_id
                            AND e.occurred_on <= a.submitted_on)              AS days_since_last_adverse_event,
       round(((a.submitted_on - b.customer_since) / 365.25)::numeric, 2)        AS tenure_years
FROM credit.application a
JOIN ledger.business b USING (business_id)
LEFT JOIN compliance.kyc k USING (business_id)
WHERE a.application_id = :app
"""

DSCR_SQL = """
WITH app AS (
    SELECT a.*, a.annual_rate / 12 AS r,
           date_trunc('month', a.submitted_on)::date AS app_month
    FROM credit.application a WHERE a.application_id = :app
), cash AS (
    SELECT sum(cf.operating_inflow + cf.nonoperating_inflow - cf.outflow) AS available
    FROM app
    JOIN ledger.deposit_account d ON d.business_id = app.business_id
    JOIN ledger.monthly_cash_flow cf ON cf.account_id = d.account_id
    WHERE cf.month >= (app.app_month - interval '12 months') AND cf.month < app.app_month
), debt AS (
    SELECT 12 * (
        coalesce((SELECT sum(f.monthly_payment) FROM credit.facility f
                   WHERE f.business_id = app.business_id AND f.opened < app.submitted_on), 0)
        + app.requested_amount * app.r / (1 - power(1 + app.r, -app.term_months))
    ) AS service
    FROM app
)
SELECT round(cash.available / debt.service, 4) AS dscr FROM cash, debt
"""

GROUP_SQL = """
WITH app AS (SELECT * FROM credit.application WHERE application_id = :app),
members AS (
    SELECT app.business_id FROM app
    UNION
    SELECT o2.business_id
    FROM app
    JOIN registry.ownership o1 ON o1.business_id = app.business_id
    JOIN registry.ownership o2 ON o2.person_id = o1.person_id AND o2.pct > 50
)
SELECT (SELECT requested_amount FROM app)
     + coalesce((SELECT sum(f.outstanding_balance) FROM credit.facility f
                  WHERE f.business_id IN (SELECT business_id FROM members)), 0) AS group_exposure
"""

RELATED_SQL = """
WITH app AS (SELECT * FROM credit.application WHERE application_id = :app),
related AS (
    SELECT app.business_id, 100::numeric AS owner_stake_pct FROM app
    UNION ALL
    SELECT o2.business_id, max(o2.pct)
    FROM app
    JOIN registry.ownership o1 ON o1.business_id = app.business_id
    JOIN registry.ownership o2 ON o2.person_id = o1.person_id AND o2.business_id <> app.business_id
    GROUP BY o2.business_id
)
SELECT f.facility_id, f.business_id, r.owner_stake_pct,
       coalesce((SELECT (SELECT submitted_on FROM app) - min(p.due_date)
                   FROM credit.payment p
                  WHERE p.facility_id = f.facility_id
                    AND p.due_date <= (SELECT submitted_on FROM app)
                    AND (p.paid_date IS NULL OR p.paid_date > (SELECT submitted_on FROM app))), 0) AS days_past_due
FROM related r
JOIN credit.facility f ON f.business_id = r.business_id
ORDER BY f.facility_id
"""


def facts_for(conn: pg8000.native.Connection, application_id: str) -> Facts:
    profile = rows(conn, PROFILE_SQL, app=application_id)
    if not profile:
        raise KeyError(f"no application {application_id}")
    p = profile[0]
    dscr = rows(conn, DSCR_SQL, app=application_id)[0]["dscr"]
    exposure = rows(conn, GROUP_SQL, app=application_id)[0]["group_exposure"]
    related = tuple(
        RelatedFacility(r["facility_id"], r["business_id"], Decimal(r["owner_stake_pct"]).quantize(Decimal("0.01")),
                        int(r["days_past_due"]))
        for r in rows(conn, RELATED_SQL, app=application_id)
    )
    return Facts(
        application_id=application_id,
        kyc_current=bool(p["kyc_current"]),
        days_since_last_adverse_event=(None if p["days_since_last_adverse_event"] is None
                                       else int(p["days_since_last_adverse_event"])),
        dscr=Decimal(dscr).quantize(Decimal("0.0001")),
        group_exposure=Decimal(exposure).quantize(Decimal("0.01")),
        related_facilities=related,
        tenure_years=Decimal(p["tenure_years"]).quantize(Decimal("0.01")),
    )
