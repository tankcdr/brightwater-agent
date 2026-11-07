# Chapter 7. Private inference end to end

**Tag:** `ch7` · **Services:** as Chapter 6, plus Ollama · **Proof:** full procedure runs on the hand-built applicants with every model on the machine, under a witness that strips every API key and reports `0 external`; the same witness catching a deliberate external call.

After Chapter 6 the judgment step runs on our own model, but the rationale is still written by a frontier model over a public API, so every run sends the case's figures off the machine. For a bank that is often the deciding objection to an agent at all. This chapter moves every model in the stack onto the machine, changes no code to do it, and runs the procedure under a witness that records every remote address the run connects to.

## What runs where

| Role | On Apple Silicon (this chapter) | On Linux with NVIDIA (`compose.private.yaml`) | Set by |
|---|---|---|---|
| Driver (the rationale) | `qwen3:8b` on Ollama, `http://localhost:11434/v1` | Qwen3-8B on vLLM | `BRIGHTWATER_DRIVER_BASE_URL`, `BRIGHTWATER_DRIVER_MODEL` |
| Decision model (`assess`) | the Chapter 6 model on `mlx_lm.server`, port 8081 | Qwen2.5-0.5B-Instruct with LoRA adapters on vLLM | `--decision-model local:<url>` |
| Embeddings | `all-minilm` on Ollama (all-MiniLM-L6-v2, 384 dimensions) | all-MiniLM-L6-v2 on Text Embeddings Inference | `BRIGHTWATER_EMBED_URL`, `BRIGHTWATER_EMBED_MODEL` |
| Data and procedure | Postgres, Cube, the AIP server, all on `localhost` | the same services | as before |

**This is a deviation from the series outline**, which assumed vLLM and Text Embeddings Inference on an NVIDIA GPU. The reader's machine is an Apple Silicon Mac, and Docker on a Mac cannot use the GPU, so here the models run natively: Ollama (MIT) for the driver and the embeddings, and the `mlx_lm.server` endpoint from Chapter 6 for the decision model. `compose.private.yaml` still provides the NVIDIA version, described below, for readers who have that hardware.

