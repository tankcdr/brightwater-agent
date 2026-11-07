# Brightwater: a governed AI loan agent, built one layer at a time

Most agent demos show that a model can reach the right answer. In regulated work that is the easy part. A bank also has to show that the decision was reached the right way: from figures everyone agrees on, under the policy in force that day, by someone allowed to make it, with a record an examiner can replay years later. A model on its own gives you none of that.

This repository builds a small-business loan assessment agent that meets that bar. It starts with the bank's data and its lending policy as plain code, and adds one layer of an open source stack per chapter: an ontology and memory graph, a semantic layer, a compiled procedure, a decision model you train and own, private inference, identity and entitlements, an audit packet, and a feedback loop. Every chapter ends with a proof you can rerun, so each layer has to earn its place with evidence rather than a feature list.

## Who this is for

Engineers and architects putting agents into work where decisions have to be explained afterwards: lending, insurance, claims, compliance, anything with an examiner or an auditor at the end of it. You should be comfortable with Python, SQL and Docker. You do not need a background in machine learning or banking; the chapters explain both as far as the code needs them.

## The tools, and why each one is here

Each tool arrives in the chapter that needs it, to fix something the chapter before could not prove. Everything runs on your machine. The only outside calls are to the Anthropic API, from Chapter 3, and Chapter 7 runs the whole decision without them.

| Tool | From | Why it is here |
|---|---|---|
| Postgres 16 | Ch 0 | Holds the five systems of record (ledger, credit, compliance, registry, CRM), each in its own schema, the way they are separate systems at a real bank. |
| Policy as plain Python | Ch 0 | The referee. Lending rules P1 to P7 are code with tests, so a decision's outcome never depends on a model. |
| LinkML and SHACL (pySHACL) | Ch 1 | LinkML defines the entities once, whichever system holds them. SHACL checks that the systems agree with each other before anything loads, so bad data fails at the gate. |
| Neo4j and neo4j-agent-memory | Ch 1 | The memory graph: who owns what, across systems. It is how the agent sees that a co-owner's other business is 80 days late. |
| Apache Ossie | Ch 2 | One file defines every figure (DSCR, group exposure), so a definition changes in one place, not in every query that uses it. |
| Cube Core | Ch 2 | Serves those definitions over an API. The policy engine and the agent's tools both read their figures from it, so they cannot disagree. |
| LangChain `create_agent` (LangGraph) with Claude Opus | Ch 3 | The free-form agent: a frontier model with tools, left to choose its own steps. It is the baseline every later chapter is measured against. |
| OpenTelemetry, with Langfuse optional | Ch 3 | Records every model and tool call with its tokens and timing. The cost and consistency numbers come from these records. |
| AIP (Agent Instruction Protocol) 0.7.0 | Ch 4 | Compiles the agent's runs into a fixed procedure: the steps and their order are set, and the model only judges and writes. Each published revision is kept, which is how Chapter 5 shows what the old rules were. |
| MLX-LM with Qwen2.5-0.5B-Instruct | Ch 6 | Fine-tunes and serves a small decision model you own, on a Mac. Its answers come with probabilities you can calibrate, so it can say when it is unsure and hand the case to a person. |
| Ollama (qwen3:8b, all-minilm) | Ch 7 | Runs the driver model and the embeddings locally, so no data leaves the machine, and `brightwater netwatch` proves it. On Linux, vLLM and TEI do the same job. |
| Keycloak | Ch 8 | Signs bankers in and issues the tokens every later check reads. |
| OpenFGA | Ch 8 | Decides whether this banker covers this application. |
| OPA | Ch 8 | Decides what a role may do: which tools a banker may call, and that only a procedure author may publish. |
| agentgateway | Ch 8 | Sits in front of the AIP server, checks every call against Keycloak's token and OPA's decision, and logs who asked. |
| Postgres row-level security | Ch 8 | The last line of defence: even a bug in a layer above cannot read another banker's rows. |
| pgaudit and a hash-chained audit table | Ch 9 | pgaudit, loaded since Chapter 0, logs every read. Each decision is sealed as a packet whose hash chains to the one before, so any later edit is detectable. |
| A model registry, scheduled with launchd or cron | Ch 10 | Retrains on the answers people gave at review, promotes the new model only if it is no worse on agreement and calibration, and records every version. |

## The scenario

