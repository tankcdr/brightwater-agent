// 02. Who owns the applicant, and what else do they hold?
//
// One row per (owner, other business). The agent uses this to find the
// connections the SOP asks about in step 3; the governed numbers for those
// businesses still come from the semantic layer.
//
//   brightwater graph query 02 --param app=APP-H5

MATCH (b:Business)-[:APPLIED_FOR]->(:Application {record_key: $app})
MATCH (owner:Person)-[stake:OWNS]->(b)
OPTIONAL MATCH (owner)-[other_stake:OWNS]->(other:Business)
WHERE other <> b
RETURN owner.record_key         AS person_id,
       owner.name               AS owner,
       stake.pct                AS stake_in_applicant,
       other.record_key         AS other_business_id,
       other.name               AS other_business,
       other_stake.pct          AS stake_in_other,
       other_stake.pct > 50     AS controls_other
ORDER BY owner, other_business
