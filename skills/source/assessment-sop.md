# Brightwater Community Bank: small-business loan assessment SOP

Version 3, owner: Credit Risk. Applies to new small-business loan applications up to $2,000,000.

This is the procedure a credit analyst follows today. It is written for people. Chapter 3 gives it,
unchanged, to a free-form agent. Chapter 4 compiles it, with the recorded agent runs and the policy,
into an AIP procedure.

## 1. Pull the application

Read the application: the business, the amount requested, the term, the rate, the purpose and the
date it was submitted. Every measurement below is taken as of the submitted date, not today.

## 2. Gather the governed figures

Never compute a figure yourself and never take one from a note. Ask the semantic layer for:

- KYC standing (current or not) and days since the last adverse event, if any.
- Debt service coverage ratio (DSCR) over the 12 full months before the month of application, with
  the cash available and the debt service behind it.
- Group exposure: the requested amount plus balances across the applicant and every business its
  owners control.
- Related facilities: the applicant's own facilities and those at businesses its owners hold stakes
  in, with each one's days past due and the owner's stake.
- Tenure: years the business has banked with us.

Write down the record key of every figure you will rely on. A figure without a record key does not
go in the rationale.

## 3. Look for connections

Check who owns the applicant and what else they own. The facts that change a decision are often one
hop away: a co-owner's other business that is behind on its loan, or a set of businesses under one
owner that together push us past our exposure limit. Use the memory graph to find the path and name
the people and businesses on it.

## 4. Apply the lending policy

Apply rules P1 to P6 exactly as written in the policy. Every rule that holds is reported, not only
the one that decides. Severity: a decline outranks a review, which outranks an approval.

When DSCR is inside the judgment band, or a related facility or group exposure sends the
application to review, give your assessment of the case for the reviewer:

- a risk tier: low, moderate, elevated or high;
- whether monthly cash flow is stable enough to rely on;
- a recommendation: approve, approve with conditions, or decline.

## 5. Decide who must sign

If the outcome is a decline and the customer has banked with us for 10 years or more (P6), a person
must confirm before the decision is filed. Do not file it as final yourself.

## 6. Write the rationale

The rationale is read by the borrower's banker and, if challenged, by an examiner.

- Start with the outcome: `Outcome: approve`, `Outcome: review` or `Outcome: decline`.
- Every sentence that states a figure cites the record it came from, in square brackets, for example
  `DSCR is 1.17 [cube:application_month.dscr/APP-H3]`.
- Name the rules that fired.
- Never mention, or reason from, race, colour, religion, national origin, sex, marital status, age,
  disability, receipt of public assistance, or any other protected characteristic. Do not mention
  dates of birth.

## 7. File the decision

File the outcome, the rationale and the list of records cited. If the filing is rejected for the
rationale, fix what it names and file again.
