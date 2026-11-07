# Chapter 9. The audit packet

**Tag:** `ch9` · **Services:** as Chapter 8 (Postgres with pgaudit, Cube, AIP server, and the control plane when enforcement is on) · **Proof:** any filed decision reconstructed from its id alone; a packet edited after sealing fails chain verification; two governance queries answered from sealed packets.

Every earlier chapter left a record behind: the decision row (Ch 3), the AIP run record (Ch 4), the rule rows (Ch 5), the decision model's attribution (Ch 6), the entitlement log (Ch 8), spans throughout. Each record is good on its own, and none of them answers an examiner's question on its own. This chapter joins them on one id, writes the result down as a single packet at the moment the decision is filed, and seals it so that nobody, including us, can change it later without the change showing.

## One id, every layer

The decision id is minted by the driver before the run starts (`procedure.run` in `src/brightwater/procedure.py`), not by the database after it ends. That is what lets every layer stamp it on its own record while the run is still happening:

| Layer | Where the decision id, or a key the decision row holds, lands |
|---|---|
| Decision row | `agent.decision.decision_id`: the `file` step reads `BRIGHTWATER_DECISION_ID` from its environment and uses it as the primary key |
| Entitlement log | `audit.authz.decision_id`, written by `src/brightwater/identity.py` for every OpenFGA and OPA check made on the decision's behalf (the client's own record of them; see Chapter 8 for what that does and does not prove) |
| Postgres reads | the connection's `application_name` is `brightwater:<decision id>` (`src/brightwater/db.py`), and `log_line_prefix` in `compose.yaml` includes `%a`, so every pgaudit line names the decision |
| OpenTelemetry spans | the root span carries `brightwater.decision_id`; the decision row stores the `trace_id` |
| AIP run record | the decision row stores the AIP run id in `run_id` |
| Policy rule rows | the decision row stores `policy_version` (for example `P1v1.P2v1...P7v1`), which names each `compliance.policy_rule` row by rule and version |
| Lint result | recomputed from the stored rationale, outcome and records read |

So the decision id is the one key an auditor needs. Everything else is reached either directly by that id or through an id the decision row holds.

## What the packet answers

`audit.assemble` in `src/brightwater/audit.py` builds the packet from those layers. It is organised by the questions an examiner actually asks:

| Question | Packet field | From |
|---|---|---|
| Who asked, and who answered? | `who`: `user`, `answered_by`, `confirmed_by`, `reviewed_by`, `configuration` | the decision row and the review log. `user` is the signed-in banker (Ch 8), `answered_by` is `agent:<model>`, `model:<resolver>/<version>` or, when a person decided at a review pause, `user:<name>` (Ch 4, Ch 6), `confirmed_by` is the person who confirmed a P6 or P7 decision, and `reviewed_by` lists each review of the judgment step with what the reviewer changed |
| Was that user allowed? | `entitlement` | `audit.authz` rows for this decision |
| What was read? | `records.read`, `records.cited`, `records.pgaudit_relations` | the record keys every step returned; the rationale's citations; the tables pgaudit saw read under this decision's connections |
| Under which definitions? | `definitions.semantic_version` | the hash of the Ossie model that produced the figures (Ch 2) |
| Which procedure, exactly? | `procedure` (`name`, `revision`, `status`) and `steps` | the AIP run record |
| What did the judgment see and say? | `judgment`: `input`, `answers`, `attribution` | the `assess` step's stored input (figures only), its answers, and who resolved them with what confidence |
| Which rules, and which version of each? | `policy` | the `compliance.policy_rule` row for every rule version in `policy_version` |
| Why this outcome? | `outcome`: outcome, status, `requires_person`, `policy_version`, `rationale`, `lint` | the decision row, with the rationale linted again at assembly time against the records the run read |
| What did the calls look like? | `model_calls` | the spans for the trace: model, token counts, tool, step, resolver, duration |
| How do I find the rest? | `ids`: `aip_run_id`, `trace_id`, `filed_at` | the decision row |

The rationale is the "why", and it is only worth something because of Chapter 3's linter: every figure in it carries a citation, every citation is a key in `records.read`, and the policy citations (`policy:P3v1`) point at rows that are in the packet too. A reader can follow each sentence of the rationale to the record behind it without leaving the packet.

## Sealing

Sealing is append-only and hash-chained.