Brightwater Community Bank (fictional) assesses small-business loan applications. In the hard cases the deciding fact sits across systems: a co-owner's other business is 80 days late on a loan, or the applicant's exposure is under the cap until you count the businesses its owners control. An agent that reads one table misses these. The stack in this repo finds them, explains them, and proves it did so under a governed definition.

All names, businesses and figures are invented.

## What each chapter answers

| Tag | Chapter | The question it answers |
|---|---|---|
| `ch0` | [Systems of record and policy as code](docs/ch0-systems-of-record.md) | Before anything smart touches a decision, what is the referee? |
| `ch1` | [Ontology and the memory graph](docs/ch1-ontology-and-graph.md) | How do you know your systems agree with each other, and how does an agent see across them? |
| `ch2` | [Semantic layer](docs/ch2-semantic-layer.md) | When someone changes what "DSCR" means, what moves, and can you still prove the numbers? |
| `ch3` | [Free-form agent, fully recorded](docs/ch3-free-form-agent.md) | Left to itself, what does a capable agent cost, and how consistent is it? |
| `ch4` | [Compile the runs into an AIP procedure](docs/ch4-compiled-procedure.md) | What changes when the procedure is fixed and the model only judges and writes? |
| `ch5` | A governed policy change (coming) | When the policy changes, who is affected, and can you prove what the old rules were? |
| `ch6` | Own the decision model (coming) | Can a small model you own answer the judgment step, and does it know when it is unsure? |
| `ch7` | Private inference end to end (coming) | Can the whole thing run without data leaving the machine, and how would you prove it? |
| `ch8` | Identity, entitlements and the control plane (coming) | Who is allowed to make this decision, and what stops everyone else? |
| `ch9` | The audit packet (coming) | Given only a decision id, can you reconstruct everything, and tell if anyone changed it? |
| `ch10` | Close the loop (coming) | How does the model learn from human reviews without quietly getting worse? |

## What you end up with

```text
  banker ── signs in (Keycloak) ──▶ gateway: entitled? allowed? (OpenFGA, OPA) ─────────── Ch 8
                                        │
                     compiled procedure (AIP): gather ▶ policy ▶ assess ▶ write ▶ file ▶ person   Ch 4, 5
                        │                  │                     │
                  policy engine     decision model           driver model                Ch 0, 6, 3, 7
                  (plain code)      (yours, calibrated)      (frontier or local)
                        │
                  governed figures: one definition per number (Ossie, Cube) ─────────────── Ch 2
                        │
                  memory graph: who owns what, checked before it loads (LinkML, SHACL, Neo4j)  Ch 1
                        │
                  five systems of record: ledger, credit, compliance, registry, CRM (Postgres)  Ch 0

  every decision ──▶ sealed, hash-chained audit packet ─────────────────────────────────── Ch 9
  every human review ──▶ retrain ▶ evaluate ▶ promote or reject ────────────────────────── Ch 10
```

## How to work through it

There are three ways in, depending on how much time you have.

**Read it.** Each chapter document stands on its own: what the layer is, why it is there, the code that implements it, and the evidence it works. Start at [Chapter 0](docs/ch0-systems-of-record.md) and go in order; each one ends with what the next one fixes.

**Build along with it (recommended).** Each chapter has a git tag that is the repository exactly as it stands at the end of that chapter. Start a branch of your own from the tag, follow the chapter's **Do it yourself** section, then work through its **Try it** exercises. The exercises are where most of the learning is: each one changes or breaks something and asks you to predict what happens before you look. To see precisely what a chapter added, diff its tag against the previous one:

```bash
git switch -c my-ch0 ch0
git diff ch0 ch1
git switch main
```

The first line makes a branch of your own, starting where Chapter 0 ends. The second shows everything Chapter 1 adds. The third takes you back to the finished system.

Starting each chapter on a branch of your own keeps any changes you make during the exercises, and leaves the chapter tags untouched.

How the chapters fit together, what carries over from one to the next, and how to fix the errors people usually hit are all in [Working through the chapters](docs/working-through-the-chapters.md). Read it once before you start.

**Reproduce the published numbers.** [RUNBOOK.md](RUNBOOK.md) runs every chapter's live proof in order and saves the outputs to `results/live/`. It is written so that Claude Code can run it for you ([CLAUDE.md](CLAUDE.md) tells it how), but it is plain shell and you can run it yourself.

## Start here

