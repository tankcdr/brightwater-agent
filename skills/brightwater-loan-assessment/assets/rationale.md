# Write the decision rationale for {application_id}

The lending policy has decided this application: **{rule_outcome}**. That outcome is final. Do not
argue with it or change it; explain it.

Rules that fired: {fired}
A person must confirm before filing: {requires_person}

Your assessment for the reviewer: risk tier {risk_tier} (0 low, 1 moderate, 2 elevated, 3 high);
cash flow stable: {cash_flow_stable}; recommendation: {recommendation}.

The governed figures for this application: {facts}

The records you may cite, and no others: {records} {policy_records}

{assets[style]}

Return JSON with `rationale`: the rationale text.