1. **One serialization.** `canonical()` round-trips the packet through JSON and writes it with sorted keys and no whitespace, so the same packet always produces the same bytes.
2. **One hash per packet.** `packet_sha256` is SHA-256 over those bytes.
3. **One chain.** `chain_hash` is SHA-256 over the previous row's `chain_hash` followed by this packet's hash. The first row chains from the literal `genesis`.
4. **One writer at a time.** `seal()` takes `LOCK TABLE audit.packet IN EXCLUSIVE MODE` inside a transaction before reading the previous hash, so two decisions filed at the same moment cannot both chain from the same row.

The row lands in `audit.packet` (`schema/postgres/004_audit.sql`) with `seq`, `decision_id` (unique, so a decision is sealed once), `packet`, `packet_sha256`, `prev_hash`, `chain_hash` and `sealed_at`. The agent's database role, `brightwater_app`, is granted `SELECT` and `INSERT` on the table and nothing else.

The driver seals automatically. `_finish` in `procedure.py` writes who answered and the AIP run id onto the decision row and then calls `audit.seal`, whenever `BRIGHTWATER_AUDIT` is `on`, which is the default. `brightwater audit --seal <decision id>` seals one by hand, for a decision filed with sealing turned off.

`audit.verify()` walks the table in order, recomputes every packet hash and every chain hash, and reports the first row that does not match:

- edit a packet and leave its stored hash alone, and verification fails at that row with `packet edited`;
- edit the packet and rewrite its stored hash to match, and the packet check passes but the chain does not, so it fails with `chain broken`;
- delete or reorder rows, and the next row's `prev_hash` no longer matches, which is also `chain broken`.

One limit is worth stating plainly. Removing rows from the end of the table leaves a shorter chain that still verifies, because nothing inside the table remembers how long it was. That is why every verification prints the chain head: keep a copy of the head somewhere the database owner cannot edit, and a truncated chain no longer matches it.

## Two governance queries

Both read sealed packets only, which is the point: they answer from the same record an examiner would be handed, not from live tables that could have moved since.

- **`overridden`** lists every decision where a person changed what the decision model proposed: the packet's attribution says it was reviewed, and its `proposed` answers differ from the final ones. Each row gives the changed questions as `[proposed, final]` and the packet's `who` block.
- **`under-threshold`** lists every decision a model answered with confidence below the procedure author's thresholds on the `assess` step: 0.6 for `risk_tier`, 0.6 for `recommendation`, and 0.2 for `cash_flow_stable`. `cash_flow_stable` is a yes/no question whose confidence is the probability of the answer given, so its threshold is read as a margin above even odds: below 0.7 counts as unsure. Each row gives the low confidences, the resolver, the model version and whether a person reviewed it.

Chapter 6 made the second query possible, because answers by the driving model (`resolver: manual`) carry no confidence at all. Only a model of our own, with calibrated probabilities, can be held to a threshold after the fact.

## Do it yourself

> How the chapters fit together, what carries over between them, and fixes for the common errors: [Working through the chapters](working-through-the-chapters.md).

Everything here uses the scripted driver and, for the governance query, the stand-in decision model from Chapter 6. No API key is needed. Enforcement stays off, so the packets have no signed-in user; step 4 of Chapter 8's walkthrough is where that would come from.

**1. Branch from the chapter's tag.**

```bash
git switch -c my-ch9 ch9
docker compose --profile aip up -d --wait
source .venv/bin/activate
uv pip install -e ".[dev]"
```

The install picks up any dependencies this chapter adds. Every `brightwater` command runs inside the virtual environment: in a new terminal, run `source .venv/bin/activate` from the repo first, or put `uv run` in front of the command.

Then:

```bash
brightwater fixture generate
brightwater db init && brightwater db load && brightwater policy sync
aip config --server http://localhost:8000 --token brightwater-aip-local
aip publish skills/brightwater-loan-assessment
rm -f ~/.brightwater/token.json
python -m brightwater.fake_dm --port 8081 &
```

The first five lines bring the fixture, the database, the AIP client and the published procedure to a known state and sign you out (with a token left from Chapter 8, `who.user` below would not be null); they take a few seconds and are safe to rerun. The last line starts the Chapter 6 stand-in decision model in the background of this terminal; if it says `Address already in use`, it is still running from an earlier chapter, and you can carry on. Stop it when you are done with `pkill -f brightwater.fake_dm`. `db init` starts `audit.packet` empty, so the chain below starts at sequence 1.

**2. File a decision and read its packet.**

