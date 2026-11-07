# Runbook: the live proofs

Everything in this repo has a test that runs without services or model APIs (`pytest`, using
the scripted driver and a stand-in decision model). The live proofs below are the ones that need
Docker, a model API key, Apple Silicon or a person. Run them in order; each step says what to
save under `results/live/` so the write-ups can quote real output.

Target machine: Apple Silicon Mac with Docker Desktop, [uv](https://docs.astral.sh/uv/),
[Ollama](https://ollama.com), and `ANTHROPIC_API_KEY` in the environment.

## 0. Setup

```bash
uv venv -p 3.14 && source .venv/bin/activate      # AIP 0.7.0 requires Python 3.14
uv pip install -e ".[dev]"
mkdir -p results/live
docker compose up -d --wait                                # Postgres, Neo4j (memory), Cube
docker compose --profile aip up -d --wait --build          # Neo4j (AIP), patched AIP server
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
brightwater procedure export-traces            # writes results/traces.md
```

Then read `results/traces.md` against the procedure: if the live runs did something every time that the
procedure does not (or a recurring mistake the linter misses), report it rather than editing the skill.

Langfuse UI: http://localhost:3000 (reader@brightwater.example / brightwater-local).

## Ch 4. The compiled procedure

Revision 1 (P1 to P6) is kept in `skills/revisions/r1/`. Publish it first so Ch 5 has something to change.

```bash
aip config --server http://localhost:8000 --token brightwater-aip-local
aip publish skills/revisions/r1/brightwater-loan-assessment | tee results/live/ch4-publish-r1.txt
R1=$(aip list | awk '/brightwater-loan-assessment/{print $2}')
brightwater procedure run --handbuilt --repeat 3 --skill brightwater-loan-assessment@$R1 \
    --auto-confirm chris | tee results/live/ch4-runs.txt
brightwater graph query 03 --param app=APP-H4 | tee results/live/ch4-step-orders.json
brightwater results table
```

`--auto-confirm` answers the P6 confirmation as you, so the timing runs are unattended. Run APP-H6
once more without it to answer the confirmation yourself.

## Ch 5. A governed policy change

```bash
aip publish skills/brightwater-loan-assessment | tee results/live/ch5-publish-r2.txt
R2=$(aip list | awk '/brightwater-loan-assessment/{print $2}')
brightwater procedure impact brightwater-loan-assessment@$R1 brightwater-loan-assessment@$R2 | tee results/live/ch5-impact.txt
aip runs | head -20 | tee results/live/ch5-runs-by-revision.txt
```

## Ch 6. The decision model (Apple Silicon)

```bash
brightwater dm dataset
brightwater dm label | tee results/live/ch6-label.txt              # frontier labels, about 1,200 calls
brightwater dm spot-check --n 30                                    # you review 30 labels
brightwater dm export
decision_model/train_mlx.sh train 2>&1 | tee results/live/ch6-train.txt
decision_model/train_mlx.sh serve &                                 # port 8081
brightwater dm calibrate | tee results/live/ch6-calibration.json
brightwater dm evaluate --options A,C,D | tee results/live/ch6-evaluate.json   # add B with TYPESAFE_API_KEY
brightwater procedure run --handbuilt --repeat 3 --configuration ch6-procedure-D \
    --decision-model local:http://localhost:8081/v1 --auto-confirm chris | tee results/live/ch6-runs.txt
```

## Ch 7. Private inference

```bash
ollama pull qwen3:8b && ollama pull all-minilm
export BRIGHTWATER_DRIVER_BASE_URL=http://localhost:11434/v1 BRIGHTWATER_DRIVER_MODEL=qwen3:8b
export BRIGHTWATER_EMBED_URL=http://localhost:11434/v1
unset LANGFUSE_HOST
brightwater netwatch -- brightwater procedure run --handbuilt --repeat 3 --configuration ch7-private \
    --model qwen3:8b --decision-model local:http://localhost:8081/v1 --auto-confirm chris \
    2>&1 | tee results/live/ch7-netwatch.txt
brightwater results table
```

Expect `netwatch: ... 0 external`.

## Ch 8. Identity, entitlements, control plane

```bash
docker compose --profile control up -d --wait
brightwater authz sync && brightwater authz rls-on
brightwater auth login dokafor --password brightwater
aip config --server http://localhost:3100 --token "$(python -c 'import json,pathlib;print(json.loads((pathlib.Path.home()/".brightwater/token.json").read_text())["access_token"])')"
export BRIGHTWATER_ENFORCE=on
brightwater procedure run APP-H1 --model scripted | tee results/live/ch8-covered.txt                # filed
(brightwater procedure run APP-H4 --model scripted; true) 2>&1 | tail -5 | tee results/live/ch8-not-covered.txt   # fails closed
(aip publish skills/brightwater-loan-assessment; true) 2>&1 | tee results/live/ch8-publish-banker.txt   # rejected
brightwater auth login pauthor --password brightwater   # then re-run aip config with the new token
aip publish skills/brightwater-loan-assessment 2>&1 | tee results/live/ch8-publish-author.txt         # accepted
docker compose logs agentgateway --tail 50 | tee results/live/ch8-gateway-log.txt
```

## Finally

```bash
brightwater results table && cp results/RESULTS.md results/live/
```
