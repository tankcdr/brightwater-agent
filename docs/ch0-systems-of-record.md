# Chapter 0. Systems of record and policy as code

**Tag:** `ch0` · **Services:** Postgres · **Proof:** the policy engine returns the expected outcome and fired rules for all six hand-built applicants, in CI.

Every later chapter in this series puts something smarter in front of the decision: an agent, a compiled procedure, a model we train ourselves. None of that can be judged without a referee. This chapter builds the referee first: the bank's data, and its lending policy as plain code that turns facts into a decision the same way every time.

By the end you will have five systems of record in Postgres filled with 63,028 rows of seeded, invented data, a policy engine that is a pure function, and a test that pins the outcome of six applicants built by hand to exercise one decision path each.

## The bank

Brightwater Community Bank is fictional. It makes small-business loans, and like every real bank its facts are scattered across systems that were never designed to talk to each other.

| Schema | System | Holds |
|---|---|---|
| `ledger` | Core deposits | Businesses, deposit accounts, monthly inflows and outflows |
| `credit` | Loan servicing | Facilities, payment history, open applications |
| `compliance` | KYC and policy | KYC status, adverse events, the lending policy as rule rows |
| `registry` | Beneficial ownership | People and their ownership stakes, with percentages |
| `relationship` | CRM | Bankers, which businesses they cover, free-text notes |

The DDL is in `schema/postgres/001_systems.sql`. One rule shapes all of it: **no foreign key crosses a schema.** In a real bank these would be separate databases owned by separate teams, joined only by a shared `business_id`. Keeping that discipline in a single Postgres is what makes the next two chapters honest. When Chapter 1 needs to know that an ownership stake points at a business the ledger actually knows about, Postgres cannot tell us, and that gap is the reason the chapter exists.

Inflows in the ledger are split into `operating_inflow` and `nonoperating_inflow` (owner capital injections, asset sales). That split looks like a detail now. Chapter 2 turns it into the cleanest demonstration of why metric definitions need governance.

## The fixture

`brightwater fixture generate` writes one CSV per table and a manifest of SHA-256 hashes. The same seed and the same `fixture/handbuilt.yaml` always produce byte-identical files; `tests/test_fixture.py` checks it. Determinism matters here for a practical reason: every number in this series, from DSCR to cost per decision, has to be reproducible by a reader on their own laptop.

Two populations come out of one run.

**Six hand-built applicants**, one per decision path. Their cash flows are derived from a target DSCR, so the ratio lands where the case needs it regardless of how the monthly noise falls.

| Application | Business | Path | Expected |
|---|---|---|---|
| APP-H1 | Larkspur Bindery LLC | Clean | approve |
| APP-H2 | Tidewater Signworks Inc | KYC review overdue | decline, P1 |
| APP-H3 | Cobble Lane Bakery LLC | DSCR inside the judgment band | review, P3 |
| APP-H4 | Northpine Freight LLC | Clean, but a co-owner's other business is 80 days late | review, P5 |
| APP-H5 | Harrow & Finch Outfitters LLC | Group exposure over the cap, visible only through the owners | review, P4 |
| APP-H6 | Saltmarsh Ceramics Co | Held out. Customer since 2009, DSCR below the floor | decline, P3 + P6, needs a person |

APP-H4 and APP-H5 are the cases the series is built around. Nothing in Northpine's own records is wrong. The fact that matters sits three systems away: Priya Lindqvist owns 40% of Northpine (registry), also owns 35% of Ridgeback Tile Co (registry again, a different row), and Ridgeback's term loan has three missed payments (credit). A single-table agent misses it. Harrow & Finch is the same shape for exposure: alone it owes $850,000 against a $1.5M cap, but its owners control three other businesses, and the group total is $1,722,000.

APP-H6 is held out. No procedure or model in later chapters sees it until evaluation.

**1,200 generated applicants**, plus 303 affiliate businesses that share owners with them, spread across every band of the policy. On the default seed the policy engine assigns 516 approvals, 298 reviews and 386 declines, and every rule fires somewhere: P1 86 times, P2 42, P3 529, P4 123, P5 77, P6 198. Chapter 6 trains and evaluates the decision model on this set.