```bash
brightwater procedure run APP-H6 --model scripted --auto-confirm officer-7 &&
ID=$(docker compose exec -T postgres psql postgresql://brightwater:brightwater@localhost/brightwater -tAc \
     "select decision_id from agent.decision where application_id='APP-H6' order by filed_at desc limit 1") &&
brightwater audit "$ID"
```

The lines are chained because `audit` with an empty id only verifies the chain: if the run had not filed, it would print `chain verified: 0 packet(s)` and look like success.

```text
APP-H6 run 1: decline  filed           aip 4a749b767f3a4f71  steps gather>apply-policy>assess>draft-rationale>file>needs-person>confirm-with-person>record-confirmation
{
  ...
  "who": {
    "user": null,
    "answered_by": "agent:scripted",
    "reviewed_by": [],
    "confirmed_by": "user:officer-7",
    "configuration": "ch4-procedure"
  },
  ...
  "procedure": {
    "name": "brightwater-loan-assessment",
    "status": "done",
    "revision": "d005aa1a7fce1d5f"
  },
  ...
}

packet seq 1  chain 5d4080b124984ca7...
chain verified: 1 packet(s), head 5d4080b124984ca7...
```

The driver sealed the packet when the decision was filed; `audit` only reads it back and verifies the chain. Because this run went through the AIP server, `procedure.revision` names the revision that ran. A full packet, from a run against the skill folder, is shown and read below.

**3. File a few more with the decision model, and ask the governance questions.**

```bash
BRIGHTWATER_RESULTS=/tmp/bw-check brightwater procedure run APP-H1 APP-H3 APP-H5 --model scripted \
    --decision-model local:http://127.0.0.1:8081/v1 --auto-confirm officer-7
brightwater audit
brightwater audit --query under-threshold
brightwater audit --query overridden
```

```text
chain verified: 4 packet(s), head a07dc2478c185f1b...
[
  {
    "decision_id": "20a0345c-717d-42a8-8418-1e6d51bc2640",
    "low": {
      "recommendation": 0.535568
    },
    "resolver": "endpoint",
    "model": "default_model",
    "reviewed": true
  },
  {
    "decision_id": "f5f565e0-c334-40e0-82a9-6d48881649ea",
    "low": {
      "risk_tier": 0.588011
    },
    "resolver": "endpoint",
    "model": "default_model",
    "reviewed": true
  }
]
2 decision(s)
[]
0 decision(s)
```

Write down the head, `a07dc2478c185f1b...`; the exercises use it. Yours will be a different hash, because each packet holds its own decision id and timestamps: wherever the exercises below show `a07dc247...` or another chain hash, compare against yours. Two of the stand-in's answers were under the author's thresholds (APP-H3's recommendation and APP-H5's risk tier), and both were reviewed. APP-H6's answer came from the driver, with no confidence, so it cannot appear in this query at all. `overridden` is empty because `--auto-confirm` accepts every proposal as it stands.

`brightwater audit <decision id>` prints the sealed packet, its sequence number and chain hash, then verifies the whole chain. With no id it only verifies. A failed verification prints the row and the reason and exits with status 4, so it can gate a job.

Here is a real packet for APP-H6 from the scripted driver, abridged: long lists are cut with `...`, and only two of the seven rule rows are shown. Keys appear in the order Postgres stores `jsonb`, not alphabetically.

