# Chapter 3. Free-form agent, fully recorded

**Tag:** `ch3` · **Services:** Postgres, Neo4j (memory), Cube, Langfuse · **Proof:** the baseline table (wall time, model time, turns, cost, outcome, linter retries), plus a graph query showing that repeated runs took different step orders.

Chapters 0 to 2 built everything an agent needs to be right: governed figures, a graph of who owns what, and a policy engine to check its answer against. This chapter adds the agent, and deliberately gives it as much freedom as a typical first agent gets: the SOP as its instructions, a handful of tools, and no prescribed order. Everything it does is recorded, because this run is the baseline every later chapter is measured against, and because Chapter 4 compiles a procedure from these recordings.

## What the agent gets

**Instructions.** `skills/source/assessment-sop.md` is the bank's written assessment SOP, the same seven sections a human analyst follows: pull the application, read governed figures only, look one hop out for connections, apply P1 to P6, decide who signs, write the rationale, file. The agent receives it unchanged.

**Tools.** `src/brightwater/tools.py` defines seven functions. The same functions back the `brightwater tool` CLI (so you can see exactly what the agent sees), this chapter's agent, and the next chapter's procedure scripts.

| Tool | Returns | Record keys like |
|---|---|---|
| `get_application` | the application and applicant identity | `credit.application:APP-H4` |
| `get_figures` | every governed figure, from Cube | `cube:application_month.dscr/APP-H4` |
| `get_related_facilities` | facilities one hop out, with stake and days past due | `cube:related_facility/APP-H4/FAC-H90-1` |
| `find_connections` | owners and their other holdings, from the memory graph | `registry.ownership:PER-H05/BUS-H90` |
| `get_notes` | the banker's free-text notes (context only) | `relationship.note:NOTE-00002` |
| `get_policy` | rules P1 to P6 with parameters | `policy:P5v1` |
| `file_decision` | files the decision, or rejects it with reasons | |

Every figure comes back with the key of the record it came from. That is what makes the next piece possible.

## The rationale linter

`src/brightwater/linter.py` checks a rationale like code before it can be filed. Three rules, all from SOP section 6:

1. **Outcome first.** The first line is `Outcome: <outcome>` and matches what is being filed.
2. **Every figure cited, to a record this run actually read.** Each sentence with a number carries a `[record-key]`, and every key must be one some tool returned during this run. A citation the run never saw is a hallucinated source, and it fails.
3. **No protected characteristics.** The Equal Credit Opportunity Act (Regulation B) prohibits credit decisions on race, colour, religion, national origin, sex, marital status, age or receipt of public assistance. The rationale may not mention any of them, or a date of birth.

The linter is mechanical by design. It cannot tell whether reasoning is good; it can tell whether reasoning is traceable and lawful in form, which is what makes it reviewable at all. When `file_decision` rejects a rationale, the agent gets the errors back and tries again, and the number of rejections is one of the measured columns.

## The agent loop

`src/brightwater/agent.py` builds the agent with LangChain's `create_agent` (LangGraph underneath) and `Neo4jMemoryMiddleware` from neo4j-agent-memory, which reads relevant memory into the system prompt before every model call and writes each turn back. The middleware embeds what it stores and searches by vector, so it switches on only when Chapter 7 sets `BRIGHTWATER_EMBED_URL`; until then the agent runs without it, and the run's reasoning trace is still written to the graph when the run ends. The model is `claude-opus-5-5` by default (`BRIGHTWATER_DRIVER_MODEL`), or any OpenAI-compatible server (`BRIGHTWATER_DRIVER_BASE_URL`), which is how Chapter 7 swaps in a local model with no code change.

## Recording everything

Three records per run, joined by ids:

- **OpenTelemetry spans** (`src/brightwater/tracing.py`), following the GenAI semantic conventions: one `invoke_agent` root, a `chat <model>` span per model call with token usage, an `execute_tool <name>` span per tool call. They go to a JSON Lines file, which the results table is computed from, and to Langfuse over OTLP when it is running. Langfuse's core is MIT licensed; the tutorial never uses its `ee/` directories.
- **A reasoning trace in the memory graph**: `(:ReasoningTrace)-[:HAS_STEP {order}]->(:ReasoningStep)-[:USES_TOOL]->(:ToolCall)`, with the filed `(:Decision)` linked to the trace and the application.
- **The decision row** in `agent.decision`: outcome, rationale, cited records, records read, who answered, the hash of the metric definitions in force, and the policy version.

