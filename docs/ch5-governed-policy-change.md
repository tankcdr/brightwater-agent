# Chapter 5. A governed policy change

**Tag:** `ch5` · **Services:** as Chapter 4 · **Proof:** one query lists every applicant whose decision changes between revisions, with old runs still pinned to revision 1; a side effect on at least one applicant, called out.

Credit Risk adds a rule. This chapter makes the change the way a regulated change should be made: versioned in the system of record, shipped as a new procedure revision, its blast radius known before it goes live, and every decision made under the old rules still provably made under them.

## The change

```text
P7 (v1)  Group exposure above the second line ($2,000,000) always goes to a person before the
         decision is filed.
```

P4 already sends exposure at or above $1,500,000 to review. P7 adds that above $2,000,000 no agent files the decision alone. The memo is `skills/brightwater-loan-assessment/source/policy-change-p7.md`.

Four edits, each in the one place it belongs:

1. **Policy as code.** `src/brightwater/policy.py` gains `Rule("P7", 1, ...)` and its evaluation. The engine already reports every rule that holds, so nothing else in the engine changes.
2. **Rule rows.** `brightwater policy sync` writes `P7v1` into `compliance.policy_rule` beside P1 to P6. Rows are keyed by rule and version, so a later change to P7 adds `P7v2` rather than overwriting history.
3. **The revision's rules.** `brightwater procedure build` regenerates `assets/policy.json` with P7.
4. **One procedure step.** `confirm-with-person` now shows which rules reserved the decision for a person, since P6 is no longer the only one.

Revision 1 is kept in `skills/revisions/r1/` (fetched from the server with `aip get`), so the comparison can be reproduced from the repo alone.

## Before shipping: what does it change?

```bash
brightwater procedure impact brightwater-loan-assessment@<r1> brightwater-loan-assessment@<r2>
```

`procedure.impact` reads the rules each revision ships with, evaluates every application under both against the same governed facts, and lists every application whose outcome, person requirement or fired rules differ. It also lists the runs still pinned to the old revision.

On the default fixture:

```text
51 application(s) change ({'person only': 51}); ... recorded run(s) stay pinned to the old revision
```

| Change | Applications |
|---|---|
| Review, now also requires a person | 32 |
| Decline that filed automatically, now waits for a person | 9 |
| Decline already reserved for a person by P6, now also by P7 | 10 |
| Outcome changes | 0 |

No outcome changes, which is correct: P7 changes who decides, not what is decided.

## The side effect

Nine of the 51 are declines. Before P7 they filed automatically, because the customer had banked with Brightwater for under ten years and P6 did not apply. P7's text says "always goes to a person", so every one of them now waits for a credit officer, even though the outcome is already a decline and a person reviewing it can only confirm it or send it back.

Whether that is intended is a policy question, not an engineering one. The memo was written with large approvals in mind; read literally, it also adds a manual step to nine declines a month on this book. The point of running `impact` before publishing is that this question gets asked by Credit Risk now, from a list of nine application ids, rather than discovered in the review queue later. If the answer is "declines are out of scope", that is P7v2, with its own impact run.

`tests/test_policy.py::test_p7_sends_very_large_exposure_to_a_person` pins this behaviour, side effect included, so it cannot change silently.

## Shipping it

```bash
aip publish skills/brightwater-loan-assessment
aip runs
```

The publish creates revision 2. In `aip runs`, the earlier runs still show revision 1.

Publishing adds a revision under the same name, and the catalog resolves the name to the newest. Nothing about revision 1 changes: its files are still on the server byte for byte, every run recorded against it still points at it, and `aip get brightwater-loan-assessment@<r1>` returns exactly what ran. `aip pin` can hold the name at revision 1 if the change needs a sign-off before it takes effect.

## Do it yourself

> How the chapters fit together, what carries over between them, and fixes for the common errors: [Working through the chapters](working-through-the-chapters.md).

Everything here uses the scripted driver and needs no API key.

**1. Branch from the chapter's tag and sync the new rule.**

