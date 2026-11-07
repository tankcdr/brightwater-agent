# Chapter 10. Close the loop

**Tag:** `ch10` · **Services:** as Chapter 6 (Postgres, Cube, MLX-LM on an Apple Silicon Mac for training and serving) · **Proof:** one full retrain, evaluate, promote-or-reject cycle on new data, recorded in the model registry, with the promoted version on every decision it answers.

Chapter 6 trained the decision model once. A model trained once drifts out of date as the book changes, and the cases it gets wrong are exactly the ones it is least sure of, which Chapter 6 already routes to a person. This chapter closes that loop: every answer a person gives at a review becomes a training label, and once a week a job retrains the model, measures it against the one in production, and promotes it only if it is at least as good. Every cycle, promoted or not, is written down.

## People's answers become labels

When the decision model answers below the author's threshold, the `assess` step pauses for review (Chapter 6) and a person answers. `_record_review` in `src/brightwater/procedure.py` appends one line per review to `results/review_answers.jsonl`:

| Field | Holds |
|---|---|
| `application_id`, `step` | which case, which step |
| `proposed` | the model's answers |
| `flagged` | the questions it was unsure of |
| `final` | the model's answers with the person's changes applied |
| `answered_by` | `user:<username>` |
| `at` | when, in UTC |

These are the most valuable labels the bank has: the cases the model found hardest, answered by the people the policy trusts to decide them.

`final_labels` in `src/brightwater/decision_model.py` builds the training labels in three layers, each overriding the one before: the rubric labels from Chapter 6, then the corrections a person made at the spot check, then every `final` answer from a review that a person (`answered_by` starting with `user:`) gave. `dm.export` writes the fine-tuning files from those labels, split by application exactly as in Chapter 6, so a reviewed case stays in the split it was always in.

## One cycle

`loop.cycle` in `src/brightwater/loop.py` is one retrain, evaluate, promote-or-reject cycle. `brightwater dm cycle` runs it with the MLX trainer and server:

1. **Find what is new.** The current model is the last `promoted` entry in the registry. `new_review_answers` counts the review answers given since that model was created.
2. **Rebuild the training data** with `dm.export`, so the labels include every review answer on file.
3. **Train a candidate** into its own folder, `decision_model/artifacts/candidates/<timestamp>`. `mlx_trainer` runs the same `decision_model/train_mlx.sh` as Chapter 6 with `BRIGHTWATER_DM_ADAPTERS` pointing at that folder, and the candidate's version is `brightwater-dm@` followed by the first 12 hex characters of the SHA-256 of its adapter weights.
4. **Serve and calibrate the candidate first.** `mlx_server` serves the candidate's adapters with `mlx_lm.server`, and the cycle fits its temperatures on the validation split (`dm.calibrate`) before it is compared with anything. An uncalibrated candidate would be judged on confidence numbers it would never ship with.
5. **Evaluate both on the held-out split.** `dm.evaluate` runs the candidate and the current model over the same held-out applications and reports agreement with the labels and expected calibration error (ECE) per question.
6. **Promote or reject**, by the rule below, and append the result to the registry.

## The promotion rule

The cycle compares the mean over the three questions of each measure, candidate against current. The two slacks are constants in `loop.py`:

```text
promote if  candidate agreement >= current agreement - 0.005    (AGREEMENT_SLACK: half a point)
       and  candidate ECE       <= current ECE       + 0.02     (ECE_SLACK: two points)
```

Both must hold. The slack exists because two models evaluated on a few hundred cases will differ by noise, and a rule with no slack would reject a candidate that is as good but not identical. The slack is deliberately small: half a point of agreement, two points of ECE. Before the first cycle, production is the model Chapter 6 trained, which has no registry entry yet. The first cycle registers it (`Chapter 6 model, registered at the first cycle`) from `decision_model/artifacts/model.json`, so the first candidate has something to beat. Only when there is no model at all is a candidate promoted unopposed, with the reason `first model`. Candidate and current are each served by their own `mlx_lm.server` on a free port and stopped after the evaluation.

