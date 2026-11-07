---
name: brightwater-loan-assessment
description: Assess a Brightwater Community Bank small-business loan application end to end. Gathers the governed figures from the semantic layer, applies lending policy rules P1 to P6 exactly, judges risk tier, cash flow stability and a recommendation, writes a cited rationale, files the decision, and hands any decline of a long-tenured customer to a person. Use when an application id (APP-...) needs a credit decision, a rationale or a risk view.
license: Apache-2.0
metadata:
  aip-version: "0.6a1"
  author: long-run-advisory
  version: "1"
---

# AIP runtime — format 0.6a1

You are executing an Agent Instruction Protocol (AIP) procedure: the fenced YAML block in this skill's `SKILL.md`. AIP is a protocol for cheaply, quickly, and accurately executing multi-step tasks as a graph of typed steps. You drive the run and execute every step yourself, following the semantics below.

Critical terminology:

- **Client**: you, the agent running this procedure; the `client_task` step kind is named for it. You supply each step's input, run its script, answer its questions by your own judgment, perform its task, follow its router, and make the final call at every step.
- **State**: the one JSON object a run carries. Each step declares the keys it reads as `inputs`; `execution` and `client_task` steps declare the keys they add as `outputs`, and a `decision` adds one key per question.
- **Step kinds**: `execution` runs a script, `decision` asks typed questions about the state, `client_task` hands work to you, `router` branches on a value in the state, `end` declares the procedure's result.

## Execution

The state starts as the start step's `inputs` and flows along `inputs_to`; each step adds its outputs to it. Every key is produced by exactly one step, so keys accumulate and nothing is overwritten. A step runs only if the state holds every key it declares in `inputs`, with the declared types; check that before each step. A step sees only those keys: give it its declared `inputs` and nothing else from the state. You have the final say at every step.

- **`execution`**: before the first one, check that this machine has everything `requires` names (the interpreter at its minimum version, the packages, the tools); if anything is missing, stop and name all of it. Run `script` from its folder, launched with the step's `command` when it has one and `python <script>` otherwise, with one JSON object on stdin, `{"state": <the step's inputs>, "assets": {<file stem>: <content>}, "expects": <the next step's inputs>}`. The script writes one JSON object to stdout holding exactly the step's `outputs`; add them to the state. A non-zero exit, or output missing a declared key, fails the step: stop there and report it rather than doing the step by hand.
- **`decision`**: answer each question from the step's inputs, and from the run so far only when the step sets `history: true`. Each answer collapses to one value under its question name and is added to the state: a noul to `true`/`false`, a choice to its label, a score to its level number. `thresholds` name the questions where an uncertain answer matters most; when your answer to one is a close call, reconsider it before continuing.
- **`client_task`**: render `template` with `{key}` from the step's inputs, `{assets[stem]}` for its assets, and `{meta.name}` for the skill name. Perform the task, loading `references` if their descriptions apply, and produce the step's `outputs`; add them to the state.
- **`router`**: read the state's `branch_on` key and continue at `branches[value]`. A value with no branch is an error.
- **`end`**: the state must hold `end`'s `inputs`. Those keys are the procedure's result.

