# Runbook: the live proofs

Everything in this repo has a test that runs without services or model APIs (`pytest`, using
the scripted driver). The live proofs below are the ones that need
Docker or a model API key. Run them in order; each step says what to
save under `results/live/` so the write-ups can quote real output.

Target machine: any machine with Docker, [uv](https://docs.astral.sh/uv/),
and `ANTHROPIC_API_KEY` in the environment.

## 0. Setup

```bash
uv venv && source .venv/bin/activate
uv pip install -e ".[dev]"
mkdir -p results/live
docker compose up -d --wait                                # Postgres, Neo4j (memory), Cube
brightwater fixture generate && brightwater db init && brightwater db load && brightwater policy sync
brightwater semantic build
pytest -q 2>&1 | tee results/live/pytest.txt        # expect 0 failed, 0 skipped (Neo4j is up now)
```

## Ch 1. The memory graph

```bash
brightwater graph load | tee results/live/ch1-load.txt
brightwater graph query 01 --param app=APP-H4 | tee results/live/ch1-query01.json
brightwater fixture break && (brightwater graph load --from fixture/broken; true) | tee results/live/ch1-broken.txt
```

Expect: query 01 returns Northpine, Priya Lindqvist (40%), Ridgeback Tile Co (35%), FAC-H90-1, 80 days.

## Ch 3. The free-form baseline

```bash
docker compose -p langfuse -f docker/langfuse/docker-compose.yml --env-file docker/langfuse/langfuse.env up -d --wait
export LANGFUSE_HOST=http://localhost:3000 LANGFUSE_PUBLIC_KEY=pk-lf-brightwater-local LANGFUSE_SECRET_KEY=sk-lf-brightwater-local
brightwater agent run --handbuilt --repeat 3 | tee results/live/ch3-runs.txt     # claude-opus-5-5 by default
brightwater graph query 03 --param app=APP-H4 | tee results/live/ch3-step-orders.json
brightwater results table
```

Langfuse UI: http://localhost:3000 (reader@brightwater.example / brightwater-local).

## Finally

```bash
brightwater results table && cp results/RESULTS.md results/live/
```
