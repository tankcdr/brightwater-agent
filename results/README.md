# Results

- `runs.jsonl`: one line per run, every configuration, written by `brightwater agent run` and
  `brightwater procedure run`. Scripted runs are not recorded unless `--record` is given.
- `RESULTS.md`: the table, rebuilt by `brightwater results table` from `runs.jsonl` alone.
- `spans/`: the OpenTelemetry spans every number in the table is computed from.
- `review_answers.jsonl`: every person's answer at a review pause (Ch 6); training data for Ch 10.
- `prices.yaml`: list prices used for cost. Local models are costed by measured seconds: multiply
  `model_s` by your machine's cost per second if you want a dollar figure.
- `live/`: outputs saved while following RUNBOOK.md, quoted in the chapter write-ups.