The models: [Qwen3-8B](https://huggingface.co/Qwen/Qwen3-8B) is Apache-2.0, [all-MiniLM-L6-v2](https://huggingface.co/sentence-transformers/all-MiniLM-L6-v2) is Apache-2.0, and the Chapter 6 decision model is built on Qwen2.5-0.5B-Instruct, also Apache-2.0. Ollama's `all-minilm` tag is the 22M-parameter variant, which is L6-v2.

## No code changes

Every model was already behind a setting:

- **The driver.** `chat_model()` in `src/brightwater/agent.py` uses the Anthropic API by default. When `BRIGHTWATER_DRIVER_BASE_URL` is set, it talks to that OpenAI-compatible server instead, at temperature 0, with `BRIGHTWATER_DRIVER_API_KEY` or the placeholder `local` as the key. Ollama serves exactly that API.
- **Reasoning text.** Qwen3 can think out loud in a `<think>...</think>` block before it answers. `ModelAnswerer` in `src/brightwater/procedure.py` strips those blocks before it looks for the JSON answer, so braces inside the model's reasoning cannot be mistaken for the rationale.
- **The decision model.** `local:<url>` already pointed at our own endpoint in Chapter 6.
- **Embeddings.** `embedder()` in `src/brightwater/graph.py` returns an OpenAI-compatible embeddings client at `BRIGHTWATER_EMBED_URL` (model `all-minilm` by default, 384 dimensions). Without that variable it returns `NoEmbeddings`, a stub that reports 384 dimensions, so the memory graph's vector indexes are sized correctly from Chapter 1 on, and raises if anything ever asks it to embed. Nothing can be embedded by a cloud API by accident. The procedure run itself asks for no embeddings; the setting matters for the memory client, which the Chapter 3 agent uses, so that it can run privately too. Setting it is also what switches on the Chapter 3 agent's memory middleware, which embeds every turn it stores and searches by vector before each model call.

The RUNBOOK also unsets `LANGFUSE_HOST`, so the run's spans go only to the local JSON Lines file the results table is computed from.

## The witness: netwatch

`brightwater netwatch -- <command>` (`src/brightwater/netwatch.py`) runs a command with three guards and one witness.

The guards:

1. **No API keys.** `ANTHROPIC_API_KEY`, `OPENAI_API_KEY`, `TYPESAFE_API_KEY` and `LANGFUSE_SECRET_KEY` are removed from the command's environment. A run that still completes did not need any of them.
2. **A dead proxy.** `HTTPS_PROXY` and `HTTP_PROXY` (both spellings) point at `127.0.0.1:9`, where nothing listens, so any client that honours proxies fails loudly instead of quietly reaching the internet.
3. **Loopback exempt.** `NO_PROXY` covers `localhost`, `127.0.0.1` and `::1`, so the local services still work.

The witness: every 100 ms it lists the open internet sockets of the process and all of its children, using [psutil](https://github.com/giampaolo/psutil) (BSD-3-Clause), and records each remote address. When the command exits it prints every endpoint it saw, marks each one `local` or `EXTERNAL`, and exits with status 3 if any endpoint is not a loopback address. Otherwise it exits with the command's own status.

## What "0 external" proves, and what it does not

It proves that the run completed with no model API key in its environment, that every client honouring the proxy settings had nowhere to go, and that every connection the witness saw from the run's processes was to this machine.

It does not prove that no packet left the machine:

- **Sampling can miss a connection.** A socket that opens and closes between two samples is never seen. This happens in the check below: the stand-in decision model on port 8081 answers every `assess` step, but its connections are short enough that they do not appear in the list. A direct connection that ignores the proxy settings and finishes inside 100 ms would be missed the same way.
- **Only the run's own processes are watched.** Ollama, `mlx_lm.server`, and the services in Docker (Postgres, Cube, the AIP server) are separate processes. The run reaches them on loopback, and what they do on their own network is outside what netwatch sees.
- **Name lookups can go through the system resolver**, which is not one of the run's processes.
- **It says nothing about what was sent** over the local connections, or about where the model weights came from. Pulling the models (`ollama pull`) needs the internet and happens before the witnessed run.

That is why the module calls itself evidence, not proof. Loopback is the only address it accepts as local, so a LAN or Docker-bridge address counts as external: it errs toward false alarms, not toward missed ones.

## The strong form: compose.private.yaml

For Linux with an NVIDIA GPU, `compose.private.yaml` makes the claim by construction instead of by observation. It defines a Docker network, `sealed`, with `internal: true`, which has no route out. On it:

- `vllm-driver` serves Qwen3-8B as `qwen3-8b`;
- `vllm-decision` serves Qwen2.5-0.5B-Instruct with LoRA enabled and the decision model's adapters mounted from `decision_model/artifacts/adapters-peft` as `brightwater-dm`, with `--max-logprobs 10`, matching the 10 alternatives the resolver asks for;
- `tei` serves all-MiniLM-L6-v2 on Text Embeddings Inference;
- `runner` is the agent, on the sealed network only, with every URL pointing at the services above.

Postgres, Cube, both Neo4j instances and the AIP server join `sealed` as well as the default network, so the runner can reach them. The model containers cannot download anything, so the Hugging Face cache volume must be populated beforehand. The adapters must be in PEFT format, which is what TRL or Unsloth produce; MLX-LM adapters from Chapter 6 are a different format and are not converted by anything in this repo.

```bash
docker compose -f compose.yaml -f compose.private.yaml --profile aip --profile private up -d --wait
```

## Do it yourself

> How the chapters fit together, what carries over between them, and fixes for the common errors: [Working through the chapters](working-through-the-chapters.md).

The live run needs an Apple Silicon Mac with Ollama and the Chapter 6 model. The witness itself needs nothing, and steps 1 to 4 were checked with the scripted driver and the stand-in decision model.

**1. Branch from the chapter's tag.**

```bash
git switch -c my-ch7 ch7
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
python -m brightwater.fake_dm --port 8081 &
```

The first four lines bring the fixture, the database, the AIP client and the published procedure to a known state; they take a few seconds and are safe to rerun (publishing the same files again changes nothing). The last line starts the Chapter 6 stand-in decision model on port 8081, in the background of this terminal; if you trained the Chapter 6 model, serve that instead (`decision_model/train_mlx.sh serve &`). If it says `Address already in use`, a decision model from Chapter 6 is still running there, and you can carry on with it. Stop the stand-in when you are done with `pkill -f brightwater.fake_dm`.

No new services: this chapter adds the witness, `src/brightwater/netwatch.py`, and changes no other code.

**2. Witness a scripted run.**

```bash
brightwater netwatch -- brightwater procedure run APP-H3 --model scripted
```

```text
APP-H3 run 1: review   filed           aip 6a0f8052be204293  steps gather>apply-policy>assess>draft-rationale>file>needs-person

netwatch: 3 remote endpoint(s) seen; 0 external
  local     127.0.0.1:4000  (python)
  local     127.0.0.1:5432  (python)
  local     127.0.0.1:8000  (brightwater)
```

Cube, Postgres and the AIP server, all on loopback. The process names tell you which part of the run opened each connection: the step scripts (`python`) read Cube and Postgres, and the driver (`brightwater`) talks to the AIP server. The exit status is the command's own, 0. Because the witness samples, the list can differ between runs: an endpoint may be missing, or credited to the other process. The line that must match is `0 external`.

**3. Add the decision model.**

```bash
BRIGHTWATER_RESULTS=/tmp/bw-check brightwater netwatch -- brightwater procedure run APP-H3 --model scripted \
    --decision-model local:http://127.0.0.1:8081/v1 --auto-confirm test-officer
```

```text
APP-H3 run 1: review   filed           aip 2ac7745264d74b04  steps gather>apply-policy>assess>draft-rationale>file>needs-person

netwatch: 3 remote endpoint(s) seen; 0 external
  local     127.0.0.1:4000  (python)
  local     127.0.0.1:5432  (python)
  local     127.0.0.1:8000  (brightwater)
```

The decision model on port 8081 answered the `assess` step, and it is not in the list. Its connections opened and closed between two samples. That is the sampling gap described above, seen on a real run, and the first exercise below turns it into a test.

**4. Catch a deliberate call out.**

```bash
brightwater netwatch -- python3 -c "import socket,time; s=socket.socket(); s.settimeout(3); print(s.connect_ex(('1.1.1.1',443))); time.sleep(1)"
echo $?
brightwater netwatch -- python -c "import httpx; httpx.get('https://api.anthropic.com')"
echo $?
```

```text
0

netwatch: 1 remote endpoint(s) seen; 1 external
  EXTERNAL  1.1.1.1:443  (python3)
3
httpx.ConnectError: [Errno 111] Connection refused
...
netwatch: 0 remote endpoint(s) seen; 0 external
1
```

The direct socket ignores proxy settings and connected, but it stayed open for a second, so the witness saw it, marked it `EXTERNAL` and exited 3. The `httpx` client honours the proxy settings, so it went to the dead proxy on `127.0.0.1:9` and failed at once; its failure is netwatch's exit status. On a Mac the error reads `[Errno 61] Connection refused`: 61 is macOS's number for the error Linux numbers 111.

**5. The live private run (needs Apple Silicon, Ollama and the Chapter 6 model).**

```bash
mkdir -p results/live
ollama pull qwen3:8b && ollama pull all-minilm
export BRIGHTWATER_DRIVER_BASE_URL=http://localhost:11434/v1 BRIGHTWATER_DRIVER_MODEL=qwen3:8b
export BRIGHTWATER_EMBED_URL=http://localhost:11434/v1
unset LANGFUSE_HOST
brightwater netwatch -- brightwater procedure run --handbuilt --repeat 3 --configuration ch7-private \
    --model qwen3:8b --decision-model local:http://localhost:8081/v1 --auto-confirm chris \
    2>&1 | tee results/live/ch7-netwatch.txt
brightwater results table
```

Ollama must be running (the Ollama app, or `ollama serve` in a second terminal), and port 8081 must be serving the trained Chapter 6 model, not the stand-in from step 1: stop that with `pkill -f brightwater.fake_dm` and start `decision_model/train_mlx.sh serve` in a second terminal, with the environment activated. The `export` lines last for this terminal: before the exercises, run `unset BRIGHTWATER_DRIVER_BASE_URL BRIGHTWATER_DRIVER_MODEL BRIGHTWATER_EMBED_URL`, or the third exercise drives with the local model instead of the default.

Its netwatch summary and the `ch7-private` column are under [The proof](#the-proof).

## The proof

**Verified here** (no CI test covers netwatch yet): a scripted procedure run under netwatch reports `0 external`, with only Cube and Postgres seen; a scripted run with the stand-in decision model and `--decision-model local:http://127.0.0.1:8081/v1` also reports `0 external`, while showing the sampling gap described above; a direct connection to an external address is reported as `EXTERNAL` and netwatch exits 3; a proxy-honouring client fails against the dead proxy.

**Live** (RUNBOOK.md, Ch 7): three runs of each hand-built applicant with the local driver, the Chapter 6 decision model and local embeddings, under netwatch, and the `ch7-private` column of the results table.

The live run, on 2026-11-10 (`results/live/ch7-netwatch.txt` for APP-H1 to H3 and `ch7-netwatch-resume.txt` for APP-H4 to H6; both report 0 external, and this is the second):

```text
netwatch: 6 remote endpoint(s) seen; 0 external
  local     127.0.0.1:11434  (Python)
  local     127.0.0.1:8081  (Python)
  local     ::1:4000  (Python)
  local     ::1:5432  (Python)
  local     ::1:7687  (Python)
  local     ::1:8000  (Python)
```

Ollama, the decision model, Cube, Postgres, the memory graph and the AIP server, and nothing else.

| Metric | ch4-procedure | ch6-procedure-D | ch7-private |
|---|---|---|---|
| Runs | 18 | 18 | 18 |
| Wall clock per decision, s | 14.0 (11.3 to 21.6) | 9.2 (6.9 to 16.8) | 25.0 (19.0 to 97.8) |
| Model time per decision, s | 13.1 (10.3 to 20.7) | 8.5 (6.0 to 16.0) | 23.5 (18.2 to 97.1) |
| Model turns | 2 (2 to 2) | 1 (1 to 1) | 2 (1 to 4) |
| Cost per decision, USD | 0.0355 (0.0314 to 0.0505) | 0.0222 (0.0151 to 0.0270) | n/a |
| Outcome agrees with policy | 18/18 | 18/18 | 15/18 |
| Linter first-pass rate | 18/18 | 18/18 | 3/15 |
| Run-to-run consistency (same steps, same records read) | 6/6 applicants | 6/6 applicants | 6/6 applicants |
| Human pauses | 3 | 12 | 12 |

qwen3:8b filed fifteen of the eighteen runs, all fifteen with the policy's outcome. It did not file APP-H4 in any of its three runs: four drafts in a row each had a sentence with a figure and no citation the linter recognizes (for example `The risk tier is elevated [assessment: risk tier 2].`: a record key has no spaces, so that bracket is not a citation and the sentence fails `figure_uncited`), so the linter rejected them and the run stopped at `draft-rationale` with nothing filed. That is the linter doing its job, and the linter first-pass rate of 3 in 15 is the measure of how far an 8B model is from a frontier model at writing a cited rationale.

What to look for: `netwatch: ... 0 external` with only `localhost` endpoints in the list; outcomes that still agree with the policy on every run, since the policy is a script and the model never decides the outcome; the linter first-pass rate, which shows whether an 8B local model can write a cited rationale as cleanly as a frontier model; and wall time. Cost per decision shows as `n/a` for the local models: they are costed by measured seconds (`model_s`), which you multiply by your machine's cost per second (`results/README.md`).

## Try it

**1. Slip past the witness.** Make the same external connection as in step 4, but close it straight away instead of holding it open:

```bash
brightwater netwatch -- python3 -c "import socket; s=socket.create_connection(('1.1.1.1',443),3); s.close()"
echo $?
```

Will netwatch see it?

<details><summary>What happens, and why it matters</summary>

```text
netwatch: 0 remote endpoint(s) seen; 0 external
0
```

It connected to the internet and the witness reported `0 external` with exit status 0. Run it a few times; in the environment where this was checked it was missed every time. This is the first limit in [What "0 external" proves](#what-0-external-proves-and-what-it-does-not), made concrete: sampling every 100 ms cannot see a connection that lives for less than that, if the client ignores the proxy settings.

So read `0 external` as evidence, and as strong only in combination with the other two guards: there was no key to send, and every well-behaved client was pointed at a dead proxy. When you need the claim to hold by construction, use the sealed network in `compose.private.yaml`, where there is no route out to miss.
</details>

**2. Bring a key and see it taken away.** Set an API key in your shell and look at the environment netwatch gives its command:

```bash
ANTHROPIC_API_KEY=sk-ant-not-a-real-key brightwater netwatch -- \
  python -c "import os; print(os.getenv('ANTHROPIC_API_KEY'), os.getenv('HTTPS_PROXY'), os.getenv('NO_PROXY'))"
```

<details><summary>What happens</summary>

```text
None http://127.0.0.1:9 localhost,127.0.0.1,::1

netwatch: 0 remote endpoint(s) seen; 0 external
```

The key is gone, the proxy points at a port where nothing listens, and loopback is exempt. A run that completes under these conditions did not need a key, which is half of what the witness's verdict rests on.
</details>

**3. Run with the frontier driver under the witness.** Leave the driver at its default, `claude-opus-5-5`, and keep the key in your shell:

```bash
ANTHROPIC_API_KEY=sk-ant-not-a-real-key brightwater netwatch -- brightwater procedure run APP-H3
echo $?
aip runs | head -1
```

Does anything get filed?

<details><summary>What happens</summary>

```text
...
langchain_anthropic.chat_models.AnthropicConnectionError: Connection error.

netwatch: 3 remote endpoint(s) seen; 0 external
  local     127.0.0.1:4000  (python)
  local     127.0.0.1:5432  (python)
  local     127.0.0.1:8000  (brightwater)
1
aec371ff51674fb9  brightwater-loan-assessment@d005aa1a7fce1d5f  paused  2 step(s)  ...
```

The run fails at the first step that needs the model, the `assess` pause after `gather` and `apply-policy`, and exits 1. Nothing is filed, and the AIP run is left `paused` after two steps. A dependency on an outside model shows up as a failure, never as a quiet success, which is the property you want from a private configuration: if `0 external` comes with a filed decision, every model it used was on the machine.
</details>

## What to carry forward

Every model in the stack now runs on hardware the bank controls, the run is witnessed, and the limits of the witness are written down. What the agent still lacks is an identity: it can read any application, and anyone who can reach the AIP server can publish a procedure. Chapter 8 puts a name on every request and decides, in three places, what that name may do.

**Previous:** [Chapter 6](ch6-own-the-decision-model.md) · **Next:** Chapter 8. Identity, entitlements and the control plane
