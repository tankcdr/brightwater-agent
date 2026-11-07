# Source and provenance

Compiled with the `aip-compile` skill (AIP 0.7.0, format 0.6a1) by a frontier model, from:

- `assessment-sop.md`: the bank's written assessment SOP, version 3 (the same text the Ch 3 free-form agent follows).
- `assets/policy.json`: lending rules P1 to P6, generated from `src/brightwater/policy.py` by `brightwater procedure build`.
- `rubric.md`: the assessment rubric for risk tier, cash flow stability and recommendation.
- The recorded Ch 3 runs (`brightwater procedure export-traces` writes `traces.md` here): tool order, figures read, linter rejections. They decide nothing in the procedure; they show which steps every competent run took, which became the fixed order below, and which mistakes recurred, which became anti-patterns and linter rules.

## Step kinds, and why

| Step | Kind | Why this kind |
|---|---|---|
| gather | execution | Reading governed figures is a lookup. No judgment, so no model. |
| apply-policy | execution | P1 to P6 are deterministic rules over structured facts: a script, never a decision. |
| assess | decision | Risk tier, stability and recommendation are judgments with a fixed answer space, so they are typed questions with thresholds, not free text. |
| draft-rationale | client_task | The rationale is generated text. Its form is enforced by the linter in `file`. |
| file | execution | Linting and writing a record are code. |
| needs-person | router | Branches on the policy's own `requires_person`. |
| confirm-with-person | client_task | P6 requires a person. The driver routes this task to a human and never to a model. |
| record-confirmation | execution | Writes the person's answer onto the filed decision. |

## Completeness check against the SOP

| SOP section | Where it is in the procedure |
|---|---|
| 1. Pull the application; measure as of the submitted date | `gather` (the semantic layer measures as of `submitted_on`) |
| 2. Governed figures only; record key for each | `gather` outputs `facts` and `records`; anti-pattern 1 |
| 3. Look for connections one hop away | `gather` via `related_facility` and `group_member` in the semantic layer; see deliberate drop 1 |
| 4. Apply P1 to P6 exactly; every rule reported; severity order | `apply-policy` with `assets/policy.json` |
| 4. Assessment when the case goes to review | `assess` (asked on every case, see deliberate drop 2) |
| 5. P6 needs a person before filing | `apply-policy.requires_person`, `needs-person`, `confirm-with-person`, `record-confirmation` |
| 6. Outcome first; cite every figure; name rules; no protected characteristics | `assets/style.md`, enforced by the linter in `file` |
| 7. File; fix and refile if rejected | `file`; the driver re-asks the rationale until it lints, before the step runs |

## Deliberate drops, with rationale

1. **Graph traversal to name the people on a path (SOP 3).** The procedure does not call the memory graph. Every fact the policy uses about connected businesses is already a governed figure (`related_facility`, `group_member`), and names are not allowed into the judgment step's inputs. The graph remains the place to explain a path to a person (query 01), outside the decision.
2. **"Assess only when the case goes to review" (SOP 4).** The `assess` decision is asked on every application. Keys produced on one branch cannot be read after branches join, and the rationale needs the assessment on the review path. Asking every time also gives Ch 6 labelled data across all bands. The recommendation is advice and never changes `rule_outcome`.
3. **Banker notes.** The SOP never lets a note be the source of a figure. The procedure does not read notes at all.
