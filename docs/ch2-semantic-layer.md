# Chapter 2. Semantic layer

**Tag:** `ch2` · **Services:** Postgres, Neo4j, Cube · **Proof:** change the DSCR definition in one file; the policy result and the agent's tool output both change, and both still reconcile to raw Postgres.

Ask three people at a bank what a borrower's debt service coverage ratio is and you can get three numbers. One counts the owner's capital injection as cash available. One uses the trailing twelve months, one the last fiscal year. One leaves out the loan being applied for. None of them is wrong in isolation. The problem is that each definition lives in someone's query, and nobody can see all three at once.

At the end of Chapter 0 the tutorial has exactly that problem in miniature. DSCR, group exposure and days past due are defined in `metrics_sql.py`, in SQL strings. When the agent arrives in Chapter 3 it would be one more caller writing its own queries. This chapter moves every definition into one governed file, serves it through Cube, and proves the move changed nothing before using it to change something on purpose.

## Definitions in one file: Apache Ossie

`semantic/brightwater.ossie.yaml` is written to the Apache Ossie core specification, version 0.2.0.dev0. Ossie is an open, vendor-neutral format for exchanging semantic models between tools. It was previously called Open Semantic Interchange (OSI) and is now incubating at the Apache Software Foundation, under the Apache-2.0 license. A model is a set of datasets, each with fields (row-level expressions) and metrics (aggregations), plus relationships between datasets.

Choosing a spec over a tool's native format is a hedge worth paying for. The file describes what DSCR means without saying anything about Cube; Cube is one consumer of it, and a second compiler in this chapter is another. The repo vendors the spec's JSON Schema (`semantic/vendor/ossie-schema.json`, with attribution in `NOTICE`) and validates the model against it on every build.

The model has five datasets, each producing one row per application or per application and something else:

| Dataset | One row per | Serves |
|---|---|---|
| `application_profile` | application | `kyc_current`, `days_since_last_adverse_event`, `tenure_years` |
| `application_month` | application, month in the DSCR window | `dscr`, `cash_available`, `debt_service_total` |
| `group_member` | application, business in its group | `group_exposure`, `group_size` |
| `related_facility` | application, related facility | `owner_stake_pct`, `days_past_due`, `max_days_past_due` |
| `application` | application | the shared key the others relate to |

DSCR is a field and a metric:

```yaml
- name: net_cash_flow
  description: Cash available for debt service in the month.
  expression:
    sql: operating_inflow + nonoperating_inflow - outflow
...
metrics:
  - name: dscr
    expression:
      sql: SUM(net_cash_flow) / NULLIF(SUM(debt_service), 0)
```

Two rules from Chapter 0 now have a home.

**Definitions here, thresholds in policy.** The model defines DSCR and never mentions 1.25. It lists related facilities with their owner stake and days past due, and never mentions 25% or 60 days. Those numbers belong to the lending policy, change on a different schedule and need a different approver. The model's `ai_context` says so explicitly, because in Chapter 3 an agent will read it.

**Portable first, dialect where needed.** Field expressions are written in Ossie's portable SQL. Where Postgres spells something differently, the field carries a `POSTGRES` dialect entry, and the compiler prefers it:

```yaml
- name: days_past_due
  expression:
    sql: COALESCE(DATEDIFF(day, oldest_unpaid_due, submitted_on), 0)
    dialects:
      - {dialect: POSTGRES, expression: "COALESCE(submitted_on - oldest_unpaid_due, 0)"}
```

## Serving it: Cube

`brightwater semantic build` compiles the Ossie model into Cube model files under `semantic/cube/model/cubes/`. Fields become dimensions, metrics become measures, relationships become joins, and source columns are qualified with `{CUBE}` so cubes stay unambiguous when joined. The compiler is under 200 lines in `src/brightwater/semantic.py`; it covers the subset of Ossie this model uses, which keeps it readable. The generated files carry a do-not-edit header, and a test fails if they drift from the model.

Cube Core (Apache-2.0) serves them over a REST API, and from this chapter on the policy engine reads its facts from Cube. `src/brightwater/metrics_cube.py` has the same interface as `metrics_sql.py` and defines nothing: it names Cube members and converts the answers into `Facts`. The default backend for `brightwater assess` is now `cube`.

Two practical notes. Cube returns numbers as strings, so every value is converted through `Decimal`, never `float`. And Cube runs in dev mode here, which hot-reloads model files and skips token checks; the client already signs an HS256 token with the shared secret, so nothing changes on the client when Chapter 8 turns authentication on.

## First, prove the move changed nothing

A migration you cannot show is behavior-preserving is a rewrite. `tests/test_ch2_semantic.py` compares the facts from Cube with the facts from the Chapter 0 SQL for the six hand-built applicants and every seventh generated one. They are identical, field for field, including every related facility and its days past due.