```json
{
  "ids": {
    "filed_at": "2026-11-07 01:11:43.362598+00:00",
    "trace_id": "32f423bd0d6cba51121e94cff24c81d0",
    "aip_run_id": "256ea4782ca14fc1"
  },
  "who": {
    "user": null,
    "answered_by": "agent:scripted",
    "confirmed_by": "user:officer-7",
    "reviewed_by": [],
    "configuration": "ch4-procedure"
  },
  "steps": ["gather", "apply-policy", "assess", "draft-rationale", "file", "needs-person",
            "confirm-with-person", "record-confirmation", "end"],
  "policy": [
    {"rule_id": "P3", "version": 1, "title": "Debt service coverage",
     "body": "DSCR at or above the approve line stands. Inside the judgment band it goes to review. Below the floor it declines.",
     "params": {"floor": "1.10", "approve_line": "1.25"}},
    {"rule_id": "P6", "version": 1, "title": "Long-tenured declines need a person",
     "body": "Declining a customer of 10 or more years requires a person to confirm before the decision is filed.",
     "params": {"tenure_years": 10}},
    "..."
  ],
  "outcome": {
    "lint": "pass",
    "status": "filed",
    "outcome": "decline",
    "rationale": "Outcome: decline\nDSCR is 0.96 [cube:application_month.dscr/APP-H6].\nGroup exposure is $204,000 [cube:group_member.group_exposure/APP-H6].\nRule P3 applies [policy:P3v1].\nRule P6 applies [policy:P6v1].",
    "policy_version": "P1v1.P2v1.P3v1.P4v1.P5v1.P6v1.P7v1",
    "requires_person": true
  },
  "records": {
    "read": ["credit.application:APP-H6", "cube:application_month.dscr/APP-H6",
             "cube:group_member.group_exposure/APP-H6", "cube:related_facility/APP-H6/FAC-H06-1",
             "ledger.business:BUS-H06", "policy:P1v1", "...", "policy:P7v1"],
    "cited": ["cube:application_month.dscr/APP-H6", "cube:group_member.group_exposure/APP-H6",
              "policy:P3v1", "policy:P6v1"],
    "pgaudit_relations": null
  },
  "judgment": {
    "input": {
      "facts": {"dscr": "0.9600", "kyc_current": true, "tenure_years": "17.37", "group_exposure": "204000.00",
                "related_facilities": [{"facility_id": "FAC-H06-1", "days_past_due": 0, "owner_stake_pct": "100.00", "...": "..."}],
                "days_since_last_adverse_event": null, "application_id": "APP-H6"},
      "fired": [{"rule_id": "P3", "effect": "decline", "reason": "DSCR 0.96 is below the 1.10 floor"},
                {"rule_id": "P6", "effect": "requires_person", "reason": "customer for 17.4 years; a person must confirm the decline"}],
      "rule_outcome": "decline",
      "monthly_debt_service": 5806.17,
      "monthly_net_cash_flow": [5511.0, 5951.32, 5855.88, "..."]
    },
    "answers": {"risk_tier": 3, "recommendation": "decline", "cash_flow_stable": false},
    "attribution": {"model": null, "resolver": "manual", "reviewed": false, "confidence": null}
  },
  "procedure": {"name": "brightwater-loan-assessment", "status": "done", "revision": null},
  "decision_id": "d487085f-cd77-4814-9e86-181dee0c1426",
  "definitions": {"semantic_version": "5604e10ef1b8"},
  "entitlement": [],
  "model_calls": [
    {"name": "execute_step gather", "ms": 724.0, "brightwater.step": "gather"},
    {"name": "execute_step assess", "ms": 0.0, "brightwater.step": "assess", "brightwater.resolver": "manual"},
    "...",
    {"name": "run_procedure brightwater-loan-assessment", "ms": 1865.9, "gen_ai.request.model": "scripted"}
  ]
}

packet seq 24  chain 7751204ea1ecf120...
```

Read it the way an examiner would. The decline came from P3 (DSCR 0.96 below the 1.10 floor), and P6 held it for a person because the customer has banked here for 17.4 years; `officer-7` confirmed it. The rationale cites four records, all of which are in `records.read`, and lints clean. The judgment step saw figures and fired rules, and no name or date of birth.

The empty and null fields are honest about how this run was made, and each fills in on a full stack:

- `user` is null and `entitlement` is empty because nobody signed in and Chapter 8's enforcement (`BRIGHTWATER_ENFORCE=on`) was off.
- `revision` is null because the run used the skill folder in the repo; runs against a published revision record it.
- `model_calls` holds step spans only, because the scripted driver makes no model calls. With a real driver it also holds a `chat` span per call, with model and token counts.
- `attribution` says `manual` with no confidence, because the driving model answered the judgment (option A in Chapter 6). With the decision model it names the resolver, the model version and a confidence per question.
- `pgaudit_relations` is null because the packet reads the Postgres log from the file named by `BRIGHTWATER_PGAUDIT_LOG`, and none was set. With Compose, Postgres logs to the container's output, so `docker compose logs postgres > pg.log` and `BRIGHTWATER_PGAUDIT_LOG=pg.log` before sealing is one way to provide it.

## The proof

**Verified here** (`tests/test_ch9_audit.py`, with the scripted driver and the stand-in decision model):

