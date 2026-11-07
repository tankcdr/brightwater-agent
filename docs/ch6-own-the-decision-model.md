# Chapter 6. Own the decision model

**Tag:** `ch6` · **Services:** as Chapter 4, plus `mlx_lm.server` on Apple Silicon (port 8081) · **Proof:** agreement, calibration, stability, pause share and latency for four ways to answer the judgment step, on a held-out split; procedure runs whose AIP history records the model that answered under its own name.

Since Chapter 4 the `assess` step has been answered by the driving model, a frontier model asked three typed questions in a prompt. It answers well, but it costs a frontier call per decision, it gives no probability with its answer, and AIP records the answer as `manual`, exactly as it would record a person's. This chapter trains a small model for that one step, reads a calibrated probability out of it for every option, compares it with the alternatives, and patches AIP so its own history says who answered.

## The step and its questions

`assess` in `skills/brightwater-loan-assessment/SKILL.md` is a `decision` step. Its inputs are figures only (the policy facts, the 12 monthly net cash flows, the monthly debt service, the rule outcome and the rules that fired), and it asks three typed questions:

| Question | Type | Answers | Review threshold |
|---|---|---|---|
| `risk_tier` | score | Low, Moderate, Elevated, High (levels 0 to 3) | confidence below 0.6 |
| `cash_flow_stable` | noul (yes/no) | true or false | probability within 0.2 of 0.5 |
| `recommendation` | choice | `approve`, `approve_with_conditions`, `decline` | confidence below 0.6 |

The criteria for each answer are the assessment rubric, `decision_model/rubric.md`, which `src/brightwater/rubric.py` also implements as code. The recommendation is advice to the reviewer: it never changes `rule_outcome`, which the policy script has already decided.

The thresholds are why probabilities matter. When a decision model answers, AIP compares each answer's confidence with the step's thresholds, and any answer that crosses one pauses the run for review, which the driver routes to a person. A threshold of 0.6 only means something if a confidence of 0.6 is right about 60% of the time. That is what calibration, below, is for.

`decision_model.questions()` reads the three questions from the skill itself, so the model is trained and asked on exactly what the procedure asks.

## The dataset and the labels

`brightwater dm dataset` computes, for every application, the exact state the `assess` step sees, and assigns each application to a split. The split is by application, never by question, so no held-out case can leak into training through a sibling question. A hash of the application id puts about 20% in `test`, 12% in `valid` and the rest in `train`, and every application the fixture marks as held out (APP-H6) is always in `test`. On the default fixture that is 831 training, 129 validation and 246 test applications, out of 1,206.

`brightwater dm label` labels every state against the rubric. The default labeller is `frontier:claude-opus-5-5`: the model is given the rubric prose verbatim and the case (figures only, through `resolver.compact`), and returns the three answers as JSON. That is about 1,200 calls, one per application. `--labeller rubric` labels with the code instead. Either way, every label is compared with the rubric code, and the command reports how many agree, so disagreements between the frontier model and the written rules are counted rather than hidden.

`brightwater dm spot-check --n 30` is the person's step. It shows a random sample of 30 cases with their labels and asks you to accept each one or type the corrected labels as JSON, and your corrections override the label in training. Later, every person's answer at a review pause (kept in `results/review_answers.jsonl`) also overrides the label for that application, which is how Chapter 10 retrains on real reviews.

`brightwater dm export` writes chat-format JSONL to `decision_model/data/mlx/`: one example per application and question, whose user message is the exact prompt the resolver will send and whose assistant message is one letter. Three examples per application.

## Training: LoRA on Apple Silicon