## Second, prove Cube serves the definition

`brightwater semantic reconcile` compiles the same Ossie file a second way, straight to SQL, runs it on Postgres, and compares every field row and every metric with what Cube returns. Two independent compilers of one definition, checked against each other, is how we know the number the policy saw is the number the file specifies, and not a stale cache or a hand-edited model file.

```text
$ brightwater semantic reconcile APP-00001 ... APP-00400
application_profile    1,600 values  ok
application_month     20,400 values  ok
group_member           3,448 values  ok
related_facility       5,017 values  ok
reconciled: Cube matches Postgres
```

## Now change a definition on purpose

The bank's original DSCR counts non-operating inflow, which includes owner capital injections, as cash available for debt service. A credit officer would object: an owner topping up the account once is not evidence the business can service debt. The stricter definition is one line.

```diff
       - name: net_cash_flow
         expression:
-          sql: operating_inflow + nonoperating_inflow - outflow
+          sql: operating_inflow - outflow
```

Before deploying a definition change you want to know what it touches. `brightwater semantic impact` compiles both versions straight to SQL and runs the policy on every application under each:

```text
$ brightwater semantic impact --model strict.ossie.yaml
...
APP-01064   approve -> review   DSCR 1.44 -> 1.17  fired P3
APP-H1      approve -> review   DSCR 1.33 -> 1.18  fired P3
20 application(s) change: 9 approve->review, 4 review->decline, 3 approve->decline,
                          2 review->review, 2 decline->decline
```

Twenty of 1,206 applications are affected. Sixteen change outcome; four keep their outcome but for a different set of rules, which matters just as much for the explanation a borrower receives. One of them is Larkspur Bindery, the clean applicant from Chapter 0: a $12,500 owner injection in March was carrying its DSCR from 1.18 to 1.33.

Then deploy it: edit the file, run `brightwater semantic build`, and ask again.

```text
$ brightwater assess APP-H1
application  outcome  person    DSCR   group exp.  fired
APP-H1       review   no        1.18      342,000  P3
```

The policy did not change. `policy.py` is untouched and still says 1.25. The definition changed, in one reviewed line, and the outcome followed. `brightwater semantic reconcile --model strict.ossie.yaml` still reports a match, so Cube is serving exactly the new definition.

The Chapter 0 hand-written SQL, meanwhile, still says 1.3297. Nobody told it. That is the drift a semantic layer exists to prevent, and it is why `metrics_sql.py` stays in the repo only as a test reference from here on.

Revert the line and rebuild, and Larkspur is approved again. `test_one_line_definition_change` runs this whole sequence against a temporary copy of the model and restores the original whatever happens.

## Do it yourself

> How the chapters fit together, what carries over between them, and fixes for the common errors: [Working through the chapters](working-through-the-chapters.md).

No API key is needed. Cube runs in Docker and serves `semantic/cube/model/` from your checkout, with hot reload, so a rebuilt model file takes effect within a second or two.

**1. Branch from the chapter's tag and start the services.**

```bash
git switch -c my-ch2 ch2
docker compose up -d --wait
source .venv/bin/activate
uv pip install -e ".[dev]"
brightwater fixture generate
brightwater db init && brightwater db load && brightwater policy sync
```

The install picks up any dependencies this chapter adds. Every `brightwater` command runs inside the virtual environment: in a new terminal, run `source .venv/bin/activate` from the repo first, or put `uv run` in front of the command.

That brings up Postgres, Neo4j and Cube on `localhost:4000`. The last two lines bring the fixture and the database to a known state; they take a few seconds and are safe to rerun.

**2. Compile the definitions.**

```bash
brightwater semantic build
git status --short
```

```text
wrote semantic/cube/model/cubes/application.yml
wrote semantic/cube/model/cubes/application_profile.yml
wrote semantic/cube/model/cubes/application_month.yml
wrote semantic/cube/model/cubes/group_member.yml
wrote semantic/cube/model/cubes/related_facility.yml
```

`git status` prints nothing: the generated files in the repo were already what the Ossie model compiles to. Open `application_month.yml` and find the `dscr` measure. It is the metric from the Ossie file, translated, with a header telling you not to edit it.

**3. Assess through Cube.**

```bash
brightwater assess --handbuilt
```

```text
application  outcome  person    DSCR   group exp.  fired
APP-H1       approve  no        1.33      342,000  -
APP-H2       decline  no        1.62      161,000  P1
APP-H3       review   no        1.17      216,000  P3
APP-H4       review   no        1.51      510,000  P5
APP-H5       review   no        1.44    1,722,000  P4
APP-H6       decline  yes       0.96      204,000  P3,P6
metrics: cube   policy: P1v1.P2v1.P3v1.P4v1.P5v1.P6v1
```