`src/brightwater/bench.py` turns those into one line per run in `results/runs.jsonl`, and `brightwater results table` computes `results/RESULTS.md` from those lines alone. Cost uses list prices checked on 2026-11-06 and kept in `results/prices.yaml` (Opus 5.5: $4 per million input tokens, $20 per million output).

## Do it yourself

> How the chapters fit together, what carries over between them, and fixes for the common errors: [Working through the chapters](working-through-the-chapters.md).

Steps 1 to 4 need no API key and no Neo4j. Step 5 is the live baseline and needs both.

**1. Branch from the chapter's tag, start the services, and add the decisions table.**

```bash
git switch -c my-ch3 ch3
docker compose up -d --wait
source .venv/bin/activate
uv pip install -e ".[dev]"
```

The install picks up any dependencies this chapter adds. Every `brightwater` command runs inside the virtual environment: in a new terminal, run `source .venv/bin/activate` from the repo first, or put `uv run` in front of the command.

Then:

```bash
brightwater fixture generate
brightwater db init && brightwater db load && brightwater policy sync
```

These lines bring the fixture and the database to a known state; they take a few seconds and are safe to rerun. `db init` now also applies `003_decisions.sql`, which creates `agent.decision`. Langfuse is optional; step 5 shows how to start it.

**2. See exactly what the agent sees.**

```bash
brightwater tool get_figures APP-H4
brightwater tool get_related_facilities APP-H4
```

```text
{
  "figures": {
    ...
    "dscr": {
      "value": 1.51,
      "record": "cube:application_month.dscr/APP-H4"
    },
    ...
  "semantic_version": "5604e10ef1b8",
  "records": [
    "cube:application_profile.kyc_current/APP-H4",
    ...
```

```text
    {
      "facility_id": "FAC-H90-1",
      "business_id": "BUS-H90",
      "owner_stake_pct": 35,
      "days_past_due": 80,
      "record": "cube:related_facility/APP-H4/FAC-H90-1"
    }
```

These are the same functions the agent calls. Every value arrives with the key of the record it came from, and `get_figures` also names the definitions that produced it (`semantic_version`, a hash of the Ossie file). Those keys are what the linter will hold the rationale to. `find_connections` reads the memory graph, so without Neo4j it returns `{"error": "memory graph unavailable: ..."}` instead of failing the run.

**3. Run the loop with the scripted analyst.**

```bash
brightwater agent run --handbuilt --model scripted --no-memory
```

```text
APP-H1 run 1: approve  lint rejections 1 tools get_application>get_figures>get_related_facilities>find_connections>get_policy>file_decision>file_decision
APP-H2 run 1: decline  lint rejections 1 tools get_application>get_figures>get_related_facilities>find_connections>get_policy>file_decision>file_decision
APP-H3 run 1: review   lint rejections 1 tools get_application>get_figures>get_related_facilities>find_connections>get_policy>file_decision>file_decision
APP-H4 run 1: review   lint rejections 1 tools get_application>get_figures>get_related_facilities>find_connections>get_policy>file_decision>file_decision
APP-H5 run 1: review   lint rejections 1 tools get_application>get_figures>get_related_facilities>find_connections>get_policy>file_decision>file_decision
APP-H6 run 1: decline  lint rejections 1 tools get_application>get_figures>get_related_facilities>find_connections>get_policy>file_decision>file_decision
```

`--model scripted` is a stand-in analyst that follows a fixed plan and needs no API key. It deliberately leaves one figure uncited on its first filing, which is why every run shows one lint rejection and `file_decision` twice: the first filing was refused with the linter's errors, and the second passed. `--no-memory` skips Neo4j entirely; with Neo4j running, leave it off and the run also writes its reasoning trace to the graph. Scripted runs check plumbing only and are never written to the results table.

**4. Read what was recorded.**

```bash
docker compose exec -T postgres psql postgresql://brightwater:brightwater@localhost/brightwater -c \
  "select application_id, outcome, status, answered_by, semantic_version, policy_version from agent.decision order by filed_at"
python -c "import json,collections; rows=[json.loads(l) for l in open('results/spans/spans.jsonl')]; t=rows[-1]['trace_id']; print(collections.Counter(r['name'].split()[0] for r in rows if r['trace_id']==t))"
```

```text
 application_id | outcome |     status     |  answered_by   | semantic_version |        policy_version
----------------+---------+----------------+----------------+------------------+-------------------------------
 APP-H1         | approve | filed          | agent:scripted | 5604e10ef1b8     | P1v1.P2v1.P3v1.P4v1.P5v1.P6v1
 ...
 APP-H6         | decline | pending_person | agent:scripted | 5604e10ef1b8     | P1v1.P2v1.P3v1.P4v1.P5v1.P6v1
Counter({'chat': 8, 'execute_tool': 7, 'invoke_agent': 1})
```