```bash
git switch -c my-ch5 ch5
docker compose --profile aip up -d --wait
source .venv/bin/activate
uv pip install -e ".[dev]"
```

The install picks up any dependencies this chapter adds. Every `brightwater` command runs inside the virtual environment: in a new terminal, run `source .venv/bin/activate` from the repo first, or put `uv run` in front of the command.

Then:

```bash
brightwater fixture generate
brightwater db init && brightwater db load && brightwater policy sync
```

```text
synced 7 rules (P1v1.P2v1.P3v1.P4v1.P5v1.P6v1.P7v1)
```

These lines bring the fixture and the database to a known state; they take a few seconds and are safe to rerun. P7v1 is now a row in `compliance.policy_rule` beside the other six. As in Chapter 4, the environment where this walkthrough was checked ran the AIP server on its filesystem backend rather than in Docker.

**2. Make sure revision 1 is on the server, and run under it.**

```bash
aip config --server http://localhost:8000 --token brightwater-aip-local
aip publish skills/revisions/r1/brightwater-loan-assessment
brightwater procedure run --handbuilt --model scripted --auto-confirm chris \
    --skill brightwater-loan-assessment@d95fc99c75ebbc06
```

```text
brightwater-loan-assessment@d95fc99c75ebbc06
```

If you kept the server from Chapter 4, publishing revision 1 again changes nothing, because the bytes and therefore the hash are the same. The six runs are recorded against `d95fc99c75ebbc06`. `--skill` names the revision, so they run under revision 1 even when the server already holds a newer one, for example when you repeat this chapter after step 4.

**3. Measure the change before you ship it.**

```bash
brightwater procedure impact brightwater-loan-assessment@d95fc99c75ebbc06 skills/brightwater-loan-assessment
```

```text
policy P1v1.P2v1.P3v1.P4v1.P5v1.P6v1 -> P1v1.P2v1.P3v1.P4v1.P5v1.P6v1.P7v1
APP-00024    review -> review   person False -> True  fired P4,P5,P7  exposure 2,975,961
APP-00030   decline -> decline  person  True -> True  fired P1,P4,P5,P6,P7  exposure 2,055,861
APP-00060    review -> review   person False -> True  fired P3,P4,P7  exposure 3,051,726
...
51 application(s) change ({'person only': 51}); 6 recorded run(s) stay pinned to the old revision
```

The pinned-run count is 6 only on a new AIP server. The server keeps its runs between chapters, so if it still holds Chapter 4's, yours is higher (17 after every step and exercise of Chapter 4). The new side is the skill folder in your working copy, not yet published. That is the point of the command: the blast radius is known before anyone else can run the change. Find the nine lines that read `decline -> decline  person False -> True`. Those are the side effect described above, and APP-00137 is one of them.

**4. Ship it, and check that revision 1 is untouched.**

```bash
aip publish skills/brightwater-loan-assessment
aip runs | head -3
rm -rf /tmp/r1 && aip get brightwater-loan-assessment@d95fc99c75ebbc06 --out /tmp/r1 &&
diff -r /tmp/r1/brightwater-loan-assessment skills/revisions/r1/brightwater-loan-assessment && echo identical
```

```text
brightwater-loan-assessment@d005aa1a7fce1d5f
18052cc7529d4776  brightwater-loan-assessment@d95fc99c75ebbc06  done  9 step(s)  2026-11-07T23:48:03.021650+00:00
12e120646b46443b  brightwater-loan-assessment@d95fc99c75ebbc06  done  7 step(s)  2026-11-07T23:48:01.410187+00:00
...
wrote /tmp/r1/brightwater-loan-assessment
identical
```

`aip get` refuses to write over an existing `/tmp/r1`, which is why the old copy is removed first; without that, a second try would compare a stale copy. Revision 2 is `d005aa1a7fce1d5f`, and the name now resolves to it. The runs from step 2 still point at revision 1, and revision 1 comes back from the server byte for byte.

**5. Watch the side effect on one application.**

```bash
brightwater procedure run APP-00137 --model scripted --skill brightwater-loan-assessment@d95fc99c75ebbc06
brightwater procedure run APP-00137 --model scripted --auto-confirm chris
```