Lay this beside Chapter 0's table. Every row is identical and only the last line differs: the facts now come from Cube. That is the migration test in miniature. Nothing should change when you move a definition, and nothing did.

**4. Reconcile Cube against Postgres.**

```bash
brightwater semantic reconcile
```

```text
application_profile       24 values  ok
application_month        306 values  ok
group_member              52 values  ok
related_facility          66 values  ok
reconciled: Cube matches Postgres
```

With no ids it checks the six hand-built applicants; pass ids to check more. The second compiler turned the same Ossie file straight into SQL, ran it on Postgres, and got the same rows and the same metrics as Cube. Agreement between two independent compilers is the evidence that Cube serves the file as written.

**5. Measure a definition change before you deploy it.**

```bash
cp semantic/brightwater.ossie.yaml strict.ossie.yaml &&
sed -i.bak 's/operating_inflow + nonoperating_inflow - outflow/operating_inflow - outflow/' strict.ossie.yaml && rm strict.ossie.yaml.bak &&
brightwater semantic impact --model strict.ossie.yaml
```

```text
APP-00089   approve -> review   DSCR 1.32 -> 1.14  fired P3
APP-00157   approve -> decline  DSCR 2.58 -> 0.68  fired P3
...
APP-01064   approve -> review   DSCR 1.44 -> 1.17  fired P3
APP-H1      approve -> review   DSCR 1.33 -> 1.18  fired P3
20 application(s) change: 9 approve->review, 4 review->decline, 3 approve->decline, 2 review->review, 2 decline->decline
```

Nothing has been deployed yet: Cube is still serving the original definition. This is the list a credit officer would review before agreeing to the change. Look at APP-00157, which goes from a DSCR of 2.58 to 0.68: almost all of its cash came from non-operating inflow.

**6. Deploy it, and see who noticed.**

```bash
cp strict.ossie.yaml semantic/brightwater.ossie.yaml && brightwater semantic build
brightwater assess APP-H1
brightwater semantic reconcile
brightwater assess APP-H1 --metrics sql
```

This step needs `strict.ossie.yaml` from step 5. If the first line says `No such file or directory`, run step 5 again first: otherwise the other three lines run against the unchanged definition and everything says approve.

```text
application  outcome  person    DSCR   group exp.  fired
APP-H1       review   no        1.18      342,000  P3
metrics: cube   policy: P1v1.P2v1.P3v1.P4v1.P5v1.P6v1
...
reconciled: Cube matches Postgres
application  outcome  person    DSCR   group exp.  fired
APP-H1       approve  no        1.33      342,000  -
metrics: sql   policy: P1v1.P2v1.P3v1.P4v1.P5v1.P6v1
```

What to check, in order: Cube says **review** at a DSCR of 1.18, reconcile still passes, and the hand-written SQL says **approve** at 1.33. The governed path moved with the definition and still reconciles. The hand-written Chapter 0 SQL did not move, because nobody told it, and it still approves Larkspur. Two answers to "what is Larkspur's DSCR?" in one system is exactly the situation this chapter exists to prevent.

**7. Put it back.**

```bash
git checkout semantic/ && rm strict.ossie.yaml
brightwater semantic build
brightwater assess APP-H1
```

Larkspur is approved again at 1.33. This deletes `strict.ossie.yaml`, so to repeat step 6 later, run step 5 first.

## The proof

```bash
docker compose up -d --wait
brightwater semantic build
pytest tests/test_ch2_semantic.py
```

- The model validates against the Ossie JSON Schema, and the generated Cube files are current.
- Facts from Cube equal facts from the Chapter 0 SQL.
- Cube reconciles to raw Postgres, field rows and metrics.
- Changing one line moves APP-H1 from approve to review in both the policy result and the `brightwater assess --json` tool output, Cube still reconciles under the new definition, the old SQL is shown to be stale, and the impact command names exactly 20 affected applications.

All six tests pass in about a minute.

## Try it

**1. Widen the group.** Group exposure counts the applicant plus every business in which one of its owners holds more than 50%. Suppose Credit Risk wants any 25% stake to count. That is a change to a definition (who is in the group), not to the P4 cap. In a copy of the model, change `o2.pct > 50` to `o2.pct >= 25` and measure it:

```bash
cp semantic/brightwater.ossie.yaml group25.ossie.yaml &&
sed -i.bak 's/o2.pct > 50/o2.pct >= 25/' group25.ossie.yaml && rm group25.ossie.yaml.bak &&
brightwater semantic impact --model group25.ossie.yaml
```

Priya Lindqvist holds 35% of Ridgeback. Will Northpine (APP-H4) be in the list? How many applications in all? Then deploy it (`cp group25.ossie.yaml semantic/brightwater.ossie.yaml && brightwater semantic build`) and run `brightwater assess --handbuilt` and `brightwater semantic reconcile`.

<details><summary>What happens, and why it matters</summary>

