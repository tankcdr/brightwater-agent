# Chapter 1. Ontology and the memory graph

**Tag:** `ch1` · **Services:** Postgres, Neo4j (memory) · **Proof:** one graph query walks applicant, owner, owner's other business, late facility; a deliberately broken fixture fails SHACL validation before it reaches the graph.

Chapter 0 ended with a fact that needs four tables in three systems to see: Northpine Freight's co-owner holds a stake in a tile installer whose loan is 80 days late. In SQL that is a self-join on ownership, a join to facilities, a correlated subquery on payments, and enough aliases that the next reader has to draw it on paper to follow it. In a graph it is one path, and the path itself is the explanation.

This chapter builds that graph, and builds the gate in front of it first. Agents trust their memory. If the memory contains an ownership stake in a business that does not exist, or stakes in one business that add up to 135%, the agent will reason over it with complete confidence. So nothing goes into the graph until it has been checked against one schema.

## One schema, three uses

`schema/linkml/brightwater.yaml` defines the entities the agent reasons about, independent of which system holds them: Business, Person, Ownership, Facility, Payment, Application, KycReview and AdverseEvent. LinkML is a YAML modeling language from the biomedical data world, Apache-2.0 licensed, and it earns its place here because one file generates several artifacts.

From this one schema we get:

1. **SHACL shapes** (generated at run time with LinkML's `ShaclGenerator`) that check every record in an import batch.
2. **The node labels and edge types** the importer writes. A `Business` in the schema is a `:Business` node in Neo4j; an `Ownership` becomes an `OWNS` edge.
3. **The vocabulary the agent's tools return**, from Chapter 3 on.

Ownership is a class, not a bare edge, and that is a deliberate modeling choice. "Owns" without "how much" cannot answer the two questions the policy asks: does this person control that business (more than 50%, for group exposure), and do they hold a beneficial stake (25% or more, for P5). The percentage is the fact.

## The gate: SHACL

`src/brightwater/ontology.py` maps the CSVs onto the schema's classes, serializes them to RDF, and validates the result with pySHACL against two sets of shapes.

**Generated shapes** check each record on its own: types, required fields, ranges (a stake must be between 0.01 and 100), patterns (NAICS is six digits), enums, and that every reference points at a node of the right class.

**Two hand-written SPARQL constraints** in `schema/shacl/extra.ttl` check across records:

- the stakes in one business add up to 100% or less, and
- a facility's outstanding balance does not exceed its original amount.

Both kinds matter, and Postgres provides neither for this data. The registry and the ledger are different systems, so `registry.ownership` has no foreign key to `ledger.business`, and no column constraint can sum rows. Those are graph-shaped facts, so they are checked where the graph is built.

`brightwater fixture break` copies the fixture and plants two rows Postgres would accept without complaint:

```text
PER-H01,BUS-99999,30.00     a stake in a business no system knows about
PER-H02,BUS-H01,35.00       a second owner of Larkspur Bindery, which is already 100% owned
```

```bash
brightwater fixture break
brightwater graph load --from fixture/broken
```

```text
SHACL: DOES NOT CONFORM (199,646 triples, 43.9s, 2 violation(s))
  - BUS-H01    Ownership stakes in this business add up to 135.00%, more than 100%.
  - ownership(PER-H01 -> BUS-99999)  business  Value does not have class schema:Organization
refusing to load a batch that does not conform; nothing was written to Neo4j
```

The whole batch is refused, not just the two bad rows. A partial load would leave the graph inconsistent with every system of record at once, which is worse than a stale graph. `tests/test_ch1_ontology.py` asserts both violations by name, and also asserts that the loader never constructs a Neo4j client for a non-conforming batch.

Validation of the full fixture takes about 45 seconds for roughly 200,000 triples on a laptop-class machine, most of it in the 19,260 payment records. That is fine for a nightly import and too slow for a per-request check, which is the right trade for this gate.

## Loading the graph

`brightwater graph load` validates, then writes in two layers. They are kept separate on purpose.

**Entities go in through neo4j-agent-memory.** Every Business, Person, Facility, Application and adverse event is added with `long_term.add_entity`, so the agent's memory API can find it later. Each becomes an `:Entity` node with its POLE+O type (ORGANIZATION, PERSON, OBJECT, EVENT) as a label, plus the LinkML class name as a domain label (`:Business`, `:Facility`). The client runs with no LLM and no embeddings: entity extraction is off because these are structured records, not text, and the embedding provider is a stub that refuses to embed. The stub declares 384 dimensions, matching the model Chapter 7 serves on TEI, because neo4j-agent-memory sizes its vector indexes from the provider at connect time and we do not want to rebuild them later.

**Typed edges go in through plain Cypher.** This is the one place the tutorial works around the library rather than through it, and the reason is specific. In neo4j-agent-memory 0.8.0, `add_relationship` stores every edge as `(:Entity)-[:RELATED_TO {type: ...}]->(:Entity)` and does not write its `attributes` argument; you can see this in `CREATE_ENTITY_RELATIONSHIP` in the library's `graph/queries.py`. An ownership edge without its percentage is the bare edge we just argued against. So the importer writes `(:Person)-[:OWNS {pct}]->(:Business)`, `HAS_FACILITY`, `HAS_PAYMENT`, `APPLIED_FOR` and `HAS_ADVERSE_EVENT` itself, with a uniqueness constraint on each class's `record_key`.

That constraint also covers a second detail of the library: it merges entities on name and type, so two different people with the same name would become one node. The fixture keeps names unique, and `record_key` is what the typed layer matches on, so identity never depends on a display name.

Two things are deliberately absent from the graph. Dates of birth are not loaded: nothing downstream needs them, and data that is not there cannot leak. Monthly ledger rows are not loaded either: the semantic layer in Chapter 2 computes over them where they live.

## The query

`queries/01_cross_system_path.cypher` is the path from Chapter 0, written once:

```cypher
MATCH (b:Business)-[:APPLIED_FOR]->(app:Application {record_key: $app})
MATCH (b)<-[stake:OWNS]-(owner:Person)-[other_stake:OWNS]->(other:Business)
WHERE other <> b
MATCH (other)-[:HAS_FACILITY]->(f:Facility)-[:HAS_PAYMENT]->(p:Payment)
WHERE p.due_date <= app.submitted_on
  AND (p.paid_date IS NULL OR p.paid_date > app.submitted_on)
WITH app, b, owner, stake, other, other_stake, f, min(p.due_date) AS oldest_unpaid
RETURN ... duration.inDays(oldest_unpaid, app.submitted_on).days AS days_past_due
```

Each hop comes from a different system: the application from credit, both stakes from the registry, the facility and its payments from credit again.

```bash
docker compose up -d --wait postgres neo4j-memory
brightwater graph load
brightwater graph query 01 --param app=APP-H4
```

```json
[
  {
    "application": "APP-H4",
    "applicant": "Northpine Freight LLC",
    "owner": "Priya Lindqvist",
    "stake_in_applicant": 40.0,
    "other_business": "Ridgeback Tile Co",
    "stake_in_other": 35.0,
    "facility": "FAC-H90-1",
    "days_past_due": 80
  }
]
```

The same query for APP-H1, the clean applicant, returns nothing.

Notice what this query is for. It finds and explains the path; it is not where the policy gets its number. The days-past-due arithmetic here mirrors the definition, but the governed figure the policy uses comes from the semantic layer in Chapter 2. The graph answers "why" and "through whom"; the semantic layer answers "how much". Keeping those roles apart is what stops the graph from quietly becoming a second, ungoverned metrics store.

## Do it yourself

> How the chapters fit together, what carries over between them, and fixes for the common errors: [Working through the chapters](working-through-the-chapters.md).

Steps 1 to 3 need only Postgres. Step 4 needs Neo4j.

**1. Branch from the chapter's tag and start the services.**

```bash
git switch -c my-ch1 ch1
docker compose up -d --wait postgres neo4j-memory
source .venv/bin/activate
uv pip install -e ".[dev]"
brightwater fixture generate
brightwater db init && brightwater db load && brightwater policy sync
```

`--wait` returns only once Postgres and Neo4j are healthy: Neo4j takes 30 to 60 seconds to start, longer the first time while it downloads the APOC plugin. The install picks up the three libraries this chapter adds (LinkML, pySHACL and neo4j-agent-memory); without it, `brightwater graph` fails at its first import. Every `brightwater` command runs inside the virtual environment: in a new terminal, run `source .venv/bin/activate` from the repo first, or put `uv run` in front of the command.

If you changed files during the previous chapter's exercises, commit them on your branch or run `git stash` first, so they don't follow you into this one. The last two lines bring the fixture and the database to a known state; they take a few seconds and are safe to rerun.

**2. Validate the clean fixture.**

```bash
brightwater graph validate
```

```text
SHACL: conforms (199,636 triples, 46.4s, 0 violation(s))
```

This is the gate on its own, with nothing written anywhere. Every record from the five systems has been mapped onto the schema's classes, turned into RDF, and checked against the generated shapes and the two hand-written ones in `schema/shacl/extra.ttl`. Most of the 46 seconds goes on the 19,260 payments, which is why this check belongs in a nightly import and not in front of every request.

**3. Break the fixture and watch the gate refuse it.**

```bash
brightwater fixture break
brightwater graph load --from fixture/broken
echo $?
```

```text
planted registry.ownership row: PER-H01,BUS-99999,30.00
planted registry.ownership row: PER-H02,BUS-H01,35.00
broken copy written to fixture/broken
SHACL: DOES NOT CONFORM (199,646 triples, 45.2s, 2 violation(s))
  - BUS-H01    Ownership stakes in this business add up to 135.00%, more than 100%.
  - ownership(PER-H01 -> BUS-99999)  business  Value does not have class schema:Organization
refusing to load a batch that does not conform; nothing was written to Neo4j
1
```

This step works even with Neo4j stopped, and that is the point: the loader validates first and stops before it opens a connection, so a bad batch never gets near the graph. The ten extra triples are the two planted rows. Each violation names the record, so whoever owns the import knows exactly where to look.

**4. Load the graph and walk the path (needs Neo4j).**

```bash
brightwater graph load &&
brightwater graph query 01 --param app=APP-H4 &&
brightwater graph query 01 --param app=APP-H1
```

The queries read whatever the graph already holds, so they only run if the load succeeded.

With Neo4j running, query 01 for APP-H4 returns the single row shown in [The query](#the-query) above (Northpine, Priya Lindqvist, Ridgeback Tile Co, `FAC-H90-1`, 80 days), and for APP-H1 it prints `[]`. Those are the results `tests/test_ch1_graph.py` asserts, and the live run (RUNBOOK.md, Ch 1) returned the same row. Without Neo4j, `graph load` validates the clean fixture and then fails with `Neo4j service unavailable`.

Compare the query with the SQL you wrote in Chapter 0's third exercise. The joins are the same, but here each one is a named edge that the importer wrote after the gate checked it, and the row the query returns reads as the explanation.

## The proof

```bash
pytest tests/test_ch1_ontology.py tests/test_ch1_graph.py
```

- The clean fixture conforms; the broken one fails with exactly the two planted violations, named.
- The loader refuses the broken batch before any Neo4j client exists.
- After loading, the graph holds 1,206 applications and 2,798 ownership edges, matching the systems of record.
- Query 01 returns the Northpine, Lindqvist, Ridgeback, FAC-H90-1, 80-days row for APP-H4 and nothing for APP-H1.

Without Neo4j the ontology tests run and the three graph tests skip: `4 passed, 3 skipped`.

## Try it

None of these needs Neo4j. Each validation takes about 45 seconds.

**1. Plant your own defects.** Copy the fixture, then make two edits that Postgres would accept: give Ridgeback's loan an outstanding balance larger than the amount it was opened for, and drop the last digit from Larkspur Bindery's NAICS code.

```bash
rm -rf fixture/mine && cp -r fixture/out fixture/mine &&
sed -i.bak 's/^FAC-H90-1,BUS-H90,term,2022-03-01,180000.00,121500.00/FAC-H90-1,BUS-H90,term,2022-03-01,180000.00,190000.00/' fixture/mine/credit.facility.csv && rm fixture/mine/credit.facility.csv.bak &&
sed -i.bak 's/^BUS-H01,Larkspur Bindery LLC,323120,/BUS-H01,Larkspur Bindery LLC,32312,/' fixture/mine/ledger.business.csv && rm fixture/mine/ledger.business.csv.bak &&
brightwater graph validate --from fixture/mine
```

The first line starts from a fresh copy: if `fixture/mine` were left over from an earlier try, `cp -r` would copy into it instead of replacing it, and the edits would land on the old files.

How many violations do you expect, and which shapes will report them? Then load the same files into Postgres with `brightwater db load --from fixture/mine`, and look at Larkspur's NAICS code:

```bash
docker compose exec -T postgres psql postgresql://brightwater:brightwater@localhost/brightwater \
  -c "select naics from ledger.business where business_id = 'BUS-H01'"
```

Will Postgres complain?

<details><summary>What happens, and why it matters</summary>

```text
SHACL: DOES NOT CONFORM (199,636 triples, 41.9s, 2 violation(s))
  - BUS-H01  naics  Value does not match pattern '^[0-9]{6}$'
  - FAC-H90-1    Outstanding balance 190000.00 exceeds the original amount 180000.00.
```

Two violations, from the two kinds of shape. The NAICS pattern comes from the LinkML schema, generated; the balance check is one of the two hand-written SPARQL constraints. The triple count is unchanged, because you edited values rather than adding rows.

`db load --from fixture/mine` succeeds, and `select naics from ledger.business where business_id = 'BUS-H01'` returns `32312`. Postgres could enforce the balance rule with a `CHECK` constraint, but the DDL has none, and in a real bank you rarely own the source system's DDL. The gate is the one place you do control.

Undo it: `rm -rf fixture/mine`, then `brightwater db init && brightwater db load && brightwater policy sync`.
</details>

**2. Remove the cross-record constraint.** Delete the `bw:OwnershipTotalShape` block from `schema/shacl/extra.ttl` (everything from that line down to the blank line before `bw:FacilityBalanceShape`), and validate the broken fixture again. This uses `fixture/broken` from step 3; if it is missing, run `brightwater fixture break` first.

```bash
brightwater graph validate --from fixture/broken
```

Before you run it: each stake in Larkspur is between 0.01% and 100%, and each points at a real business. Which violations are left?

<details><summary>What happens</summary>

```text
SHACL: DOES NOT CONFORM (199,646 triples, 26.5s, 1 violation(s))
  - ownership(PER-H01 -> BUS-99999)  business  Value does not have class schema:Organization
```

The 135% is gone from the report. Every shape generated from the schema looks at one record at a time, and each of Larkspur's stakes is fine on its own. Only a constraint that sums across records can see that together they are impossible. That is why `extra.ttl` exists, and why it is short: it holds only the rules the schema cannot express.

Undo it: `git checkout schema/shacl/extra.ttl`.
</details>

**3. A typo the gate cannot catch.** Change Priya Lindqvist's stake in Ridgeback from 35% to 20%, validate, load it, and assess Northpine:

```bash
rm -rf fixture/typo && cp -r fixture/out fixture/typo &&
sed -i.bak 's/^PER-H05,BUS-H90,35.00/PER-H05,BUS-H90,20.00/' fixture/typo/registry.ownership.csv && rm fixture/typo/registry.ownership.csv.bak &&
brightwater graph validate --from fixture/typo
brightwater db load --from fixture/typo && brightwater assess APP-H4
```

Does the gate refuse it? What happens to APP-H4?

<details><summary>What happens, and the limit it shows</summary>

```text
SHACL: conforms (199,636 triples, 43.3s, 0 violation(s))
application  outcome  person    DSCR   group exp.  fired
APP-H4       approve  no        1.51      510,000  -
```

The batch conforms, because a 20% stake is perfectly valid: Ridgeback's stakes now add up to 85%, which is allowed. With Priya under the 25% line, P5 no longer applies and Northpine is approved.

The gate checks that the data is consistent, not that it is true. A wrong value that is still plausible passes, and the decision changes with it. No schema fixes that. What you can do is know, afterwards, exactly which records each decision read, so that when the registry corrects the stake you can find every decision that relied on the wrong one. Chapter 3 starts recording the records each run reads, and Chapter 9 seals them into the decision's audit packet.

Undo it: `rm -rf fixture/typo`, then `brightwater db init && brightwater db load && brightwater policy sync`. APP-H4 goes back to review with P5.
</details>

## What to carry forward

The graph now holds entities the agent can traverse, and every one of them passed one schema on the way in. What it does not hold is a single governed definition of any number. Chapter 0's DSCR still lives in a SQL string, and so does the 50% control threshold that decides group membership. Chapter 2 moves every definition into one file and puts a server in front of it.

**Previous:** [Chapter 0](ch0-systems-of-record.md) · **Next:** Chapter 2. Semantic layer