```text
APP-00137 run 1: decline  filed           aip a241dae79b284ba4  steps gather>apply-policy>assess>draft-rationale>file>needs-person
APP-00137 run 1: decline  filed           aip 98b443c9cefb4080  steps gather>apply-policy>assess>draft-rationale>file>needs-person>confirm-with-person>record-confirmation
```

Same application, same facts, same outcome. Under revision 1 it files on its own; under revision 2 it waits for a credit officer (here, `--auto-confirm` plays one). `aip runs` shows each run against the revision it used.

## The proof

**Verified here:** P7's unit tests, including the side effect and a check that the same rules without P7 behave exactly as revision 1; `procedure impact` on the two revisions gives the counts above.

**Live** (RUNBOOK.md, Ch 5): publish revision 2 after Chapter 4's runs, then run the impact command against the server.

The live run, on 2026-11-10, with revision 1 `d95fc99c75ebbc06` and revision 2 `d005aa1a7fce1d5f`:

```text
51 application(s) change ({'person only': 51}); 19 recorded run(s) stay pinned to the old revision
```

`aip runs` lists each of those 19 runs (Chapter 4's 18 and the APP-H6 confirmation) against `brightwater-loan-assessment@d95fc99c75ebbc06`, the revision it ran, after revision 2 was published:

```text
284f045996964098  brightwater-loan-assessment@d95fc99c75ebbc06  done  9 step(s)  2026-11-10T20:29:12.815078Z
cfc358d83f274cf7  brightwater-loan-assessment@d95fc99c75ebbc06  done  9 step(s)  2026-11-10T20:27:53.709244Z
c73e938815b343fd  brightwater-loan-assessment@d95fc99c75ebbc06  done  7 step(s)  2026-11-10T20:27:12.289809Z
...
```

## Try it

**1. Write P7v2.** Suppose Credit Risk reads the impact list and answers: declines are out of scope. Make that P7 version 2. In `src/brightwater/policy.py`, change the P7 rule to:

```python
        "P7", 2, "Very large group exposure needs a person",
        "Group exposure above the second line goes to a person before the decision is filed, unless the decision is a decline.",
        {"second_line": "2000000", "declines": False},
```

and add one condition to P7's check in `evaluate`, so that it reads:

```python
    if (r := _rule(rules, "P7")) and facts.group_exposure > Decimal(r.params["second_line"]) \
            and (r.params.get("declines", True) or outcome is not Outcome.DECLINE):
```

Then refresh the skill's rules and measure against the published revision 2:

```bash
brightwater procedure build
brightwater procedure impact brightwater-loan-assessment@d005aa1a7fce1d5f skills/brightwater-loan-assessment
brightwater procedure impact brightwater-loan-assessment@d95fc99c75ebbc06 skills/brightwater-loan-assessment | tail -1
```

How many of the 51 should change back? Will any line still mention a decline?

<details><summary>What happens, and why it matters</summary>

```text
policy P1v1.P2v1.P3v1.P4v1.P5v1.P6v1.P7v1 -> P1v1.P2v1.P3v1.P4v1.P5v1.P6v1.P7v2
APP-00030   decline -> decline  person  True -> True  fired P1,P4,P5,P6  exposure 2,055,861
...
APP-00137   decline -> decline  person  True -> False fired P3,P4  exposure 2,861,747
...
19 application(s) change ({'person only': 19}); 0 recorded run(s) stay pinned to the old revision
32 application(s) change ({'person only': 32}); 6 recorded run(s) stay pinned to the old revision
```

Against revision 2, nineteen applications change. Nine are the side effect going away: declines that P7v1 held for a person now file on their own again (`True -> False`). The other ten are declines P6 already reserves for a person. They still need one, but P7 no longer fires for them, so their explanation changes, and impact lists them because a borrower's explanation is part of the decision. Against revision 1, the change is now exactly the 32 reviews, which is what the memo intended. (The pinned-run counts depend on which runs you have made, so yours may be higher.)

Now sync the policy and look at the rows:

```bash
brightwater policy sync
docker compose exec -T postgres psql postgresql://brightwater:brightwater@localhost/brightwater \
  -c "select rule_id, version, params from compliance.policy_rule where rule_id = 'P7'"
```

The query returns both `P7 | 1 | {"second_line": "2000000"}` and `P7 | 2 | {"declines": false, "second_line": "2000000"}`. Compare that with Chapter 0's first exercise, where a change overwrote the only row.

Notice also what the change needed. The revision carries each rule's version, text and parameters, but the code that interprets them is `evaluate` in the installed package. That is why the new behaviour is a parameter with a default (`r.params.get("declines", True)`) that keeps P7v1 meaning exactly what it meant. `pytest -q tests/test_policy.py` now fails two tests, including the one that pins the side effect: the change is visible in review, not just in production.

Undo it: `git checkout src/ skills/`, then `brightwater db init && brightwater db load && brightwater policy sync` (a plain sync would leave the P7v2 row behind), and run step 2 again if you want runs on the server.
</details>

**2. Pin the name back.** Credit Risk has not signed off on revision 2 yet. Hold the name at revision 1 and run one of the nine:

```bash
aip pin brightwater-loan-assessment d95fc99c75ebbc06
brightwater procedure run APP-00137 --model scripted
aip runs | head -1
aip pin --clear brightwater-loan-assessment
```

Which steps will the run take?

<details><summary>What happens</summary>

```text
brightwater-loan-assessment -> d95fc99c75ebbc06
APP-00137 run 1: decline  filed           aip c983cc67999c4a45  steps gather>apply-policy>assess>draft-rationale>file>needs-person
c983cc67999c4a45  brightwater-loan-assessment@d95fc99c75ebbc06  done  7 step(s)  ...
brightwater-loan-assessment pin cleared
```

With the pin, every client that asks for the name gets revision 1, so APP-00137 files without a person and the run is recorded against `d95fc99c75ebbc06`. Publishing and taking effect are separate steps, which is what lets a change be reviewed after it is on the server and before it applies to anyone. Clearing the pin makes the name resolve to the newest revision again.
</details>

**3. Forget to bump the version.** Change P7's `second_line` from `"2000000"` to `"2500000"` in `src/brightwater/policy.py`, but leave its version at 1. Then:

```bash
brightwater procedure build
brightwater procedure impact brightwater-loan-assessment@d005aa1a7fce1d5f skills/brightwater-loan-assessment | sed -n '1p;$p'
```

What will the first line, which names the two policy versions, say?

<details><summary>What happens, and the limit it shows</summary>

```text
policy P1v1.P2v1.P3v1.P4v1.P5v1.P6v1.P7v1 -> P1v1.P2v1.P3v1.P4v1.P5v1.P6v1.P7v1
31 application(s) change ({'person only': 31}); 2 recorded run(s) stay pinned to the old revision
```

Thirty-one applications change under two policies with the same name. Nothing in the code enforces that a changed rule gets a new version: the version is a convention, and `policy sync` would overwrite the P7v1 row in place, exactly as in Chapter 0. (Your pinned-run count may differ, depending on which runs you have made.)

Two things still hold. The impact command shows the change, so a reviewer can refuse a diff whose version strings match while its outcomes do not. And a published revision is a hash over `assets/policy.json`, so the edited rules could only ship as a new revision with a new hash, whatever the version says. Chapter 9 adds the last piece: each decision's audit packet copies the rule rows it was decided under at the moment it is sealed, so a row overwritten later cannot change what the packet says.

Undo it: `git checkout src/ skills/`.
</details>

## What to carry forward

A policy change is now four reviewed edits, a measured blast radius, and a new revision that leaves the old one intact. The judgment step is still answered by a frontier model, for a frontier price, and its confidence is not recorded at all. Chapter 6 trains a model of our own for it.

**Previous:** [Chapter 4](ch4-compiled-procedure.md) · **Next:** [Chapter 6. Own the decision model](ch6-own-the-decision-model.md)