Training writes the candidate's own `model.json` inside its folder, never over production's. On promotion, the candidate's folder is copied over `decision_model/artifacts/adapters`, its calibration over `decision_model/artifacts/calibration.json`, and `decision_model/artifacts/model.json` is updated with its version. On rejection none of the live adapters, calibration or model metadata are touched, the registry's current model is still the previous one, and the entry records why, with the numbers, for example `agreement 0.6778 < 0.9222 - 0.005`.

## The registry

`decision_model/registry.jsonl` gets one line per cycle: `version`, `created_at`, `review_answers_used`, `training_rows` per split, the candidate's `agreement` and `ece`, the current model it was measured `against` (version, agreement, ECE), `status` (`promoted` or `rejected`) and `reason`.

It is the record of which model was in production when. The version string it holds is the same one that reaches every decision the model answers. When the decision client answers, the driver writes `answered_by: model:endpoint/<version>` on the decision row, and the AIP attribution records `model: <version>`. Both land in the Chapter 9 packet, as `who.answered_by` and `judgment.attribution.model`. So an examiner holding a packet can go from a decision to the exact model that answered it, and from there to the registry line that says when it was promoted, what it was trained on, and how it measured against the model it replaced.

## Scheduling

On the Mac, `ops/launchd/com.longrunadvisory.brightwater.dm-cycle.plist` runs `brightwater dm cycle` every Sunday at 02:30 and appends its output to `results/dm-cycle.log`. Replace `REPO_PATH` in the file with the repo's path, then:

```bash
cp ops/launchd/com.longrunadvisory.brightwater.dm-cycle.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.longrunadvisory.brightwater.dm-cycle.plist
```

On Linux the same schedule is one crontab line:

```text
30 2 * * 0  cd /path/to/brightwater-agent && .venv/bin/brightwater dm cycle >> results/dm-cycle.log 2>&1
```

`brightwater dm cycle` wires in the MLX trainer and server, which need Apple Silicon. `loop.cycle` takes the train and serve functions as arguments, so on an NVIDIA machine you pass ones that train with TRL or Unsloth and serve on vLLM (the options `train_mlx.sh` names); the resolver only needs an OpenAI-compatible endpoint with log-probabilities.

## Do it yourself

> How the chapters fit together, what carries over between them, and fixes for the common errors: [Working through the chapters](working-through-the-chapters.md).

A real cycle trains with MLX-LM, so it needs an Apple Silicon Mac. Everything else about the loop can be run anywhere: the promotion rule, the registry, the labels and the version on each decision. The steps below do that with training stubbed and two copies of the Chapter 6 stand-in playing the candidates, the same way `tests/test_ch10_loop.py` does. None of the numbers are results.

**1. Branch from the chapter's tag and prepare the labels.**

```bash
git switch -c my-ch10 ch10
docker compose --profile aip up -d --wait
source .venv/bin/activate
uv pip install -e ".[dev]"
```

The install picks up any dependencies this chapter adds. Every `brightwater` command runs inside the virtual environment: in a new terminal, run `source .venv/bin/activate` from the repo first, or put `uv run` in front of the command.

Then, but first: if you trained a model in Chapter 6, leave out the `dm dataset && dm label` line below, because it would replace your frontier labels with the rubric's, and keep its `decision_model/artifacts/`: the first cycle will register that model and compare the candidate with it.

```bash
brightwater fixture generate
brightwater db init && brightwater db load && brightwater policy sync
aip config --server http://localhost:8000 --token brightwater-aip-local
aip publish skills/brightwater-loan-assessment
brightwater dm dataset && brightwater dm label --labeller rubric
python -m brightwater.fake_dm --port 8081 &
```

The first four lines bring the fixture, the database, the AIP client and the published procedure to a known state; they take a few seconds and are safe to rerun. The last line starts the Chapter 6 stand-in decision model in the background of this terminal; if it says `Address already in use`, it is still running from an earlier chapter, and you can carry on. Stop it when you are done with `pkill -f brightwater.fake_dm`.

**2. The real cycle (needs Apple Silicon).**

