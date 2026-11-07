# A person must confirm this decision

Application {application_id} is a **{rule_outcome}** that the lending policy reserves for a person.
Rules that reserve it: {person_reasons} (P6: declining a customer of ten years or more; P7: group
exposure above $2,000,000).

Decision record: {decision_id}

Rationale as drafted:

{rationale}

This task must be answered by a credit officer, not by an agent. Return JSON with `confirmed`
(true to file the decision as drafted, false to send it back), `confirmed_by` (your user id) and
`confirmation_note`.
