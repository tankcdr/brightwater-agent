# brightwater-agent

A tutorial repo: a governed small-business loan assessment agent, built up over chapters 0 to 10.
Read `README.md` for the map and `docs/` for each chapter.

## When asked to "run the live proofs"

Follow `RUNBOOK.md` section by section, in order. Save every output it names under `results/live/`.
Stop and report if a step's output does not match what the runbook says to expect; do not edit
code to make a proof pass without saying so.

Steps that need a person (labels spot-check in Ch 6, the P6 confirmation for APP-H6 in Ch 4) are
answered by the user. Ask them.

## Conventions

- Python 3.14 (AIP 0.7.0 requires it). `uv venv -p 3.14 && uv pip install -e ".[dev]"`.
- Tests: `pytest -q`. Tests that need a service skip when it is down; with `docker compose up -d`
  and `--profile aip` running, nothing should skip.
- Definitions live in `semantic/brightwater.ossie.yaml`; thresholds live in `src/brightwater/policy.py`.
  Never put a threshold in the semantic model or a definition in the policy.
- `semantic/cube/model/` is generated: change the Ossie model and run `brightwater semantic build`.
- The skill in `skills/brightwater-loan-assessment/` must pass `aip-spec validate` after any edit.
- Every runtime dependency must be MIT, Apache-2.0, BSD or PostgreSQL licensed; check before adding one.
- No em dashes in docs.