```bash
brightwater dm cycle
cat decision_model/registry.jsonl
```

Without MLX-LM the cycle stops at training, with `ModuleNotFoundError: No module named 'mlx'` from `train_mlx.sh`, and nothing is registered; it leaves an empty folder under `decision_model/artifacts/candidates/`, which you can delete. `--limit N` evaluates on the first N held-out applications only, which is useful for a quick check.

**3. Run a cycle with the trainer stubbed.** Save this as `/tmp/stub_cycle.py`. It is `loop.cycle` with two stand-in servers in place of `mlx_trainer` and `mlx_server`: a good candidate, which answers about one prompt in 17 wrongly, and a worse one, which answers one in three wrongly.

```python
import json, sys, threading
from brightwater import decision_model as dm, fake_dm, loop, resolver
quality = sys.argv[1]                       # good or worse
urls = {}
for port, (name, wrong) in zip((18091, 18092), (("good", 17), ("worse", 3))):
    srv = fake_dm.serve(port, wrong_every=wrong)      # wrong_every=3: one answer in three is wrong
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    urls[name] = f"http://127.0.0.1:{port}/v1"

def train(candidate_dir):                   # stands in for mlx_trainer
    (candidate_dir / "adapters.safetensors").write_bytes(b"stub")
    return f"brightwater-dm@{candidate_dir.name}-{quality}"

def serve(version, adapters):               # stands in for mlx_server
    cal = dm.ART / "calibration.json"
    temps = json.loads(cal.read_text())["temperature"] if cal.exists() else {}
    url = urls["worse" if version.endswith("worse") else "good"]
    return resolver.EndpointDecisionClient(url, "fake", version=version, calibration=temps)

print(json.dumps(loop.cycle(train, serve, limit=60), indent=1))
```

```bash
python /tmp/stub_cycle.py good
```

```text
{
 "version": "brightwater-dm@20261107T190251-good",
 "created_at": "2026-11-08T00:02:52Z",
 "review_answers_used": 0,
 "training_rows": {
  "train": 2493,
  "valid": 387,
  "test": 738
 },
 "agreement": 0.9222,
 "ece": 0.1356,
 "status": "promoted",
 "reason": "first model"
}
```

There was no model in production, so the candidate is promoted unopposed, with the reason `first model`. Every step of a real cycle still ran: the export, the candidate's own folder, calibration on the validation split, evaluation on 60 held-out applications, the promotion, and a line in `decision_model/registry.jsonl`. Your version carries your own timestamp. If you kept a Chapter 6 model, or have run this step before, there is already a model in production, so the candidate is compared with it and the reason is not `first model`.

**4. See the version on a decision.**

```bash
cat decision_model/artifacts/model.json
BRIGHTWATER_RESULTS=/tmp/bw-check brightwater procedure run APP-H1 --model scripted \
    --decision-model local:http://127.0.0.1:8081/v1 --auto-confirm officer-7
docker compose exec -T postgres psql postgresql://brightwater:brightwater@localhost/brightwater -tAc \
  "select answered_by from agent.decision order by filed_at desc limit 1"
```

```text
{
  "version": "brightwater-dm@20261107T190251-good",
  "served_name": "default_model"
}
APP-H1 run 1: approve  filed           aip a4493f8d30c1430b  steps gather>apply-policy>assess>draft-rationale>file>needs-person
model:endpoint/brightwater-dm@20261107T190251-good
```

Promotion updated `model.json`, the resolver reads its version, and the decision row carries it. The same string is in the registry line from step 3 and, through Chapter 9, in the decision's sealed packet. That is the path from a decision to the model that answered it, to how that model was trained and measured.