```text
APP-00001   approve -> review   DSCR 2.17 -> 2.17  fired P4
APP-00002   approve -> review   DSCR 1.28 -> 1.28  fired P4
...
APP-01186    review -> review   DSCR 1.53 -> 1.53  fired P4,P5
43 application(s) change: 20 approve->review, 14 decline->decline, 9 review->review
```

Forty-three applications change, twenty approvals among them, and every one of them because P4 now fires. None of the hand-built applicants is in the list. After you deploy, the table shows why:

```text
APP-H2       decline  no        1.62      441,000  P1
APP-H4       review   no        1.51      631,500  P5
```

Ridgeback joins Northpine's group, and its $121,500 balance raises the group exposure from $510,000 to $631,500, which is still well under the $1.5M cap. APP-H2's group grows too. The outcomes stay the same, but the figures behind two explanations have changed.

`reconcile` still says `Cube matches Postgres`. That is worth being clear about: reconciliation proves that Cube serves the definition faithfully. It says nothing about whether the definition is a good one. That judgment belongs to the person reviewing the impact list, which is why the list exists.

Undo it: `git checkout semantic/ && rm group25.ossie.yaml && brightwater semantic build`.
</details>

**2. Edit a generated file by hand.** Skip the Ossie model and change Cube's copy directly. On line 30 of `semantic/cube/model/cubes/application_month.yml`, remove the non-operating inflow from `net_cash_flow`:

```bash
sed -i.bak "30s/{CUBE}.operating_inflow + {CUBE}.nonoperating_inflow - {CUBE}.outflow/{CUBE}.operating_inflow - {CUBE}.outflow/" \
  semantic/cube/model/cubes/application_month.yml && rm semantic/cube/model/cubes/application_month.yml.bak
brightwater assess APP-H1
brightwater semantic reconcile
pytest -q tests/test_ch2_semantic.py::test_generated_cube_files_are_up_to_date
```

Cube hot-reloads, so it will serve the edit. Which of the three commands notices?

<details><summary>What happens</summary>

`assess` notices nothing: APP-H1 goes to review at 1.18, exactly as if the change had been made properly. Both checks catch it:

```text
application_profile       24 values  ok
  MISMATCH application_month rows: ["('APP-H1', '2026-03-01', Decimal('20183.490000'), Decimal('6959.121359'))", "('APP-H1', '2026-03-01', Decimal('7683.490000'), Decimal('6959.121359'))"]
  MISMATCH application_month.cash_available APP-H1: postgres=111041.160000 cube=98541.160000
  MISMATCH application_month.dscr APP-H1: postgres=1.329684 cube=1.180000
application_month        306 values  FAILED
group_member              52 values  ok
related_facility          66 values  ok
NOT reconciled
```

and the drift test fails. The mismatch is in March, the month of Larkspur's $12,500 owner injection: 20,183.49 minus 7,683.49 is exactly that injection. The reconcile output points straight at it.

This is why there are two compilers. The policy cannot tell a reviewed change from an unreviewed one, because both arrive as a number. Only a check against the source of the definition can, and `reconcile` exits with status 1, so it can gate a deployment.

Undo it: `git checkout semantic/`.
</details>

**3. Which definition made this decision?** Deploy the strict definition again (steps 5 and 6 of Do it yourself; step 7 deleted `strict.ossie.yaml`, so step 5 has to come first) and run `brightwater assess APP-H1 --json`. Look at the last lines, then revert and run it again.

<details><summary>What you find, and what Chapter 3 adds</summary>

Under the strict definition the decision is `review`; under the original it is `approve`. Both end with the same stamp:

```text
    "policy_version": "P1v1.P2v1.P3v1.P4v1.P5v1.P6v1"
```

The output records which rules were applied and nothing about which definition produced the DSCR they were applied to. If both decisions had been filed, nothing in them would explain why they differ. Chapter 3 fixes this: every decision row it files carries `semantic_version`, a hash of the Ossie model in force, beside the policy version.

Undo it as in step 7.
</details>

## What to carry forward

Three layers now answer three different questions. The systems of record hold what happened. The graph holds who is connected to whom, checked by one schema. The semantic layer holds what every number means, in one file a reviewer can read. Chapter 3 puts an agent on top and gives it the `brightwater` CLI as its tools, which means every number it sees is governed before it ever reasons about one, and every run is recorded so we can measure what comes next.

A note for Chapter 8: the semantic layer reads across coverage on purpose (Northpine's banker needs Ridgeback's days past due, not Ridgeback's ledger), so when row-level security goes on, Cube will query as a service role and enforce entitlements on what it returns rather than on what it reads.

**Previous:** [Chapter 1](ch1-ontology-and-graph.md) · **Next:** [Chapter 3. Free-form agent, fully recorded](ch3-free-form-agent.md)