Each decision row names who answered, the definitions and the policy in force. APP-H6 is `pending_person`: P6 requires a person, and nothing in the free-form agent can supply one. Chapter 4 adds that step. The last run left one `invoke_agent` span, eight `chat` spans and seven `execute_tool` spans in `results/spans/spans.jsonl`, the same counts the CI test asserts.

**5. The live baseline (needs an Anthropic API key and Neo4j).**

```bash
docker compose -p langfuse -f docker/langfuse/docker-compose.yml --env-file docker/langfuse/langfuse.env up -d --wait
export LANGFUSE_HOST=http://localhost:3000 LANGFUSE_PUBLIC_KEY=pk-lf-brightwater-local LANGFUSE_SECRET_KEY=sk-lf-brightwater-local
export ANTHROPIC_API_KEY=...
brightwater graph load
brightwater agent run --handbuilt --repeat 3
brightwater graph query 03 --param app=APP-H4
brightwater results table
```

The first two lines start Langfuse and point the traces at it; they are optional, so skip both together (with `LANGFUSE_HOST` set and no Langfuse running, every run tries to export to it). `graph load` is Chapter 1's step 4: `find_connections` reads the memory graph, so it has to be loaded, and loading again is safe. It makes 18 runs on `claude-opus-5-5`, and `results table` writes `results/RESULTS.md` from them. Its numbers are in [The proof](#the-proof) below.

## The proof

**Plumbing, verified in CI** (`tests/test_ch3_agent.py`, `tests/test_linter.py`): for all six applicants the loop files the policy's outcome; the linter rejects the uncited draft and the retry passes; spans carry the GenAI attributes (one `invoke_agent`, eight `chat`, seven `execute_tool` per scripted run); figures carry record keys and the definition version.

**The baseline** needs a frontier model and is produced by RUNBOOK.md, Ch 3: three runs of each hand-built applicant on `claude-opus-5-5`. The table it produces has one column, `ch3-freeform`, and every later chapter adds a column beside it:

The live run, on 2026-11-10:

| Metric | ch3-freeform |
|---|---|
| Runs | 18 |
| Wall clock per decision, s | 22.4 (15.2 to 35.2) |
| Model time per decision, s | 22.1 (14.9 to 34.8) |
| Model turns | 4 (3 to 4) |
| Cost per decision, USD | 0.1118 (0.0800 to 0.1720) |
| Outcome agrees with policy | 18/18 |
| Linter first-pass rate | 9/18 |
| Run-to-run consistency (same steps, same records read) | 0/6 applicants |
| Human pauses | 0 |

Query 03 for APP-H4 (`results/live/ch3-step-orders.json`): three runs, three tool orders.

```text
get_figures > find_connections > get_related_facilities > get_application > get_policy > get_notes > file_decision > file_decision
get_figures > get_notes > find_connections > get_application > get_related_facilities > get_policy > file_decision > file_decision
get_figures > get_notes > get_application > find_connections > get_related_facilities > get_policy > file_decision > file_decision
```

What to look for: wall clock and cost per decision; whether outcomes agree with the policy engine on every run (the free-form agent applies P1 to P6 itself, from text); the linter first-pass rate; and query 03, which lists each run's tool order. A free-form agent with the same instructions and the same tools does not take the same path twice, and its citations differ between runs even when its outcome does not.

## Try it

**1. Make the linter reject a rationale.** Start from the rationale the scripted analyst filed for APP-H4 and make four drafts that each break one rule. Before you run it, write down which rule you expect each draft to break.

```bash
python - <<'EOF'
from brightwater.linter import lint
read = {"cube:application_month.dscr/APP-H4", "cube:group_member.group_exposure/APP-H4",
        "cube:related_facility/APP-H4/FAC-H90-1", "policy:P5v1"}
base = ("Outcome: review\n"
        "DSCR is 1.51 [cube:application_month.dscr/APP-H4].\n"
        "Group exposure is $510,000 [cube:group_member.group_exposure/APP-H4].\n"
        "Rule P5 applies [policy:P5v1].")
drafts = {
    "as filed": base,
    "uncited": base + "\nA related loan at Ridgeback is 80 days past due.",
    "unseen":  base + "\nA related loan is 80 days past due [cube:related_facility/APP-H4/FAC-H90-2].",
    "protected": base + "\nThe owners are married, which adds stability.",
    "wrong outcome": base.replace("Outcome: review", "Outcome: approve"),
}
for name, text in drafts.items():
    v = lint(text, "review", read)
    print(f"{name:14} {'pass' if not v else ''}")
    for x in v:
        print("   ", x)
EOF
```