```yaml
purpose: >
  Turn a small-business loan application id into a filed, explainable credit decision. The lending
  policy decides the outcome deterministically from governed figures; a decision step adds the
  judgment a reviewer needs (risk tier, cash flow stability, recommendation); the agent writes the
  rationale; a person must confirm any decline of a customer of ten years or more.

trigger_when:
  - A Brightwater small-business loan application (APP-...) needs a credit decision filed.
  - A banker asks for the outcome, rationale or risk view on a loan application.

do_not_use_when:
  - The request is a consumer loan, a renewal, or an application over $2,000,000.
  - The request is to change the lending policy or a metric definition.

requires:
  interpreter:
    name: python
    version: "3.11"
  packages:
    - brightwater

steps:
  - name: gather
    kind: execution
    description: Read every governed figure for the application from the semantic layer, with the record behind each.
    inputs:
      - name: application_id
        type: string
        description: The application to assess, e.g. APP-H4.
    script: scripts/steps.py
    command: ["python", "steps.py", "gather"]
    outputs:
      - name: facts
        type: object
        description: Policy facts (KYC, adverse events, DSCR, group exposure, related facilities, tenure); figures and ids only.
      - name: monthly_net_cash_flow
        type: list[*]
        description: Net cash flow for each of the 12 months in the DSCR window, oldest first.
      - name: monthly_debt_service
        type: float
        description: Debt service due each month, existing facilities plus the requested loan.
      - name: records
        type: list[*]
        description: The record key of every figure read; the only keys a rationale may cite besides policy rules.
      - name: semantic_version
        type: string
        description: Hash of the metric definitions the figures were computed under.
    timeout: 120
    inputs_to: apply-policy

  - name: apply-policy
    kind: execution
    description: Apply lending rules P1 to P6 exactly, reporting every rule that holds; a decline outranks a review, which outranks an approval.
    inputs:
      - name: facts
        type: object
    script: scripts/steps.py
    command: ["python", "steps.py", "apply-policy"]
    assets:
      - assets/policy.json
    outputs:
      - name: rule_outcome
        type: string
        description: approve, review or decline. Final; nothing downstream changes it.
      - name: fired
        type: list[*]
        description: Each rule that held, with its effect and reason.
      - name: requires_person
        type: boolean
        description: True when a person must confirm before the decision is final (P6).
      - name: person_reasons
        type: list[*]
        description: The rules that require a person.
      - name: policy_version
        type: string
      - name: policy_records
        type: list[*]
        description: Citable keys for the rules in this revision, e.g. policy:P3v1.
    timeout: 60
    inputs_to: assess

  - name: assess
    kind: decision
    description: Judge the case for the reviewer, from figures only, using the assessment rubric.
    inputs:
      - name: facts
        type: object
      - name: monthly_net_cash_flow
        type: list[*]
      - name: monthly_debt_service
        type: float
      - name: rule_outcome
        type: string
      - name: fired
        type: list[*]
    questions:
      risk_tier:
        type: score
        instructions: >
          Rate the credit risk; take the highest level any condition reaches. DSCR is facts.dscr, group
          exposure is facts.group_exposure, and a related facility counts only where owner_stake_pct is
          25 or more.
        criteria:
          - "Low: none of the conditions below."
          - "Moderate: DSCR from 1.25 to below 1.50, or group exposure from $750,000 to below $1,500,000."
          - "Elevated: DSCR from 1.10 to below 1.25, a related facility 30 to 59 days past due, or group exposure at or above $1,500,000."
          - "High: DSCR below 1.10, KYC not current, an adverse event within 365 days, or a related facility 60 or more days past due."
      cash_flow_stable:
        type: noul
        instructions: Is monthly cash flow stable enough to rely on?
        criteria:
          true: Every month's net cash flow covers monthly_debt_service, and the standard deviation of the 12 months is at most 25% of their mean.
          false: At least one month falls below monthly_debt_service, or the months vary by more than 25% of their mean.
      recommendation:
        type: choice
        instructions: What should the reviewer do? This is advice; it never changes rule_outcome.
        criteria:
          approve: Risk tier Low or Moderate and cash flow stable.
          approve_with_conditions: Risk tier Elevated, or Low or Moderate with unstable cash flow.
          decline: Risk tier High.
    thresholds:
      risk_tier: 0.6
      cash_flow_stable: 0.2
      recommendation: 0.6
    inputs_to: draft-rationale

  - name: draft-rationale
    kind: client_task
    description: Write the rationale a banker and an examiner will read, citing a record for every figure.
    inputs:
      - name: application_id
        type: string
      - name: rule_outcome
        type: string
      - name: fired
        type: list[*]
      - name: requires_person
        type: boolean
      - name: facts
        type: object
      - name: records
        type: list[*]
      - name: policy_records
        type: list[*]
      - name: risk_tier
        type: integer
      - name: cash_flow_stable
        type: boolean
      - name: recommendation
        type: string
    template: assets/rationale.md
    assets:
      - assets/style.md
    outputs:
      - name: rationale
        type: string
        description: "Starts with Outcome: <rule_outcome>; every figure cited to a listed record; no protected characteristics."
    inputs_to: file

  - name: file
    kind: execution
    description: Lint the rationale against the records this run read, then file the decision (pending a person if required).
    inputs:
      - name: application_id
        type: string
      - name: rule_outcome
        type: string
      - name: requires_person
        type: boolean
      - name: rationale
        type: string
      - name: records
        type: list[*]
      - name: policy_records
        type: list[*]
      - name: risk_tier
        type: integer
      - name: cash_flow_stable
        type: boolean
      - name: recommendation
        type: string
    script: scripts/steps.py
    command: ["python", "steps.py", "file"]
    outputs:
      - name: decision_id
        type: string
      - name: status
        type: string
        description: filed, or pending_person when a person must confirm.
    timeout: 60
    inputs_to: needs-person

  - name: needs-person
    kind: router
    description: Declines of long-tenured customers wait for a person; everything else is done.
    branch_on: requires_person
    branches:
      "true": confirm-with-person
      "false": end

  - name: confirm-with-person
    kind: client_task
    description: A credit officer confirms or returns the decline. Never answered by an agent.
    inputs:
      - name: application_id
        type: string
      - name: rule_outcome
        type: string
      - name: person_reasons
        type: list[*]
      - name: rationale
        type: string
      - name: decision_id
        type: string
    template: assets/confirm.md
    outputs:
      - name: confirmed
        type: boolean
      - name: confirmed_by
        type: string
        description: The credit officer's user id.
      - name: confirmation_note
        type: string
    inputs_to: record-confirmation

  - name: record-confirmation
    kind: execution
    description: Record the officer's answer on the filed decision.
    inputs:
      - name: decision_id
        type: string
      - name: confirmed
        type: boolean
      - name: confirmed_by
        type: string
      - name: confirmation_note
        type: string
    script: scripts/steps.py
    command: ["python", "steps.py", "record-confirmation"]
    outputs:
      - name: final_status
        type: string
    timeout: 60
    inputs_to: end

  - name: end
    kind: end
    description: The filed decision.
    inputs:
      - name: decision_id
        type: string
      - name: status
        type: string
      - name: rule_outcome
        type: string

anti_patterns:
  - Computing a figure instead of reading it from the semantic layer, or taking one from a banker's note.
  - Changing the policy's outcome in the rationale because the assessment disagrees with it.
  - An agent answering the confirm-with-person task.
  - Citing a record the run did not read.
```