- The APP-H6 packet reconstructs the decision: the procedure name and steps, `answered_by: agent:scripted` and `confirmed_by: user:officer-9`, a decline that lints clean, `resolver: manual` on the judgment, the DSCR record among the records read, every rule P1 to P7 in force, no `legal_name` or `date_of_birth` anywhere in the judgment input, and a chain that verifies.
- Tampering is caught. After sealing APP-H1 and APP-H3, the test rewrites APP-H3's sealed outcome from review to approve: verification fails on that decision with `packet edited`. It then rewrites the stored hash to match the edited packet: verification fails with `chain broken`.
- `under-threshold` finds the stand-in model's unsure answers across 24 applications, and every one it finds was answered by the `endpoint` resolver and reviewed by a person.

**Live** (RUNBOOK.md, Ch 9): the packet for the latest APP-H6 decision on the full stack, signed in and enforced, and both governance queries over every packet sealed in the earlier chapters' runs.

The live run, on 2026-11-10: APP-H6 signed in as `trenn`, who covers it, with enforcement on, the Chapter 6 decision model answering `assess` and `claude-opus-5-5` drafting the rationale. From `results/live/ch9-packet.json`, abridged:

```json
"who": {"user": "user:trenn", "answered_by": "model:endpoint/brightwater-dm@2c8a8bc8874e",
        "reviewed_by": [], "confirmed_by": "user:chris", "configuration": "ch9-audit"},
"procedure": {"name": "brightwater-loan-assessment", "status": "done", "revision": "d005aa1a7fce1d5f"},
"judgment": {"attribution": {"model": "brightwater-dm@2c8a8bc8874e", "resolver": "endpoint", "reviewed": false,
             "confidence": {"risk_tier": 0.99968, "recommendation": 0.999922, "cash_flow_stable": 0.8548}}},
"entitlement": [{"engine": "openfga", "subject": "user:trenn", "action": "can_assess",
                 "object": "application:APP-H6", "allowed": true, "detail": {"ms": 14}}, ...],
"model_calls": [..., {"name": "chat claude-opus-5-5", "ms": 6903.6,
                      "gen_ai.usage.input_tokens": 997, "gen_ai.usage.output_tokens": 598}]
```

`entitlement` has nine rows: OpenFGA's `can_assess` checks and OPA's checks on `get_application`, `get_figures`, `get_related_facilities` and `file_decision`, all allowed. The chain verifies.

The two governance queries ran over the 34 packets sealed by Chapters 6 to 8: `under-threshold` found 15 decisions where the decision model was unsure (APP-H3 to APP-H5), every one answered by the `endpoint` resolver and reviewed; `overridden` found none, because `--auto-confirm` accepts what the model proposed. Answer a review pause yourself and change an answer, and that decision appears in `overridden` with what you changed.

What to look for: a populated `who.user` and `entitlement` from Chapter 8; a `procedure.revision`; `chat` spans with token counts in `model_calls`; on decisions the decision model answered, `answered_by: model:endpoint/<version>` and a confidence per question; and, in `under-threshold`, the share of unsure answers that a person reviewed, which should be all of them.

## Try it

**1. Tamper with a sealed packet.** List the chain, then change the third packet's outcome from review to approve, as anyone with write access to the table could:

```bash
docker compose exec -T postgres psql postgresql://brightwater:brightwater@localhost/brightwater -c \
  "select seq, packet->>'decision_id' as decision_id, packet->'outcome'->>'outcome' as outcome, left(chain_hash,16) as chain from audit.packet order by seq"
docker compose exec -T postgres psql postgresql://brightwater:brightwater@localhost/brightwater -c \
  "update audit.packet set packet = jsonb_set(packet, '{outcome,outcome}', '\"approve\"') where seq = 3"
brightwater audit; echo $?
```

Then cover your tracks: recompute the stored hash so that it matches the edited packet, and verify again.

```bash
python - <<'EOF'
from brightwater import audit, db
with db.session() as conn:
    [[p]] = conn.run("select packet from audit.packet where seq = 3")
    conn.run("update audit.packet set packet_sha256 = :h where seq = 3", h=audit.sha256(audit.canonical(p)))
EOF
brightwater audit; echo $?
```

<details><summary>What happens, and why it matters</summary>

```text
 seq |             decision_id              | outcome |      chain
-----+--------------------------------------+---------+------------------
   1 | 87e0bee2-ef22-433a-ab56-0e60e96e66b5 | decline | 5d4080b124984ca7
   2 | 53bf4326-968d-4ef9-8510-646c9b2612b0 | approve | 57d5c595a93493cb
   3 | 20a0345c-717d-42a8-8418-1e6d51bc2640 | review  | 1c7d1a1e0f0b61d6
   4 | f5f565e0-c334-40e0-82a9-6d48881649ea | review  | a07dc2478c185f1b
UPDATE 1
chain verified: FAILED at seq 3 (20a0345c-717d-42a8-8418-1e6d51bc2640): packet edited
4
```

