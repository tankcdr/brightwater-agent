// 01. The cross-system path: applicant -> owner -> owner's other business -> late facility.
//
// Each hop comes from a different system of record:
//   APPLIED_FOR  credit         (application)
//   OWNS         registry       (ownership stake, with pct on the edge)
//   OWNS         registry       (the same person's stake elsewhere)
//   HAS_FACILITY credit         (the other business's loan)
//   HAS_PAYMENT  credit         (its payment history)
//
// "Late" is measured on the application date, the same way the policy does:
// days from the oldest payment still unpaid on that date.
//
//   brightwater graph query 01 --param app=APP-H4

MATCH (b:Business)-[:APPLIED_FOR]->(app:Application {record_key: $app})
MATCH (b)<-[stake:OWNS]-(owner:Person)-[other_stake:OWNS]->(other:Business)
WHERE other <> b
MATCH (other)-[:HAS_FACILITY]->(f:Facility)-[:HAS_PAYMENT]->(p:Payment)
WHERE p.due_date <= app.submitted_on
  AND (p.paid_date IS NULL OR p.paid_date > app.submitted_on)
WITH app, b, owner, stake, other, other_stake, f, min(p.due_date) AS oldest_unpaid
RETURN app.record_key                                        AS application,
       b.name                                                AS applicant,
       owner.name                                            AS owner,
       stake.pct                                             AS stake_in_applicant,
       other.name                                            AS other_business,
       other_stake.pct                                       AS stake_in_other,
       f.record_key                                          AS facility,
       duration.inDays(oldest_unpaid, app.submitted_on).days AS days_past_due
ORDER BY days_past_due DESC