All names come from word lists in `src/brightwater/fixture.py`. None of it describes a real business or person.

## The policy as code

`src/brightwater/policy.py` is the single source of truth for the lending policy.

| Rule | Says | Parameters |
|---|---|---|
| P1 | KYC must be current, else decline | |
| P2 | No adverse event within the lookback, else decline | `lookback_days: 365` |
| P3 | DSCR at or above the approve line stands; inside the band goes to review; below the floor declines | `approve_line: 1.25`, `floor: 1.10` |
| P4 | Group exposure at or over the cap goes to review | `cap: 1,500,000` |
| P5 | A facility 60+ days late at a business where an owner of the applicant holds 25%+ blocks auto-approval | `days_past_due: 60`, `owner_stake_pct: 25` |
| P6 | Declining a customer of 10+ years needs a person before filing | `tenure_years: 10` |

The engine is one function, `evaluate(facts) -> Decision`. It touches no database. Three choices in it are worth calling out, because each one pays off later.

**Every rule that holds is reported, not just the one that decided.** A decline for P1 that would also have tripped P5 says so. This is the difference between an outcome and an explanation, and the audit packet in Chapter 9 depends on it.

**The facts carry no names, no dates of birth and no free text.** `Facts` holds ids, booleans and numbers. That is the exact input the judgment step will see in Chapter 4, and what the audit trail records as that step's input. Deciding it now means the privacy boundary is a type, not a promise.

**Thresholds live here; definitions do not.** P3 knows that 1.25 is the approve line. It does not know how DSCR is computed. P5 knows that 60 days and a 25% stake matter. It does not know which facilities count as related or how lateness is measured. That split is easy to blur in Chapter 0, where both happen to sit in Python, and Chapter 2 is where it becomes load-bearing.

`brightwater policy sync` writes the rules into `compliance.policy_rule`, one row per rule and version, so the policy that governed a decision can be read back from a system of record. Chapter 5 adds P7 and bumps versions in place.

## Where the facts come from, for now

`src/brightwater/metrics_sql.py` computes the facts with hand-written SQL. It is the version most teams start with: each fact is a query someone wrote once, in application code. The definitions it encodes:

- **DSCR window:** the 12 full calendar months before the month of application.
- **Cash available:** operating inflow plus non-operating inflow minus outflow, across all of the business's accounts.
- **Debt service:** monthly payments on existing facilities plus the level payment on the requested loan, times 12.
- **Group:** the applicant plus every business in which any owner of the applicant holds more than 50%.
- **Related facilities:** the applicant's own, plus those at any business in which an owner holds a stake, tagged with the largest stake.
- **Days past due:** from the oldest payment still unpaid on the application date.

It works, and it stays in the repo after Chapter 2 replaces it, as the reference the migration is tested against.

## Row-level security and read auditing, defined now

`schema/postgres/002_rls.sql` defines the rule a bank actually uses: a banker sees a business only if they cover it, with the banker's identity carried in the session setting `brightwater.banker_id`. Postgres lets you create policies without enabling row-level security, so they are defined now and switched on in Chapter 8, when Keycloak supplies the identity.

Ownership is deliberately left readable. A banker assessing Northpine must see that its co-owner holds a stake in Ridgeback, even though someone else covers Ridgeback. What they may not see is Ridgeback's ledger. Chapter 8 keeps that split: OpenFGA decides which applications a banker may assess, row-level security keeps each ledger to its own banker, and ownership stays unrestricted.

The Postgres image in `compose.yaml` also loads `pgaudit` with `pgaudit.log = read`, so from the first query every read is logged with the relation it touched. Chapter 9 joins those lines to a decision id.

## Do it yourself

> How the chapters fit together, what carries over between them, and fixes for the common errors: [Working through the chapters](working-through-the-chapters.md).

You need Docker and uv. Everything in this chapter runs on a laptop in a few minutes, with no API key.