`read` plays the part of the records the run's tools returned.

<details><summary>What happens, and why it matters</summary>

```text
as filed       pass
uncited
    figure_uncited: no citation in: 'A related loan at Ridgeback is 80 days past due.'
unseen
    unknown_record: [cube:related_facility/APP-H4/FAC-H90-2] was not returned by any tool in this run
protected
    protected_attribute: mentions 'married'
wrong outcome
    outcome_first: rationale says 'approve' but the filing says 'review'
```

Each draft breaks exactly one rule, and each error says what to fix, which is what the agent gets back when `file_decision` refuses a draft. The `unseen` draft is the important one. The key looks right and the facility very nearly exists, but this run never read it, so the citation would send an examiner to a record that did not inform the decision. A citation is only worth something if it can be checked against what the run actually saw, and that is why every tool returns record keys.
</details>

**2. Change a definition and look at the decision row.** In Chapter 2's last exercise, nothing in the output said which DSCR definition was used. Deploy the strict definition again and run the agent on Larkspur:

```bash
sed -i.bak 's/operating_inflow + nonoperating_inflow - outflow/operating_inflow - outflow/' semantic/brightwater.ossie.yaml && rm semantic/brightwater.ossie.yaml.bak &&
brightwater semantic build &&
brightwater agent run APP-H1 --model scripted --no-memory
docker compose exec -T postgres psql postgresql://brightwater:brightwater@localhost/brightwater -c \
  "select application_id, outcome, semantic_version, policy_version from agent.decision where application_id = 'APP-H1' order by filed_at"
```

This needs step 3's APP-H1 row; if you have run `db init` since, run step 3 again first. What will distinguish the new row from the one step 3 filed?

<details><summary>What happens</summary>

```text
APP-H1 run 1: review   lint rejections 1 tools get_application>get_figures>get_related_facilities>find_connections>get_policy>file_decision>file_decision
 application_id | outcome | semantic_version |        policy_version
----------------+---------+------------------+-------------------------------
 APP-H1         | approve | 5604e10ef1b8     | P1v1.P2v1.P3v1.P4v1.P5v1.P6v1
 APP-H1         | review  | e012ce0bb95d     | P1v1.P2v1.P3v1.P4v1.P5v1.P6v1
```

Two decisions on the same application under the same policy with different outcomes, and now the row says why: the definitions in force were different. `semantic_version` is a short hash of the Ossie file, so anyone holding the repo's history can find the exact definition behind each decision.

Undo it: `git checkout semantic/ && brightwater semantic build`. You can leave the extra row, or delete it:

```bash
docker compose exec -T postgres psql postgresql://brightwater:brightwater@localhost/brightwater -c \
  "delete from agent.decision where semantic_version = 'e012ce0bb95d'"
```
</details>

**3. What the linter cannot see.** Read the rationale the scripted analyst filed for Northpine:

```bash
docker compose exec -T postgres psql -tA postgresql://brightwater:brightwater@localhost/brightwater -c \
  "select rationale from agent.decision where application_id = 'APP-H4' order by filed_at limit 1"
```

The reason Northpine is in review is Ridgeback's loan, 80 days late. Is that in the rationale? Did it pass the linter?

<details><summary>What you find</summary>

```text
Outcome: review
DSCR is 1.51 [cube:application_month.dscr/APP-H4].
Group exposure is $510,000 [cube:group_member.group_exposure/APP-H4].
Rule P5 applies [policy:P5v1].
```

It passed, and it never mentions the late loan, the co-owner or Ridgeback. Every figure is cited and every citation is real, so the linter has nothing to object to. The linter checks that reasoning is traceable and lawful in form. It does not check that the reasoning is complete or good, and its own docstring says so.

The scripted analyst is deliberately thin, but a real model can make the same omission on one run and not on the next, which is part of what the live baseline measures. Chapter 4 narrows the gap from the other side: the procedure fixes which records are read on every run, and hands the policy's fired rules, with their reasons, to the step that writes the rationale.
</details>

## What to carry forward

The agent can be right, and every run can be audited after the fact. What it cannot be is predictable: the order of work, the records consulted and the rationale's sources vary run to run, and every run pays frontier prices to rediscover the same procedure. Chapter 4 takes these recordings, the SOP and the policy, and compiles them into a procedure that a smaller, cheaper driver follows step by step.

**Previous:** [Chapter 2](ch2-semantic-layer.md) · **Next:** Chapter 4. Compile the runs into an AIP procedure