```text
chain verified: FAILED at seq 3 (20a0345c-717d-42a8-8418-1e6d51bc2640): chain broken
4
```

The first edit is caught because the packet no longer matches its own hash. The second is caught because row 3's chain hash was computed from the original packet hash. To get away with the edit you would have to recompute every chain hash from row 3 onward, and then the head would no longer match the one you wrote down in step 3. Either way, verification names the row and exits 4.

Undo it: run the same `update` with `'"review"'` instead of `'"approve"'`, then the Python snippet again. `brightwater audit` reports `chain verified: 4 packet(s), head a07dc2478c185f1b...` once more, because the original packet produces the original hash.
</details>

**2. Remove the newest packet.** Delete the last row and verify:

```bash
docker compose exec -T postgres psql postgresql://brightwater:brightwater@localhost/brightwater -c "delete from audit.packet where seq = 4"
brightwater audit; echo $?
```

Will verification notice?

<details><summary>What happens, and the limit it shows</summary>

```text
DELETE 1
chain verified: 3 packet(s), head 1c7d1a1e0f0b61d6...
0
```

It passes. Every remaining row is consistent with the rows before it, and nothing inside the table remembers how long the chain used to be. The only sign is the head: `1c7d1a1e...` instead of the `a07dc247...` you wrote down. This is the limit stated under [Sealing](#sealing), and its fix lies outside the database: keep the head somewhere the database owner cannot edit, and compare against it.

Undo it: `BRIGHTWATER_RESULTS=/tmp/bw-check brightwater audit --seal f5f565e0-c334-40e0-82a9-6d48881649ea`, using the decision id from your row 4. The packet is assembled again from the same records, so it gets the same hash, and the head comes back as the one you wrote down, now at sequence 5. Keep the `BRIGHTWATER_RESULTS` prefix: the packet includes the review answers, and step 3 wrote them to `/tmp/bw-check`. Without it the packet comes out with no review answers, so it gets a different hash and a different head, although the chain still verifies.
</details>

**3. Overwrite a rule row after the decision.** In Chapter 5's third exercise, a changed parameter without a new version overwrote the rule row in place. Do that now, after decisions have been sealed: change P3's `approve_line` in `src/brightwater/policy.py` from `"1.25"` to `"1.15"`, keep the version, and sync. Then compare the live rule row with the one sealed in APP-H6's packet:

```bash
brightwater policy sync
docker compose exec -T postgres psql postgresql://brightwater:brightwater@localhost/brightwater \
  -c "select params from compliance.policy_rule where rule_id = 'P3' and version = 1" \
  -c "select r->'params' as sealed_params from audit.packet, jsonb_array_elements(packet->'policy') r where seq = 1 and r->>'rule_id' = 'P3'"
brightwater audit
```

What will each of the three say?

<details><summary>What happens</summary>

```text
                  params
-------------------------------------------
 {"floor": "1.10", "approve_line": "1.15"}

               sealed_params
-------------------------------------------
 {"floor": "1.10", "approve_line": "1.25"}

chain verified: 4 packet(s), head a07dc2478c185f1b...
```

The system of record now says P3v1 has an approve line of 1.15, which was never true while APP-H6 was decided. The packet still says 1.25, because it copied the rule rows when it was sealed, and the chain still verifies, because nothing in the packets changed. That answers the question Chapter 0's first exercise left open: the record of what a decision was made under no longer depends on nobody ever editing the live tables.

Undo it: `git checkout src/ && brightwater policy sync`.
</details>

## What to carry forward

Any decision can now be handed to an examiner as one document, and the document proves it has not changed since the decision was filed. Two of its fields matter for the last chapter: who answered the judgment, down to the model version, and whether a person reviewed or overrode that answer. The people who review the model's unsure answers are producing the best labels the bank has. Chapter 10 feeds them back into the model, on a schedule, under a rule that decides whether the new model is allowed to replace the old one.

**Previous:** [Chapter 8](ch8-identity-and-control-plane.md) · **Next:** [Chapter 10. Close the loop](ch10-close-the-loop.md)