**5. Schedule it.** The launchd and cron set-ups are in [Scheduling](#scheduling) above. They were not installed here.

## The proof

**Verified here** (`tests/test_ch10_loop.py`, with training stubbed and the stand-in decision model playing two candidates): the first cycle promotes with the reason `first model`; a second cycle with a candidate that answers one prompt in three wrongly is rejected, with `agreement` in the reason; the current model is still the first one; the registry holds exactly two lines, `promoted` then `rejected`; the Chapter 6 model is registered once before a first cycle; automatic review answers are not training labels; and a procedure run answered by the promoted version files a decision with `answered_by: model:endpoint/<that version>`.

**Live** (RUNBOOK.md, Ch 10): a real cycle on the Mac. The training data now includes every review answer given during the earlier chapters' runs; MLX-LM trains a new adapter on Qwen2.5-0.5B-Instruct, the cycle calibrates it, evaluates it on the held-out split against the registry's current model, and promotes or rejects it. The first cycle registers the Chapter 6 model and compares the candidate with it. Only review answers given by a person (`user:<name>`) are training labels; reviews accepted by `--auto-confirm` are recorded as `auto:<name>` and left out.

The live run, on 2026-11-10 (`results/live/ch10-cycle.json` and `results/live/ch10-registry.jsonl`):

```json
{"version": "brightwater-dm@2c8a8bc8874e", "created_at": "2026-11-10T22:30:51Z", "status": "promoted", "reason": "Chapter 6 model, registered at the first cycle"}
{"version": "brightwater-dm@2c8a8bc8874e", "created_at": "2026-11-10T23:42:25Z", "review_answers_used": 0, "training_rows": {"train": 2493, "valid": 387, "test": 738}, "agreement": 0.7697, "ece": 0.0599, "against": {"version": "brightwater-dm@2c8a8bc8874e", "agreement": 0.7697, "ece": 0.0599}, "status": "promoted", "reason": "agreement and calibration held"}
```

Every review in the earlier chapters' runs was accepted by `--auto-confirm`, so `review_answers_used` is 0 and the training data was the Chapter 6 data. With the same data and the same settings, MLX-LM trained the same adapter byte for byte, so the candidate has the same version, agreement and ECE, and it is promoted because it held both. Answer a few review pauses yourself (Chapter 6, without `--auto-confirm`) before the next cycle and the candidate differs.

What to look for: `review_answers_used`, which says how many person answers this cycle learned from; the candidate's agreement and ECE beside the current model's; the decision and its reason. Either outcome is a pass for this chapter. A promotion shows the loop improving the model; a rejection shows the rule protecting production from a model that did not earn its place, which is the more important of the two.

## Try it

**1. Feed it a worse candidate.**

```bash
python /tmp/stub_cycle.py worse
```

The worse candidate's ECE turns out to be 0.147. The current model's is 0.1356. Will the calibration condition hold? Will the candidate be promoted?

<details><summary>What happens, and why it matters</summary>

```text
{
 "version": "brightwater-dm@20261107T190257-worse",
 ...
 "agreement": 0.6778,
 "ece": 0.147,
 "against": {
  "version": "brightwater-dm@20261107T190251-good",
  "agreement": 0.9222,
  "ece": 0.1356
 },
 "status": "rejected",
 "reason": "agreement 0.6778 < 0.9222 - 0.005"
}
```

The calibration condition holds: 0.147 is within two points of 0.1356. The candidate is still rejected, because it agrees with the labels on far fewer cases, and both conditions have to hold. Notice that a calibrated bad model can look well behaved: it is honestly unsure, so its confidence matches its accuracy. Calibration says whether to trust the confidence; agreement says whether to trust the answer. The rule checks both.

`cat decision_model/artifacts/model.json` still names the good version, and the registry now has two lines, `promoted` then `rejected`, with the reason written down.
</details>

**2. Loosen the rule.** Change `AGREEMENT_SLACK` in `src/brightwater/loop.py` from half a point to a quarter of the whole scale, and feed the worse candidate again:

```bash
sed -i.bak 's/^AGREEMENT_SLACK = 0.005/AGREEMENT_SLACK = 0.25/' src/brightwater/loop.py && rm src/brightwater/loop.py.bak
python /tmp/stub_cycle.py worse
cat decision_model/artifacts/model.json
```

<details><summary>What happens</summary>

```text
 "status": "promoted",
 "reason": "agreement and calibration held"
```

and `model.json` now names the `-worse` version, so every decision from here on is answered by a model that gets one prompt in three wrong. Nothing else stopped it. The promotion rule is two constants and two comparisons, and it is the only thing between a retrained model and production. That is why the constants are in code, under review like the lending policy, and not in a config file someone can change at 2:30 on a Sunday.

Undo it: `git checkout src/`, then start the registry again with `rm -rf decision_model/registry.jsonl decision_model/artifacts` and `python /tmp/stub_cycle.py good`. If you trained a model in Chapter 6, that `rm` deletes it too, so move `decision_model/artifacts` somewhere else first if you want to keep it.
</details>

**3. A person's answer and an automatic one.** Add two review answers, given after the current model was promoted: one by a person who changed APP-H3's recommendation, one accepted by `--auto-confirm`. Then run a cycle:

```bash
AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
cat >> results/review_answers.jsonl <<EOF
{"application_id": "APP-H3", "step": "assess", "proposed": {"recommendation": "approve_with_conditions"}, "flagged": ["recommendation"], "final": {"recommendation": "decline"}, "answered_by": "user:dokafor", "at": "$AT"}
{"application_id": "APP-H5", "step": "assess", "proposed": {"risk_tier": 2}, "flagged": ["risk_tier"], "final": {"risk_tier": 2}, "answered_by": "auto:chris", "at": "$AT"}
EOF
python /tmp/stub_cycle.py good
python -c "from brightwater import decision_model as dm; print(dm.final_labels()['APP-H3'])"
```

What will `review_answers_used` be? What is APP-H3's training label now?

<details><summary>What happens</summary>

```text
 "review_answers_used": 1,
 ...
 "status": "promoted",
 "reason": "agreement and calibration held"
{'risk_tier': 2, 'cash_flow_stable': True, 'recommendation': 'decline'}
```

One answer counted: the person's. The automatic acceptance is recorded, so the review is not lost, but it is not a label, because nobody judged anything. The person's answer overrides the rubric's label for APP-H3, so the next model is trained on what the credit officer decided. This is the loop the chapter is named for: the cases the model was least sure of come back, answered by the people the policy trusts, as the labels it learns from next.

Undo it by deleting the two lines you added, which are the last two in the file:

```bash
python -c "p='results/review_answers.jsonl'; l=open(p).readlines(); open(p,'w').writelines(l[:-2])"
```

If the file held nothing before this exercise, `rm results/review_answers.jsonl` does the same; if it holds review answers from your earlier chapters, removing it deletes those labels too.
</details>

## What the ten chapters add up to

Follow one decision, APP-H6, through everything that was built:

- Its facts live in five **systems of record** and its rules are **policy as code** (Ch 0). The **ontology and memory graph** find the businesses its owners control (Ch 1), and the **semantic layer** computes its DSCR under one governed, versioned definition (Ch 2).
- An agent with instructions and tools could assess it, recorded in full, but took a different path every time (Ch 3). Those recordings were **compiled into a procedure** that takes the same steps and reads the same records on every run (Ch 4), and that procedure changes only through **governed revisions** whose blast radius is measured before they ship (Ch 5).
- Its judgment step is answered by a **decision model the bank owns**, with calibrated confidence, and sent to a person when it is unsure (Ch 6). The whole run can happen with **no external connection** at all (Ch 7), on behalf of a **signed-in banker who is entitled** to see the application, through a control plane that fails closed (Ch 8).
- Its decision is reconstructed from one id into a **sealed, tamper-evident audit packet** (Ch 9), and the person's answers at review make next week's model better, under a promotion rule that keeps a worse model out of production (Ch 10).

None of the layers is exotic, and each was added because the one before it left a question it could not answer: is the figure right, who is connected, is the path repeatable, what changes when the rules change, who answered and how sure, did anything leave the building, was this person allowed, can you prove it, and does it get better. Each chapter's proof is a test in this repo, and each live result is in `results/live/`, so every claim in the series can be rerun rather than taken on trust.

**Previous:** [Chapter 9](ch9-audit-packet.md) · **Back to:** [the README](../README.md)
