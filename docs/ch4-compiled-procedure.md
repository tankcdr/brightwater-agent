# Chapter 4. Compile the runs into an AIP procedure

**Tag:** `ch4` · **Services:** Postgres, Neo4j (memory and AIP), Cube, AIP server · **Proof:** repeated runs on one applicant take identical steps and read identical records; the same table as Chapter 3 shows the difference.

Chapter 3's agent rediscovers the procedure on every run. This chapter writes it down once, in a form a runtime can enforce, and lets the driving model act only where judgment or writing is actually needed.

## AIP in one paragraph

The [Agent Instruction Protocol](https://github.com/zach-blumenfeld/aip) (Apache-2.0, version 0.7.0, format 0.6a1) extends Agent Skills: a skill folder whose `SKILL.md` body is a fixed runtime block plus one YAML graph of typed steps. Five step kinds: `execution` runs a script, `decision` asks typed questions (yes/no, a choice, a score) with confidence thresholds, `client_task` hands work to the agent with a template and required outputs, `router` branches on a value, `end` declares the result. A validator enforces the graph and the data flow (every key read is produced on every path to it, one producer per key, one type per key). An AIP server holds a catalog of published revisions, drives each run with the same engine, and records every step; scripts always run on the client, next to the data.

## Compiling

AIP's `aip-compile` skill turns source material into a procedure, and its authors recommend the largest model available for this one-time step. The procedure in `skills/brightwater-loan-assessment/` was compiled by following that skill, from four sources: the SOP and the assessment rubric, kept in the skill's `source/` folder, the policy (as `assets/policy.json`), and the recorded Ch 3 runs (`brightwater procedure export-traces` writes `results/traces.md`, outside the skill, so a revision's id never depends on your own runs). `source/README.md` records the step-kind choices and the completeness check: every SOP section, and where it landed in the procedure.

The traces decide nothing in the procedure; the SOP and the policy do. What the traces add is evidence: which steps every competent run took (they became the fixed order) and which mistakes recurred (they became linter rules and anti-patterns in the templates). The first build of this procedure used the SOP, the policy, the rubric and scripted runs; `traces.md` is written by the live Ch 3 run in RUNBOOK.md, and the procedure is checked against it there.

The graph:

```text
gather ─▶ apply-policy ─▶ assess ─▶ draft-rationale ─▶ file ─▶ needs-person ─┬─▶ end
(script)   (script)      (decision)  (client task)    (script)  (router)     └─▶ confirm-with-person ─▶ record-confirmation ─▶ end
                                                                                   (client task, person only)  (script)
```

The choices that matter, and why:

- **The policy is a script, never a decision.** P1 to P6 are deterministic rules over structured facts. The driving model is never asked whether KYC is current.
- **The rules travel inside the revision.** `apply-policy` evaluates the rules in `assets/policy.json`, not whatever the installed package says. A revision's hash covers its assets, so a run recorded against revision 1 is a record of exactly the rules it applied. Chapter 5 depends on this.
- **Judgment is three typed questions, not prose.** `assess` asks a risk tier (score, four levels), whether cash flow is stable (yes/no) and a recommendation (choice of three). Typed answers can be compared, measured and, in Chapter 6, answered by a model of our own with calibrated probabilities. The step's inputs are figures only: no names, no dates of birth, no notes.
- **The person-only step is enforced by the driver.** `confirm-with-person` is a client task whose template says a credit officer must answer it, and `src/brightwater/procedure.py` routes it to a person callback and never to the model.
- **The rationale is linted before the procedure sees it.** The driver lints each draft and asks again with the errors, so the `file` step (which lints again, as a guard) never receives one that fails.

Two deliberate drops, recorded in `source/README.md`: the procedure does not walk the memory graph to name people (every fact the policy uses about connected businesses is already a governed figure, and names are kept out of the judgment step), and `assess` is asked on every application rather than only on reviews (keys produced on one branch cannot be read after branches join, and asking every time gives Chapter 6 labels across every band).

`aip-spec validate skills/brightwater-loan-assessment` passes, and CI runs it.

## The driver

`brightwater procedure run` starts a run and acts only at pauses. With no decision model configured, every `decision` pauses and the driving model answers it (this is option A in the Chapter 6 comparison). AIP records such answers with resolver `manual`, exactly as it records a person's, so the driver also writes who actually answered (`answered_by: agent:<model>`) and the AIP run id onto the decision row. That gap in AIP's attribution, and how Chapter 6 closes it properly, is worth noticing now.

## Do it yourself

> How the chapters fit together, what carries over between them, and fixes for the common errors: [Working through the chapters](working-through-the-chapters.md).

Steps 1 to 5 use the scripted driver and need no API key. Step 6 is the live comparison and needs one.

**1. Branch from the chapter's tag and start the AIP services.** From this chapter on, AIP 0.7.0 needs Python 3.14, so the environment is recreated: `--clear` replaces the 3.12 environment in `.venv` with a 3.14 one, and the install puts every dependency back into it.

```bash
git switch -c my-ch4 ch4
docker compose --profile aip up -d --wait --build
uv venv -p 3.14 --clear
source .venv/bin/activate
uv pip install -e ".[dev]"
brightwater --help
```

Every `brightwater` command runs inside the virtual environment: in a new terminal, run `source .venv/bin/activate` from the repo first, or put `uv run` in front of the command.

Then:

```bash
brightwater fixture generate
brightwater db init && brightwater db load && brightwater policy sync
```

These lines bring the fixture and the database to a known state; they take a few seconds and are safe to rerun. `db init` also empties `agent.decision`, which step 4's count relies on. The `aip` profile adds a second Neo4j, for AIP's run history, and the AIP server on port 8000. The environment where this walkthrough was checked has no Docker, so there the server ran as `aip server --backend filesystem --token brightwater-aip-local` instead of on the Neo4j backend. The client commands and their output are the same either way.

**2. Point the client at the server and publish revision 1.**

```bash
aip config --server http://localhost:8000 --token brightwater-aip-local
aip publish skills/brightwater-loan-assessment
aip list
```

```text
brightwater-loan-assessment@d95fc99c75ebbc06
brightwater-loan-assessment  d95fc99c75ebbc06  1 revision(s)  Assess a Brightwater Community Bank small-business loan application end to end. ...
```

`d95fc99c75ebbc06` is the revision: a hash over the skill's `SKILL.md`, its scripts and its assets, including `assets/policy.json`. The same files give the same hash, so yours should match. From now on "the procedure" means this exact set of bytes.

**3. Run the procedure on the six applicants.**

```bash
brightwater procedure run --handbuilt --model scripted --auto-confirm chris
```

```text
APP-H1 run 1: approve  filed           aip 12b4b0fdc11044bb  steps gather>apply-policy>assess>draft-rationale>file>needs-person
APP-H2 run 1: decline  filed           aip 175b48a863d548c6  steps gather>apply-policy>assess>draft-rationale>file>needs-person
APP-H3 run 1: review   filed           aip 50560a023204446c  steps gather>apply-policy>assess>draft-rationale>file>needs-person
APP-H4 run 1: review   filed           aip 6c5f088db1e04704  steps gather>apply-policy>assess>draft-rationale>file>needs-person
APP-H5 run 1: review   filed           aip f3d17eff884040ed  steps gather>apply-policy>assess>draft-rationale>file>needs-person
APP-H6 run 1: decline  filed           aip 8e95e34fc364471a  steps gather>apply-policy>assess>draft-rationale>file>needs-person>confirm-with-person>record-confirmation
```

Compare with Chapter 3. The steps are the procedure's, not the model's: every run gathers, applies the policy as a script, asks the judgment questions, drafts, files and checks whether a person is needed. APP-H6 takes the extra branch, and this time it ends `filed`, because `--auto-confirm chris` played the credit officer. In Chapter 3 it stayed `pending_person`, because nothing there could ask a person. The `aip` column is the AIP run id, which the decision row also stores. Your run ids will differ.

**4. Repeat one applicant and compare what was read.**

```bash
brightwater procedure run APP-H4 --model scripted --repeat 3
docker compose exec -T postgres psql postgresql://brightwater:brightwater@localhost/brightwater -c \
  "select configuration, count(*) runs, count(distinct records_read::text) distinct_reads,
          count(distinct cited_records::text) distinct_cites from agent.decision
   where application_id = 'APP-H4' group by 1"
```

```text
 configuration | runs | distinct_reads | distinct_cites
---------------+------+----------------+----------------
 ch4-procedure |    4 |              1 |              1
```

Four runs (step 3's and these three) read exactly the same records and cite exactly the same ones. The scripted analyst in Chapter 3 also repeats itself, because it follows a fixed plan, so this alone does not show the difference. What it shows is where the repeatability comes from: here the order of work and the records read are fixed by the procedure, so they stay fixed whichever model drives it. The live comparison in step 6 is where that shows up as a difference.

**5. See the runs on the server.**

```bash
aip runs | head -3
```

```text
58f5f24d94cd408d  brightwater-loan-assessment@d95fc99c75ebbc06  done  7 step(s)  2026-11-07T23:42:07.609887+00:00
...
```

Every run is recorded against the revision that ran. Chapter 5 depends on this. The AIP console (`npx aip-console --server http://localhost:8000`) draws the graph and each run's path through it.

**6. The live comparison (needs an Anthropic API key).**

```bash
export ANTHROPIC_API_KEY=...
brightwater procedure run --handbuilt --repeat 3 --auto-confirm chris
brightwater results table
```

It drives the same procedure with `claude-opus-5-5` and adds the `ch4-procedure` column beside Chapter 3's. Its numbers are under [The proof](#the-proof).

## The proof

**Verified in CI** (`tests/test_ch4_procedure.py`): the skill validates; every hand-built applicant gets the policy's outcome; three runs of APP-H4 take identical steps and read identical records; the person-only step reaches the person callback exactly when P6 applies and never reaches the answerer; the filed decision carries `answered_by` and the AIP run id.

**The live comparison** (RUNBOOK.md, Ch 4) runs the same six applicants three times with `claude-opus-5-5` as the driver and adds the `ch4-procedure` column to the table:

The live run, on 2026-11-10:

| Metric | ch3-freeform | ch4-procedure |
|---|---|---|
| Runs | 18 | 18 |
| Wall clock per decision, s | 22.4 (15.2 to 35.2) | 14.0 (11.3 to 21.6) |
| Model time per decision, s | 22.1 (14.9 to 34.8) | 13.1 (10.3 to 20.7) |
| Model turns | 4 (3 to 4) | 2 (2 to 2) |
| Cost per decision, USD | 0.1118 (0.0800 to 0.1720) | 0.0355 (0.0314 to 0.0505) |
| Outcome agrees with policy | 18/18 | 18/18 |
| Linter first-pass rate | 9/18 | 18/18 |
| Run-to-run consistency (same steps, same records read) | 0/6 applicants | 6/6 applicants |
| Human pauses | 0 | 3 |

The three human pauses are APP-H6's three runs, each a P6 confirmation. Query 03 for APP-H4 (`results/live/ch4-step-orders.json`) now lists the three Chapter 3 orders above beside one order for all three procedure runs:

```text
gather > apply-policy > assess > draft-rationale > file > needs-person
```

What changes and why: model turns fall to two per run (one decision, one rationale) because the procedure decides the order; agreement with the policy is guaranteed by construction because the policy is a script; consistency becomes identical steps and identical records read on every repeat; and the cost per decision is the cost of two short prompts. The model is still a frontier model here, on purpose: this chapter isolates what the procedure buys. Chapter 6 replaces the judgment, and Chapter 7 the driver.

## Try it

**1. Change the rules inside the skill.** In `skills/brightwater-loan-assessment/assets/policy.json`, change P3's `approve_line` from `"1.25"` to `"1.15"`. Do not publish. Then run Cobble Lane Bakery (DSCR 1.17) twice, once against the published revision and once against the folder:

```bash
sed -i.bak 's/"approve_line": "1.25"/"approve_line": "1.15"/' skills/brightwater-loan-assessment/assets/policy.json && rm skills/brightwater-loan-assessment/assets/policy.json.bak
brightwater procedure run APP-H3 --model scripted
brightwater procedure run APP-H3 --model scripted --skill skills/brightwater-loan-assessment
docker compose exec -T postgres psql postgresql://brightwater:brightwater@localhost/brightwater -c \
  "select outcome, policy_version, run_id from agent.decision where application_id = 'APP-H3' order by filed_at desc limit 2"
aip runs | head -1
```

`src/brightwater/policy.py` still says 1.25. Which outcome does each run give? What will the decision rows say about the rules they applied?

<details><summary>What happens, and why it matters</summary>

```text
APP-H3 run 1: review   filed           aip 811d5be8697d4951  steps gather>apply-policy>assess>draft-rationale>file>needs-person
APP-H3 run 1: approve  filed           aip a600e06247d64189  steps gather>apply-policy>assess>draft-rationale>file>needs-person
 outcome |        policy_version         |      run_id
---------+-------------------------------+------------------
 approve | P1v1.P2v1.P3v1.P4v1.P5v1.P6v1 | a600e06247d64189
 review  | P1v1.P2v1.P3v1.P4v1.P5v1.P6v1 | 811d5be8697d4951
811d5be8697d4951  brightwater-loan-assessment@d95fc99c75ebbc06  done  7 step(s)  ...
```

The run against the published revision applies the rules that shipped inside it, so APP-H3 goes to review whatever is in your working copy. That is the guarantee this chapter set up: a run recorded against a revision is a record of exactly the rules it applied.

The run against the folder applies your edit and approves. Look at what it left behind. Its decision row claims `P3v1`, the same as the other, although it used a different approve line. And its run id is not in `aip runs`, because runs from a local folder are not recorded on the server. A rule changed with no new version and no revision to point at is exactly the gap Chapter 0's first exercise found, now one layer up. Chapter 5 closes it: a changed rule gets a new version in the system of record and ships as a new revision.

Undo it: `git checkout skills/`. If you published the edited folder by mistake, the server now has a second revision and resolves the name to it; Chapter 5 covers what to do about that, and `aip pin brightwater-loan-assessment d95fc99c75ebbc06` holds the name at revision 1 meanwhile. Clear the pin with `aip pin --clear brightwater-loan-assessment` before you start Chapter 5, which expects the name to follow the newest revision.
</details>

**2. Ask the judgment step for a name.** The `assess` step sees figures only. Try to give it the applicant's legal name by adding one input to the step in `SKILL.md`, after `fired`:

```yaml
      - name: legal_name
        type: string
```

Then validate the skill:

```bash
aip-spec validate skills/brightwater-loan-assessment
```

<details><summary>What happens</summary>

```text
{"path": "skills/brightwater-loan-assessment/SKILL.md", "kind": "unproduced_input", "message": "step `assess` consumes `legal_name`, which no step produces on every path to it; declare them on the start step `gather` if the client supplies them, or as `outputs` of an upstream step", "location": "body:$.steps[2]"}
INVALID: 1 error(s) ...
```

The command exits with status 1. The validator enforces the data flow: a step can read only what an earlier step produces on every path to it, and nothing produces a name. To get one into the judgment you would have to change `gather` so that it outputs one, which is a visible, reviewed change to a published artifact. In Chapter 3 the privacy boundary was an instruction in a prompt. Here it is part of the procedure's contract, and Chapter 6 trains a model on exactly these inputs.

Undo it: `git checkout skills/`.
</details>

**3. Be the person.** Run Saltmarsh Ceramics without `--auto-confirm`, and refuse to confirm:

```bash
brightwater procedure run APP-H6 --model scripted
```

Answer `n` to the question, give any user id, and add a note. What status will the decision have?

<details><summary>What happens</summary>

The driver stops at `confirm-with-person` and shows you the task:

```text
Rationale as drafted:

Outcome: decline
DSCR is 0.96 [cube:application_month.dscr/APP-H6].
Group exposure is $204,000 [cube:group_member.group_exposure/APP-H6].
Rule P3 applies [policy:P3v1].
Rule P6 applies [policy:P6v1].

This task must be answered by a credit officer, not by an agent. Return JSON with `confirmed`
(true to file the decision as drafted, false to send it back), `confirmed_by` (your user id) and
`confirmation_note`.

Confirm this decision? [y/N] n
Your user id: officer-7
Note: DSCR history needs a second look
APP-H6 run 1: decline  pending_person  aip 8f8a0a54fcdc42b5  steps gather>apply-policy>assess>draft-rationale>file>needs-person>confirm-with-person>record-confirmation
```

The decision stays `pending_person`. The step is a `client_task` like the rationale, but the driver routes it to a person callback and never to the answering model, so no model, however it is prompted, can confirm a decline that P6 reserved for a person. `tests/test_ch4_procedure.py` checks that the answerer never receives this step.
</details>

## What to carry forward

The procedure is now a published, hash-identified artifact, and every run is recorded against the revision that ran. That makes the next question answerable: when the policy changes, which applicants are affected, and which past decisions were made under the old rules?

**Previous:** [Chapter 3](ch3-free-form-agent.md) · **Next:** Chapter 5. A governed policy change
