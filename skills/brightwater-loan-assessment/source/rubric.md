# Assessment rubric

The three questions the `assess` step asks about every application, and how to answer them. The
frontier labeller in Chapter 6 is given this text verbatim; `src/brightwater/rubric.py` implements
the same rules deterministically, as a cross-check on the labels and as the answers the scripted
driver gives in tests.

The inputs are figures only: the policy facts, the 12 monthly net cash flows in the DSCR window,
the monthly debt service, the rule outcome and the rules that fired. No names, no notes.

## risk_tier (score, 0 to 3)

Take the highest tier any condition reaches.

| Tier | Label | Any of |
|---|---|---|
| 3 | High | DSCR below 1.10; KYC not current; an adverse event within 365 days; a related facility 60 or more days past due where an owner holds 25% or more |
| 2 | Elevated | DSCR from 1.10 to below 1.25; a related facility 30 to 59 days past due where an owner holds 25% or more; group exposure at or above $1,500,000 |
| 1 | Moderate | DSCR from 1.25 to below 1.50; group exposure from $750,000 to below $1,500,000 |
| 0 | Low | none of the above |

## cash_flow_stable (yes or no)

Yes when both hold: no month in the window has net cash flow below that month's debt service, and
the coefficient of variation of the 12 monthly net cash flows (standard deviation over mean) is
0.25 or less. Otherwise no.

## recommendation (approve, approve_with_conditions, decline)

- **decline** when the risk tier is 3.
- **approve_with_conditions** when the risk tier is 2, or when the tier is 0 or 1 and cash flow is
  not stable.
- **approve** otherwise.

The recommendation is advice to the person reviewing the file. It never overrides the lending
policy: an application the policy sends to review stays in review whatever is recommended.