You need Docker and [uv](https://docs.astral.sh/uv/). uv installs the Python each chapter needs (3.12 is fine for Chapters 0 to 3; Chapter 4 onward needs 3.14, because AIP 0.7.0 does). Later chapters need more, and each chapter's header says what:

| From | You also need |
|---|---|
| Ch 3 | an Anthropic API key (`ANTHROPIC_API_KEY`); Langfuse is optional |

Every chapter's tests also run with no API key at all, using a scripted driver; that is what CI runs. The live runs are what produce the numbers.

```bash
git clone https://github.com/tankcdr/brightwater-agent.git && cd brightwater-agent
git switch -c my-ch0 ch0
uv venv -p 3.12
source .venv/bin/activate
uv pip install -e ".[dev]"
brightwater --help
```

If the last line prints the command list, you're set. Every `brightwater` command in the chapters runs inside this environment. In a new terminal, run `source .venv/bin/activate` from the repo first, or put `uv run` in front of the command. Chapter 4 recreates the environment with Python 3.14, and its first step shows how.

If a command fails, check the table at the end of [Working through the chapters](docs/working-through-the-chapters.md) first.

Then open [Chapter 0](docs/ch0-systems-of-record.md) and start at **Do it yourself**.

## Services by chapter

Services are added through Compose profiles, so earlier chapters never pay for later ones:

| Profile | Adds | From |
|---|---|---|
| (default) | Postgres 16 with pgvector and pgaudit; Neo4j Community (memory graph) from Ch 1; Cube Core from Ch 2 | Ch 0 |
| `aip` | a second Neo4j for AIP's run history, and the AIP server | Ch 4 |

Langfuse (Ch 3, optional) runs from its own vendored compose file in `docker/langfuse/`.

## Layout

```
compose.yaml           Postgres (pgvector, pgaudit), Neo4j, Cube; profiles aip and control
fixture/handbuilt.yaml the six hand-built applicants and their expected outcomes
schema/postgres/       the five systems of record, and row-level security policies (enabled in Ch 8)
schema/linkml/         the entity schema
schema/shacl/          cross-record constraints the schema cannot express
semantic/              the Apache Ossie model, the vendored spec schema, generated Cube files
src/brightwater/       policy, metrics, ontology gate, graph, tools, agent, procedure driver,
                       decision model, netwatch, identity, audit, loop, CLI
skills/                the compiled AIP procedure (current revision), its sources, and revision 1
decision_model/        rubric, MLX-LM training script, model registry
results/               prices, run records and the computed results table
queries/               numbered graph queries used in the text
tests/                 one proof per chapter
docs/                  one file per chapter
```

## Inspirations

This tutorial started from two talks at AI Engineer.

- **[Make Your AI Agents Predictable With Graph Engineering](https://www.youtube.com/watch?v=g_NgCLTibiM)**, Zachary Blumenfeld (Neo4j). Give an agent a map of the logic it should follow: agent memory as a graph, and past decision traces compiled into a graph-shaped skill, shown on a loan-approval agent. Chapters 1, 3, 4 and 5 follow that path: the memory graph, the recorded free-form runs, those runs compiled into a procedure, and governed revisions of it. AIP, the procedure format Chapter 4 uses, is his project.
- **[Everyone Has the Same AI Models. Here's What Sets Agents Apart](https://www.youtube.com/watch?v=1_lX0m64-aI)**, Richmond Alake (Oracle). The intelligence plane: what an agent knows, what it remembers, what it is allowed to see, and how it gets better, with an ontology and a semantic layer, data security, private inference and small fine-tuned decision models. Those are Chapters 1 and 2 (ontology and semantic layer), 6 (a decision model you own), 7 (private inference), 8 (who may see what) and 10 (getting better from people's reviews).

## Licenses

This repository is Apache-2.0. Every runtime dependency is MIT, Apache-2.0, BSD or PostgreSQL licensed, except Neo4j Community Edition (GPL-3.0), which is run unmodified as a separate server. The Postgres driver is pg8000 (BSD-3-Clause) rather than psycopg (LGPL-3.0) to keep that statement exact. `NOTICE` lists the vendored third-party files (the Ossie spec schema and the Langfuse compose file). Model weights are downloaded at run time, never committed: Qwen2.5-0.5B-Instruct and Qwen3 are Apache-2.0.

The tutorial is a clean-room implementation. It takes its concept from the talks under [Inspirations](#inspirations), and reuses none of their code, data, prompts or text.
