# Results

- `runs.jsonl`: one line per run, every configuration, written by `brightwater agent run`.
  Scripted runs are not recorded unless `--record` is given.
- `RESULTS.md`: the table, rebuilt by `brightwater results table` from `runs.jsonl` alone.
- `spans/`: the OpenTelemetry spans every number in the table is computed from.
- `prices.yaml`: list prices used for cost.
- `live/`: outputs saved while following RUNBOOK.md, quoted in the chapter write-ups.