**This is a deviation from the series outline**, which assumed an NVIDIA GPU. The machine this tutorial is built and run on is an Apple Silicon Mac, and Docker on a Mac cannot use its GPU, so the training and serving here use [MLX-LM](https://github.com/ml-explore/mlx-lm) (MIT, version 0.32.2), which runs natively on Apple Silicon. Nothing else in the chapter depends on that choice: the resolver only needs an OpenAI-compatible `/v1/chat/completions` that returns log-probabilities, and on NVIDIA the same JSONL trains with TRL or Unsloth and serves on vLLM.

The base model is [Qwen2.5-0.5B-Instruct](https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct) (Apache-2.0, about 0.49B parameters). The task is small and closed: read a short block of figures, apply a written rubric, answer with one letter. A half-billion-parameter model is enough to learn that and small enough to train in a laptop's unified memory.

`decision_model/train_mlx.sh train` runs `mlx_lm.lora` with LoRA, which freezes the base weights and trains small adapter matrices on 16 layers: batch size 8, 600 iterations, learning rate 1e-4, and `--mask-prompt`, so the loss is computed on the answer letter only and not on the prompt. When training finishes it writes `decision_model/artifacts/model.json`, whose `version` is `brightwater-dm@` followed by the first 12 hex characters of the adapter file's SHA-256. That version is what every decision answered by this model will carry.

## Serving, and a probability per option

`decision_model/train_mlx.sh serve` starts `mlx_lm.server` on port 8081 with the base model and the trained adapters.

`EndpointDecisionClient` in `src/brightwater/resolver.py` is option D, the client AIP calls for the step. For each question it sends one prompt. This is the one for APP-H3's `cash_flow_stable`:

```text
Question (cash_flow_stable): Is monthly cash flow stable enough to rely on?
Options:
A) Every month's net cash flow covers monthly_debt_service, and the standard deviation of the 12 months is at most 25% of their mean.
B) At least one month falls below monthly_debt_service, or the months vary by more than 25% of their mean.
Case: {"dscr":1.17,"group_exposure":216000,"kyc_current":true,"days_since_last_adverse_event":null,"related_facilities":[{"stake_pct":100,"days_past_due":0}],"monthly_net_cash_flow":[4436,4030,4759,3974,3981,4175,4111,4355,3960,3889,4096,4027],"monthly_debt_service":3546,"rule_outcome":"review","rules_fired":["P3"]}
Answer with the letter only.
```

The case carries no application id, no names and no notes. The options are lettered in a fixed order (for a score, A is Low and D is High), and the training examples use the same prompt, so the model learns to answer this exact format.

It asks for one token at temperature 0 with `logprobs` and `top_logprobs: 10`, and reads the log-probabilities of the allowed letters from the first token's alternatives. A letter the server did not return gets a floor below the lowest one it did. The letters' log-probabilities are divided by the question's temperature and passed through a softmax over the allowed letters only, which gives one probability per option. Those become the answer shapes AIP expects: a probability of yes for a noul, and for a choice or a score the chosen value, its confidence and the full distribution.

Reading probabilities from the model's own token distribution, instead of asking a model how confident it is, is the point of option D. The number comes from the same computation that produced the answer.

## Calibration

A fine-tuned model's raw probabilities are usually overconfident. `brightwater dm calibrate` fixes that with temperature scaling (Guo et al., 2017): it collects the raw letter probabilities on the validation split from the served model, then fits one temperature per question that minimizes the negative log-likelihood of the labels (a golden-section search on the log of the temperature, between 0.05 and 20). A temperature above 1 softens the probabilities and one below 1 sharpens them; the model's answer never changes, only how sure it says it is.

The report gives, per question, the temperature, NLL before and after, and expected calibration error (ECE) before and after. ECE puts the answers in ten confidence bins and takes the gap between average confidence and accuracy in each bin, weighted by the bin's size; 0 is perfect calibration. The temperatures go to `decision_model/artifacts/calibration.json`, and the resolver applies them on every call from then on.

Temperature scaling is guaranteed not to raise validation NLL, because it minimizes it. It usually lowers ECE as well, but that is not guaranteed, which is why both are reported.

## Four ways to answer the step

`brightwater dm evaluate` runs each option over the held-out `test` split and compares its answers with the final labels (frontier labels after spot-check corrections and review answers).

| Option | Who answers | Probabilities | AIP records |
|---|---|---|---|
| A | The driving model, at a pause, as in Chapter 4 | none | `manual` |
| B | TypeSafe, the hosted decision model AIP supports natively (`TYPESAFE_API_KEY`) | TypeSafe's | `typesafe` |
| C | A frontier model asked for a probability per option (`FrontierDecisionClient`) | self-reported, uncalibrated | `endpoint` (with the patch below) |
| D | Our fine-tuned model on our endpoint (`EndpointDecisionClient`) | from token log-probabilities, temperature-scaled | `endpoint` (with the patch below) |

For each option it reports agreement per question, ECE per question (none for A, which gives no probabilities), the largest spread in confidence across three repeats of the first 20 cases, the share of decisions that pause (always 1.0 for A, since every decision pauses; for the others, the share where an answer crosses a threshold), and seconds per decision. The results go to `decision_model/artifacts/evaluation.json`. Cost is not in this report: it comes from the procedure runs in the results table, where the driver's tokens are priced from `results/prices.yaml` and local models are measured in seconds (`results/README.md`).

## Closing AIP's attribution gap

Chapter 4 noted that AIP records a decision answered by the driving model as `manual`, the same as a person's, and that the driver wrote `answered_by: agent:<model>` on the decision row to make up for it. With a decision model the gap is worse. AIP 0.7.0 allows exactly two resolver names (`aip.model.steps.RESOLVERS` is `("typesafe", "manual")`), checks every recorded attribution against them on the client and on the server, and writes `typesafe` for any injected decision client. An answer from our own model could only be recorded as TypeSafe's, which is false, or as manual, which is also false.

`aip_resolver/0001-resolver-attribution.patch` is a two-hunk change against upstream AIP (commit `7e63f2fd80acaa3138236b683de5d8b4b834ce35`, version 0.7.0): it adds one resolver name, `endpoint`, and has the client take the resolver name from the decision client's `resolver_name` attribute when it has one. Until the change is upstream, `src/brightwater/aip_patch.py` applies the same two changes at import time, in the driver (`brightwater procedure run`) and in the server (`brightwater aip-server`, which is what the `aip-server` service in `compose.yaml` runs), so the repository does not carry a fork. AIP is Apache-2.0, and the patch is a modification under that licence.

With the patch, the `assess` entry in AIP's own run history carries `resolver: endpoint`, the model version and a confidence for each of the three questions. If an answer crossed a threshold and a person reviewed it, the entry also carries `reviewed: true` and the model's proposed answers. The driver writes the same fact onto the decision row: `answered_by` is `model:<resolver>/<version>`, for example `model:endpoint/brightwater-dm@<digest>`, instead of `agent:<model>`. When a person answered a review pause, the person is who decided, so `answered_by` is `user:<name>`, and the audit packet (Chapter 9) keeps both the model's proposal and what the person changed.

## Do it yourself

> How the chapters fit together, what carries over between them, and fixes for the common errors: [Working through the chapters](working-through-the-chapters.md).

Training and serving the real model need an Apple Silicon Mac, and frontier labels need an Anthropic API key. Everything else runs anywhere, with a stand-in for the trained model, and that is how the steps below were checked. The stand-in's numbers are plumbing, never results.

**1. Branch from the chapter's tag and rebuild the AIP server.**

```bash
git switch -c my-ch6 ch6
docker compose --profile aip up -d --wait --build
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
```

These lines bring the fixture, the database and the AIP client to a known state; they take a few seconds and are safe to rerun. Do not skip `fixture generate` here even if you generated the fixture in an earlier chapter: this chapter's generator gives the generated applicants more varied cash flows, and every number below comes from the new fixture. With the old one, step 6 onwards prints different values (for example a pause share of 0.3821 instead of 0.3415 in the first exercise). The hand-built applicants do not change.

`--build` matters: the `aip-server` service now runs `brightwater aip-server`, which applies the attribution patch described above before it starts. Without the patch the server would refuse an `endpoint` attribution. In the environment where this walkthrough was checked there is no Docker, so the patched server ran on the filesystem backend, started from Python with `brightwater.aip_patch.serve([...])`.

**2. Build the dataset.**

```bash
brightwater dm dataset
```

```text
{'train': 831, 'valid': 129, 'test': 246}
```

One row per application, holding exactly what the `assess` step sees, split by application. APP-H6 is always in `test`.

**3. Label it.**

```bash
brightwater dm label --labeller rubric
```

```text
{'labelled': 1206, 'labeller': 'rubric', 'agrees_with_reference_rubric': 1206}
```

The live run uses the default labeller, `frontier:claude-opus-5-5` (about 1,200 calls, needs an API key), and then `brightwater dm spot-check --n 30`, where you review a sample yourself. Neither was run here. The rubric labeller agrees with the reference on every application by construction; the number worth reading in the live run is how many frontier labels disagree with it.

**4. Export the fine-tuning files.**

```bash
brightwater dm export
head -c 400 decision_model/data/mlx/train.jsonl
```

```text
{'train': 2493, 'valid': 387, 'test': 738}
{"messages": [{"role": "user", "content": "Question (risk_tier): Rate the credit risk; take the highest level any condition reaches. DSCR is facts.dscr, group exposure is facts.group_exposure, and a related facility counts only where owner_stake_pct is 25 or more.\nOptions:\nA) Low: none of the conditions below. ...
```

Three examples per application, one per question. The user message is the exact prompt the resolver will send, and the assistant message is one letter.

**5. Serve a decision model.** On Apple Silicon, train and serve the real one:

```bash
decision_model/train_mlx.sh train
decision_model/train_mlx.sh serve &
```

Anywhere else, start the stand-in on the same port:

```bash
python -m brightwater.fake_dm --port 8081 &
```

Either one serves on port 8081 and keeps running in the background of this terminal while you work through the rest of the chapter. If you see `Address already in use`, a model from an earlier try is still on that port, and you can carry on with it. To stop the stand-in, run `pkill -f brightwater.fake_dm`; to stop the trained model, `pkill -f mlx_lm.server`.

The stand-in speaks the slice of the chat completions API the resolver uses, applies the rubric to the case it reads out of the prompt, and puts most of the probability on the rubric's letter, with seeded noise and about one answer in 17 deliberately wrong. Training was not run here.

**6. Calibrate.**

```bash
brightwater dm calibrate --url http://127.0.0.1:8081/v1
```

```text
{
  "risk_tier": {
    "temperature": 0.7691,
    "nll_before": 0.496,
    "nll_after": 0.4763,
    "ece_before": 0.1417,
    "ece_after": 0.1355,
    "n": 129
  },
  ...
```

These are the stand-in's numbers. Read the shape, not the values: one temperature per question, fitted on the 129 validation applications, with NLL never higher after than before. The temperatures are written to `decision_model/artifacts/calibration.json`, and the resolver applies them from now on.

**7. Evaluate on the held-out split.**

```bash
brightwater dm evaluate --options D --url http://127.0.0.1:8081/v1
```

```text
{
  "D": {
    "n": 246,
    "agreement": {
      "risk_tier": 0.9431,
      "cash_flow_stable": 0.9512,
      "recommendation": 0.9228
    },
    "ece": {
      "risk_tier": 0.164,
      "cash_flow_stable": 0.0541,
      "recommendation": 0.112
    },
    "max_confidence_spread_across_repeats": 0.0,
    "pause_share": 0.2358,
    "seconds_per_decision": 0.004,
    "resolver": "endpoint"
  }
}
```

Again the stand-in, so the values mean nothing about a trained model. Two of the fields are structural, though: the spread across repeats is 0.0 because the same prompt at temperature 0 gives the same distribution, and `pause_share` is the share of held-out decisions in which at least one answer crossed the author's thresholds. Options A and C need a frontier model and B needs `TYPESAFE_API_KEY`, so they were not run here.

**8. Run the procedure with the decision model answering.**

```bash
BRIGHTWATER_RESULTS=/tmp/bw-check brightwater procedure run APP-H1 APP-H3 APP-H6 --model scripted \
    --decision-model local:http://127.0.0.1:8081/v1 --auto-confirm test-officer
docker compose exec -T postgres psql postgresql://brightwater:brightwater@localhost/brightwater -c \
  "select application_id, outcome, status, answered_by from agent.decision order by filed_at"
```

```text
APP-H1 run 1: approve  filed           aip 70ea5425e3d949df  steps gather>apply-policy>assess>draft-rationale>file>needs-person
APP-H3 run 1: review   filed           aip 085e25f21fc249dd  steps gather>apply-policy>assess>draft-rationale>file>needs-person
APP-H6 run 1: decline  filed           aip 4fdb8b8a0ca44516  steps gather>apply-policy>assess>draft-rationale>file>needs-person>confirm-with-person>record-confirmation
 application_id | outcome | status |         answered_by
----------------+---------+--------+------------------------------
 APP-H1         | approve | filed  | model:endpoint/default_model
 APP-H3         | review  | filed  | model:endpoint/default_model
 APP-H6         | decline | filed  | model:endpoint/default_model
```

With no trained model there is no `model.json`, so the version falls back to the served name; `default_model` is the only name mlx-lm 0.32.2 maps to the model it loaded. Now look at what AIP itself recorded for APP-H3's `assess` step (use your run id):

```bash
aip runs 085e25f21fc249dd --json | python -c "import json,sys; d=json.load(sys.stdin); print(json.dumps([h['attribution'] for h in d['history'] if h['step']=='assess'][0], indent=2))"
```

```text
{
  "resolver": "endpoint",
  "model": "default_model",
  "confidence": {
    "risk_tier": 0.994241,
    "cash_flow_stable": 0.9765,
    "recommendation": 0.579349
  },
  "reviewed": true,
  "proposed": {
    "risk_tier": 2,
    "cash_flow_stable": true,
    "recommendation": "approve_with_conditions"
  }
}
```

The recommendation's confidence, 0.579, is under the 0.6 threshold, so the run paused for review and `--auto-confirm` accepted the proposal. AIP's own history says who answered, under its own name, and with what confidence. `--auto-confirm` records its acceptances in `review_answers.jsonl` as `auto:<name>`, not `user:<name>`, so they never become training labels; only a person's answer does. `BRIGHTWATER_RESULTS` keeps the stand-in's review answers out of the real `results/`.

**9. The live run (needs Apple Silicon and an Anthropic API key).** RUNBOOK.md, Ch 6 runs the real sequence and saves each output under `results/live/`:

```bash
mkdir -p results/live
pkill -f brightwater.fake_dm
brightwater dm dataset
brightwater dm label | tee results/live/ch6-label.txt
brightwater dm spot-check --n 30
brightwater dm export
decision_model/train_mlx.sh train 2>&1 | tee results/live/ch6-train.txt
```

`mkdir` creates the folder the outputs go to, and `pkill` stops the stand-in from step 5 so the trained model can have port 8081. `dm label` makes about 1,200 frontier calls, and `spot-check` asks you to review 30 labels. Then, in a second terminal, activate the environment first and start the trained model, and leave it running:

```bash
source .venv/bin/activate
decision_model/train_mlx.sh serve
```

Once it reports that it is serving on port 8081, go back to the first terminal:

```bash
brightwater dm calibrate | tee results/live/ch6-calibration.json
brightwater dm evaluate --options A,C,D | tee results/live/ch6-evaluate.json
brightwater procedure run --handbuilt --repeat 3 --configuration ch6-procedure-D \
    --decision-model local:http://localhost:8081/v1 --auto-confirm chris | tee results/live/ch6-runs.txt
```

Add `B` to `--options` if you have `TYPESAFE_API_KEY`. The live numbers are under [The proof](#the-proof).

## The proof

**Plumbing, verified in CI** (`tests/test_ch6_decision_model.py`, with the stand-in server): APP-H6 is in `test` and no application is split across splits; rubric labels agree with the reference on every application, and the export has three examples per training application, each answered with one letter; temperature scaling does not raise validation NLL for any question, and the temperatures are written; a procedure run with the endpoint resolver has no decision pauses, files `answered_by: model:endpoint/brightwater-dm@test`, and AIP's history records the `assess` step under resolver `endpoint` with a confidence for all three questions; the evaluation harness reports a pause share of 1.0 for option A and zero confidence spread across repeats for option D.

**Live.** The training loss, the calibration report, the A to D comparison and the `ch6-procedure-D` column come only from the live run on Apple Silicon (RUNBOOK.md, Ch 6). None of those numbers come from the stand-in.

The live run, on 2026-11-10, on an Apple Silicon Mac:

- **Labels.** `dm label` labelled all 1,206 applications with `claude-opus-5-5`, and all 1,206 agree with the reference rubric. The 30-case spot-check accepted every label.
- **Training.** Validation loss 0.135 at iteration 200, 0.132 at 400 and 0.108 at 600; model version `brightwater-dm@2c8a8bc8874e`. Served with its adapters, it answers 47 of the first 60 test prompts correctly; the base model without them answers 18.
- **Calibration** (`results/live/ch6-calibration.json`, 129 validation cases):

| Question | Temperature | NLL before | NLL after | ECE before | ECE after |
|---|---|---|---|---|---|
| risk_tier | 0.6059 | 0.4788 | 0.4466 | 0.1046 | 0.0588 |
| cash_flow_stable | 0.9167 | 0.5543 | 0.5535 | 0.0291 | 0.0258 |
| recommendation | 0.7387 | 0.3662 | 0.3551 | 0.0695 | 0.0373 |

- **The comparison** (`results/live/ch6-evaluate.json`, 246 test cases):

| Option | Agreement: risk tier / stable / recommendation | ECE: risk tier / stable / recommendation | Spread across repeats | Pause share | Seconds per decision |
|---|---|---|---|---|---|
| A | 1.0 / 1.0 / 1.0 | none | none | 1.0 | 4.875 |
| C | 1.0 / 1.0 / 1.0 | 0.0191 / 0.0175 / 0.0271 | 0.04 | 0.0041 | 3.013 |
| D | 0.748 / 0.7317 / 0.8293 | 0.0633 / 0.0644 / 0.0519 | 0.0 | 0.3862 | 0.122 |

- **The procedure on option D:**

| Metric | ch4-procedure | ch6-procedure-D |
|---|---|---|
| Runs | 18 | 18 |
| Wall clock per decision, s | 14.0 (11.3 to 21.6) | 9.2 (6.9 to 16.8) |
| Model time per decision, s | 13.1 (10.3 to 20.7) | 8.5 (6.0 to 16.0) |
| Model turns | 2 (2 to 2) | 1 (1 to 1) |
| Cost per decision, USD | 0.0355 (0.0314 to 0.0505) | 0.0222 (0.0151 to 0.0270) |
| Outcome agrees with policy | 18/18 | 18/18 |
| Linter first-pass rate | 18/18 | 18/18 |
| Run-to-run consistency (same steps, same records read) | 6/6 applicants | 6/6 applicants |
| Human pauses | 3 | 12 |

A and C agree with the labels on every case because the same frontier model wrote the labels. D, a half-billion-parameter model, agrees on three answers in four, and it knows when it is unsure: calibration roughly halves its risk-tier and recommendation ECE, and 39% of its decisions cross a threshold and go to a person. That is what the 12 human pauses are: 9 reviews of APP-H3 to APP-H5, where D was unsure whether the cash flow is stable, and APP-H6's 3 confirmations. It answers in about a tenth of a second and costs nothing per call, so the cost per decision that remains is the driver's rationale.

What to look for: whether D's agreement with the labels is close to A's; whether calibration brings D's ECE down, and how C's self-reported confidence compares; D's zero spread across repeats, since the same prompt at temperature 0 gives the same distribution; the pause share, which is the share of decisions a person would review at the author's thresholds; and seconds per decision for a local half-billion-parameter model against a frontier call.

## Try it

These use the stand-in on port 8081, so they show how the pieces behave, not how good a trained model is.

**1. Turn calibration off.** Move the temperatures aside and evaluate again:

```bash
mv decision_model/artifacts/calibration.json /tmp/calibration.json
brightwater dm evaluate --options D --url http://127.0.0.1:8081/v1
```

Which numbers will change: agreement, ECE, pause share?

<details><summary>What happens, and why it matters</summary>

```text
agreement   {"risk_tier": 0.9431, "cash_flow_stable": 0.9512, "recommendation": 0.9228}
ece         {"risk_tier": 0.1918, "cash_flow_stable": 0.0562, "recommendation": 0.1155}
pause_share 0.3415
```

(Those are the relevant fields of the JSON, side by side.) Agreement is identical to step 7, because temperature scaling never changes which letter wins. ECE is worse on every question, and the pause share goes from 0.2358 to 0.3415: without calibration, about one more held-out decision in ten would go to a person. The stand-in is underconfident on the validation set, so its fitted temperatures are below 1 and sharpen it; a fine-tuned model is more often overconfident, and calibration would then soften it and send more cases to review. Either way, the threshold in the skill only means what it says once the confidence has been calibrated.

Undo it: `mv /tmp/calibration.json decision_model/artifacts/calibration.json`.
</details>

**2. Move a threshold.** The thresholds belong to the procedure's author. Raise the recommendation threshold in `skills/brightwater-loan-assessment/SKILL.md` from 0.6 to 0.8 (the `thresholds:` block under `assess`), then evaluate again:

```bash
sed -i.bak 's/      recommendation: 0.6/      recommendation: 0.8/' skills/brightwater-loan-assessment/SKILL.md && rm skills/brightwater-loan-assessment/SKILL.md.bak
brightwater dm evaluate --options D --url http://127.0.0.1:8081/v1
```

<details><summary>What happens</summary>

Agreement is unchanged and `pause_share` rises from 0.2358 to 0.3496. `decision_model.questions()` reads the questions and thresholds from the skill itself, so the evaluation measures exactly what the procedure would do. The threshold is the dial between cost and caution: every point of pause share is a decision a credit officer reads. Because it lives in the skill, changing it is a new revision, with the same review and impact discipline as Chapter 5.

Undo it: `git checkout skills/`.
</details>

**3. Compare the two kinds of answerer.** Run APP-H3 once more without a decision model, so the driver answers the judgment itself (option A), then compare the two runs in AIP's history:

```bash
BRIGHTWATER_RESULTS=/tmp/bw-check brightwater procedure run APP-H3 --model scripted
docker compose exec -T postgres psql postgresql://brightwater:brightwater@localhost/brightwater -c \
  "select answered_by, run_id from agent.decision where application_id = 'APP-H3' order by filed_at"
```

Then print the `assess` attribution for each run id, as in step 8, one line per run:

```bash
for r in $(docker compose exec -T postgres psql postgresql://brightwater:brightwater@localhost/brightwater -tAc \
    "select run_id from agent.decision where application_id = 'APP-H3' order by filed_at"); do
  aip runs $r --json | python -c "import json,sys; d=json.load(sys.stdin); print(d['run_id'], json.dumps([h['attribution'] for h in d['history'] if h['step']=='assess'][0]))"
done
```

<details><summary>What you find</summary>

```text
         answered_by          |      run_id
------------------------------+------------------
 model:endpoint/default_model | 085e25f21fc249dd
 agent:scripted               | 95cd6ab1303d4c97
```

```text
085e25f21fc249dd {"resolver": "endpoint", "model": "default_model", "confidence": {"risk_tier": 0.994241, "cash_flow_stable": 0.9765, "recommendation": 0.579349}, "reviewed": true, "proposed": {...}}
95cd6ab1303d4c97 {"resolver": "manual", "model": null, "confidence": null, "reviewed": false}
```

The driver's answer is recorded by AIP as `manual`, with no model and no confidence, exactly as a person's would be. Only the decision row, which the driver writes, says `agent:scripted`. The decision model's answer carries its resolver, its version and a confidence for each question in AIP's own history. That difference is what Chapter 9's `under-threshold` query is built on: you can only hold an answer to a threshold after the fact if it was recorded with a confidence.
</details>

## What to carry forward

The judgment step is now answered by a model we trained, can version, and can measure, and its confidence means something because it is calibrated against held-out labels. AIP's history and the decision row both say which model answered. The rationale is still written by a frontier model over a public API, though, so the case's figures still leave the building on every run. Chapter 7 moves every model the procedure uses onto the machine and proves that nothing goes out.

**Previous:** [Chapter 5](ch5-governed-policy-change.md) · **Next:** Chapter 7. Private inference end to end
