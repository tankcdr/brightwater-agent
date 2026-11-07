from brightwater.linter import lint

KNOWN = {"cube:application_month.dscr/APP-H3", "credit.application:APP-H3", "policy:P3v1"}


def rules(text, outcome="review", known=KNOWN):
    return [v.rule for v in lint(text, outcome, known)]


def test_clean_rationale_passes():
    text = ("Outcome: review\n"
            "DSCR is 1.17 [cube:application_month.dscr/APP-H3], inside the judgment band, so P3 sends it to review "
            "[policy:P3v1].\nThe request is $175,000 over 120 months [credit.application:APP-H3].")
    assert rules(text) == []


def test_outcome_must_lead_and_match():
    assert rules("DSCR is fine.\nOutcome: review") == ["outcome_first"]
    assert rules("Outcome: approve\nNothing else.") == ["outcome_first"]


def test_uncited_figure_fails():
    assert rules("Outcome: review\nDSCR is 1.17, inside the band.") == ["figure_uncited"]


def test_record_ids_are_not_figures():
    assert rules("Outcome: review\nAPP-H3 and FAC-H90-1 were checked under P3.") == []


def test_citation_must_come_from_the_run():
    assert rules("Outcome: review\nDSCR is 1.17 [cube:application_month.dscr/APP-H9].") == ["unknown_record"]


def test_protected_attributes_fail():
    assert "protected_attribute" in rules("Outcome: review\nThe owner is married and nearing retirement age.")
    assert rules("Outcome: review\nThe borrower's management team is experienced.") == []