**1. Start Postgres and install the package.**

```bash
git switch -c my-ch0 ch0
docker compose up -d --wait postgres
uv venv -p 3.12 --clear
source .venv/bin/activate
uv pip install -e ".[dev]"
brightwater --help
```

`--clear` replaces an existing `.venv`, so the block is safe to run again; without it, `uv venv` stops with `A virtual environment already exists`. If you already ran the README's **Start here**, you are on `my-ch0` and the first line fails with `a branch named 'my-ch0' already exists`: that is fine, carry on with the rest. `brightwater --help` should print the command list; if it does, you're set. Every `brightwater` command runs inside the virtual environment: in a new terminal, run `source .venv/bin/activate` from the repo first, or put `uv run` in front of the command.

**2. Generate the bank.**

```bash
brightwater fixture generate
```

```text
wrote 13 tables, 63,028 rows to fixture/out
hand-built: APP-H1, APP-H2, APP-H3, APP-H4, APP-H5, APP-H6 (held out: APP-H6)
```

Look in `fixture/out/`: one CSV per table and a `manifest.json` of hashes. Run the command again and the hashes do not change. Every number later in the series traces back to these files, which is why they have to be identical on your machine and on mine.

**3. Load the five systems and the policy.**

```bash
brightwater db init && brightwater db load && brightwater policy sync
```

The last line is `synced 6 rules (P1v1.P2v1.P3v1.P4v1.P5v1.P6v1)`. That string is the policy version, and it is stamped on every decision from here on. Look at the rows it wrote:

```bash
docker compose exec -T postgres psql postgresql://brightwater:brightwater@localhost/brightwater \
  -c "select rule_id, version, params from compliance.policy_rule order by rule_id"
```

**4. Assess the six hand-built applicants.**

```bash
brightwater assess --handbuilt
```

```text
application  outcome  person    DSCR   group exp.  fired
APP-H1       approve  no        1.33      342,000  -
APP-H2       decline  no        1.62      161,000  P1
APP-H3       review   no        1.17      216,000  P3
APP-H4       review   no        1.51      510,000  P5
APP-H5       review   no        1.44    1,722,000  P4
APP-H6       decline  yes       0.96      204,000  P3,P6
metrics: sql   policy: P1v1.P2v1.P3v1.P4v1.P5v1.P6v1
```

Read each row against the table of applicants above. Two are worth stopping on. APP-H4 is a clean approve by its own numbers (DSCR 1.51, exposure well under the cap), yet it goes to review; the reason is a business it does not own. APP-H6 shows that a rule can change who decides without changing what is decided: P3 declines it, and P6 says a person has to confirm that.

**5. Ask why.**

```bash
brightwater assess APP-H6 --json
```

The `facts` block is everything the engine saw: ids, booleans and numbers, no names. The `fired` list has one entry per rule that held, each with its effect and a reason in words:

```text
{"rule_id": "P3", "effect": "decline",         "reason": "DSCR 0.96 is below the 1.10 floor"}
{"rule_id": "P6", "effect": "requires_person", "reason": "customer for 17.4 years; a person must confirm the decline"}
```

Run the same command for APP-H4 and find the fact that fired P5.

## The proof

```bash
pytest tests/test_policy.py tests/test_fixture.py tests/test_ch0_handbuilt.py
```

`test_policy.py` checks every boundary with no database at all: DSCR of exactly 1.25 approves and 1.2499 goes to review, exposure of exactly the cap goes to review, a 59-day delinquency does not fire P5 and a 24.99% stake does not either. `test_ch0_handbuilt.py` loads the full fixture and asserts the outcome, the fired rules and the person flag for all six applicants, then checks that every rule fires somewhere in the generated set. `.github/workflows/ci.yml` runs all of it against a Postgres service container on every push.

## Try it

Each exercise changes one thing. Predict the result before you run it; the explanation is underneath.

**1. Move the approve line.** In `src/brightwater/policy.py`, change P3's `approve_line` from `"1.25"` to `"1.15"`. Then:

```bash
brightwater assess APP-H3
brightwater policy sync
pytest -q tests/test_policy.py
```

<details><summary>What happens, and why it matters</summary>

APP-H3 (DSCR 1.17) now approves, and three tests in `test_policy.py` fail, because they pin the boundaries. Now look at the rule row again with the `psql` query from step 3: it still says `P3`, version `1`, but with the new parameter. `policy sync` updated the row in place.

That is the problem to notice. Every decision already filed under `P3v1` now points at a rule row that says something different from what was in force when it was made. The tests caught the change; the system of record did not. Chapter 5 is the fix: a changed parameter becomes a new rule version and a new procedure revision, and the old one stays readable.

Put it back before moving on: `git checkout src/brightwater/policy.py && brightwater policy sync`.
</details>

**2. Lower the floor and watch the person disappear.** Change P3's `floor` from `"1.10"` to `"0.90"` and run `brightwater assess APP-H6`.

<details><summary>What happens</summary>

```text
APP-H6       review   no        0.96      204,000  P3
```

APP-H6 is now inside the judgment band, so it goes to review instead of declining, and P6 no longer fires: it only applies to declines. A threshold change in one rule removed a person from a decision through another. This kind of interaction is what Chapter 5's impact query is built to surface before a change ships. Restore the file afterwards: `git checkout src/brightwater/policy.py`.
</details>

**3. Find Northpine's problem by hand.** Without using the CLI, write SQL that explains why APP-H4 went to review: from the application, to its owners, to the other businesses those owners hold, to a loan at one of them that is 60 or more days late. The tables are `credit.application`, `registry.ownership`, `registry.person`, `credit.facility` and `credit.payment`. To run SQL, open a `psql` session inside the Postgres container (no client needed on your machine; `\q` leaves it):

```bash
docker compose exec postgres psql postgresql://brightwater:brightwater@localhost/brightwater
```

<details><summary>One answer</summary>

```sql
SELECT a.application_id, p.full_name AS owner, o2.pct AS stake_pct,
       f.business_id AS other_business, f.facility_id,
       a.submitted_on - min(pay.due_date) AS days_past_due
FROM credit.application a
JOIN registry.ownership o1  ON o1.business_id = a.business_id           -- owners of the applicant
JOIN registry.person    p   ON p.person_id    = o1.person_id
JOIN registry.ownership o2  ON o2.person_id   = o1.person_id            -- everything they hold
                           AND o2.business_id <> a.business_id
JOIN credit.facility    f   ON f.business_id  = o2.business_id          -- loans at those businesses
JOIN credit.payment     pay ON pay.facility_id = f.facility_id
                           AND pay.due_date < a.submitted_on
                           AND (pay.paid_date IS NULL OR pay.paid_date > a.submitted_on)
WHERE a.application_id = 'APP-H4'
GROUP BY 1, 2, 3, 4, 5;
```

```text
 application_id |      owner      | stake_pct | other_business | facility_id | days_past_due
 APP-H4         | Priya Lindqvist |     35.00 | BUS-H90        | FAC-H90-1   |            80
```

Five joins across three systems, and nothing in Postgres knows they connect: those joins live only in your head and in this query. To see how little the database protects you, insert an ownership stake in a business that does not exist:

```sql
INSERT INTO registry.ownership VALUES ('PER-H05', 'BUS-DOES-NOT-EXIST', 90);   -- INSERT 0 1
DELETE FROM registry.ownership WHERE business_id = 'BUS-DOES-NOT-EXIST';
```

Postgres accepts it, because no foreign key crosses a schema. Chapter 1 adds the check that would have refused it, and a graph in which this five-join walk is one short query.
</details>

## What to carry forward

The engine is boring on purpose. It is the fixed point against which the agent in Chapter 3, the procedure in Chapter 4 and the decision model in Chapter 6 are all measured. The next two chapters fix the two weaknesses this chapter leaves in place: nothing checks that the five systems agree with each other, and the definition of every number lives in a SQL string that nobody else can see.

**Next:** Chapter 1. Ontology and the memory graph
