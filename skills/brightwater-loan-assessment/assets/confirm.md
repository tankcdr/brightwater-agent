# A person must confirm this decision

Application {application_id} is a **{rule_outcome}** that the policy will not let an agent file on
its own. Reasons: {person_reasons}.

Decision record: {decision_id}

Rationale as drafted:

{rationale}

This task must be answered by a credit officer, not by an agent. Return JSON with `confirmed`
(true to file the decision as drafted, false to send it back), `confirmed_by` (your user id) and
`confirmation_note`.
