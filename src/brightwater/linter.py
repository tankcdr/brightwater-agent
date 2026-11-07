"""The rationale linter.

A rationale is what a banker reads and what an examiner may challenge, so it
gets checked like code before it is filed. Three rules, from SOP section 6:

1. **Outcome first.** The first line is `Outcome: <outcome>` and matches the
   outcome being filed.
2. **Every figure cited, to a record the run actually read.** Each sentence
   with a number carries at least one `[record-key]` citation, and every
   citation is a record key some tool returned during this run. A citation
   the run never saw is a hallucinated source, and it fails.
3. **No protected characteristics.** The Equal Credit Opportunity Act
   (Regulation B) prohibits credit decisions on race, color, religion,
   national origin, sex, marital status, age, receipt of public assistance,
   or the good-faith exercise of consumer-protection rights. The rationale may
   not mention any of them, or a date of birth.

The linter is deliberately mechanical. It cannot tell whether the reasoning
is good; it can tell whether the reasoning is traceable and lawful in form,
which is what makes the reasoning reviewable at all.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

CITATION = re.compile(r"\[([a-z]+[a-z_.]*:[^\]\s]+)\]")
FIGURE = re.compile(r"(?<![A-Za-z\-])\$?\d[\d,]*(?:\.\d+)?%?")
OUTCOME_LINE = re.compile(r"^\s*Outcome:\s*(approve|review|decline)\b", re.IGNORECASE)

# Words that reveal reasoning from a protected basis. Kept narrow to avoid
# flagging ordinary words: "age" alone is checked as a whole word.
PROTECTED = [
    r"race", r"racial", r"colou?r", r"religio(n|us)", r"national origin", r"ethnic(ity)?", r"nationality",
    r"\bsex\b", r"gender", r"marital", r"married", r"divorced", r"widow(ed)?", r"\bage\b", r"\baged\b",
    r"years old", r"elderly", r"pregnan(t|cy)", r"disabilit(y|ies)", r"disabled", r"public assistance",
    r"welfare", r"date of birth", r"\bdob\b", r"born (in|on)",
]
PROTECTED_RE = re.compile("|".join(f"(?:{p})" for p in PROTECTED), re.IGNORECASE)

# Record ids such as APP-H4 or FAC-H90-1 contain digits but are not figures.
_IDS = re.compile(r"\b(?:APP|BUS|FAC|PER|ACC|NOTE|ADV|BKR)-[A-Z0-9-]+\b|\bP\d+(?:v\d+)?\b")


@dataclass(frozen=True)
class Violation:
    rule: str
    detail: str

    def __str__(self) -> str:
        return f"{self.rule}: {self.detail}"


def _sentences(text: str) -> list[str]:
    # Split on sentence ends and line breaks; keep bracketed citations attached.
    parts = re.split(r"(?<=[.!?])\s+(?=[A-Z(])|\n+", text)
    return [p.strip() for p in parts if p.strip()]


def lint(rationale: str, outcome: str, known_records: set[str]) -> list[Violation]:
    out: list[Violation] = []

    m = OUTCOME_LINE.match(rationale)
    if not m:
        out.append(Violation("outcome_first", "the rationale must start with 'Outcome: <approve|review|decline>'"))
    elif m.group(1).lower() != outcome:
        out.append(Violation("outcome_first", f"rationale says {m.group(1).lower()!r} but the filing says {outcome!r}"))

    for i, sentence in enumerate(_sentences(rationale)):
        if i == 0 and OUTCOME_LINE.match(sentence):
            sentence = OUTCOME_LINE.sub("", sentence, count=1)
        cites = CITATION.findall(sentence)
        bare = CITATION.sub("", _IDS.sub("", sentence))
        if FIGURE.search(bare) and not cites:
            out.append(Violation("figure_uncited", f"no citation in: {sentence[:120]!r}"))
        for c in cites:
            if c not in known_records:
                out.append(Violation("unknown_record", f"[{c}] was not returned by any tool in this run"))

    for hit in sorted({h.group(0).lower() for h in PROTECTED_RE.finditer(rationale)}):
        out.append(Violation("protected_attribute", f"mentions {hit!r}"))
    return out


def cited(rationale: str) -> list[str]:
    return sorted(set(CITATION.findall(rationale)))
