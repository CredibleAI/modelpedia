
from modelpedia.build import database
from modelpedia.ingest import screen
from tests.helpers import AUDIT, AUDIT_REVIEWS, OPTIMISER, screened_row


OPTIMISER_REVIEWS = (
    "The paper proposes a new optimizer. The proposed method outperforms Adam on ImageNet and "
    "the authors introduce a novel schedule.",
    "This work proposes an optimizer. The proposed approach achieves better convergence and is "
    "state-of-the-art, though the novel framework is close to prior work.",
    "The proposed algorithm is a new method for large-scale training. It outperforms baselines.",
)

def test_a_paper_auditing_a_named_model_outscores_one_proposing_a_method():
    assert screen.screen(*AUDIT).score > screen.screen(*OPTIMISER).score
    assert screen.screen(*AUDIT).tier != screen.WEAK

def test_a_paper_with_nothing_to_do_with_explanation_screens_weak():
    assert screen.screen(*OPTIMISER).tier == screen.WEAK

def test_an_abstract_alone_cannot_reach_the_top_tier():
    assert screen.screen(*AUDIT).tier == screen.POSSIBLE
    with_reviews = screen.combine(screen.screen(*AUDIT),
                                  screen.review_screen(AUDIT_REVIEWS))
    assert with_reviews.tier == screen.STRONG

def test_reviewers_agreeing_that_a_paper_proposes_a_method_pushes_it_down():
    proposing = screen.combine(screen.screen(*OPTIMISER),
                               screen.review_screen(OPTIMISER_REVIEWS))
    assert proposing.tier == screen.WEAK
    assert proposing.points("r-proposes") < 0

def test_proposing_a_method_never_vetoes_a_paper_that_also_reports_findings():
    both = (AUDIT[0], AUDIT[1] + " We propose a new attribution method. Our approach "
                                 "outperforms prior work and is state-of-the-art.")
    assert screen.combine(screen.screen(*both),
                          screen.review_screen(AUDIT_REVIEWS)).tier == screen.STRONG

def test_screening_has_no_reject_outcome():
    for title, abstract in (AUDIT, OPTIMISER, ("", ""), ("x", None)):
        assert screen.screen(title, abstract).tier in (screen.STRONG, screen.POSSIBLE,
                                                       screen.WEAK)

def test_a_model_name_is_matched_on_word_boundaries_only():
    assert screen.screen("the same architecture", "high activity levels").signals == ()
    assert any(s.term == "resnet-50" for s in screen.screen("a ResNet-50 backbone", "").signals)

def test_an_abbreviation_that_names_two_different_things_is_not_a_model_signal():
    for ambiguous in ("we use SAM, sharpness-aware minimization",
                      "we report MAE on the test split",
                      "we opt for a smaller batch",
                      "the angle phi in radians"):
        models = [s for s in screen.screen("", ambiguous).signals if s.group == "model"]
        assert models == [], ambiguous

def carries_screening_vocabulary(name):
    field = screen.haystack(name, "")
    return any(screen.terms_in(field, group, rules.patterns.get(key))
               for rules in screen.RULESETS for key, group in rules.groups.items())

def test_a_name_the_registry_happens_to_hold_does_not_lift_a_paper():
    plain = screen.screen("spatial statistics", "we evaluate on a corpus here")
    lifted = [entity["name"] for entity in database.load_registries().values()
              if entity.get("name") and not carries_screening_vocabulary(entity["name"])
              and screen.screen("spatial statistics",
                                "we evaluate on %s here" % entity["name"]).score != plain.score]
    assert lifted == []

def test_a_term_only_one_reviewer_uses_does_not_count():
    alone = screen.review_screen(("the paper probes the circuit inside the model",
                                  "the writing is clear", "the experiments are adequate"))
    shared = screen.review_screen(("the paper probes the circuit inside the model",
                                   "the authors probe the circuit carefully",
                                   "a probe of the circuit, well executed"))
    assert alone.points("r-xai") == 0.0
    assert shared.points("r-xai") > 0.0

def test_one_text_agrees_with_itself():
    single = screen.review_screen(("the paper probes the circuit inside the model",))
    assert single.points("r-xai") > 0.0

def test_the_two_sides_of_a_total_can_still_be_read_apart():
    total = screen.combine(screen.screen(*AUDIT), screen.review_screen(AUDIT_REVIEWS))
    assert screen.side_score(total.subscores, screen.ABSTRACT) == screen.screen(*AUDIT).score
    assert screen.side_score(total.subscores, screen.REVIEW) == \
        screen.review_screen(AUDIT_REVIEWS).score
    assert total.score == round(screen.side_score(total.subscores, screen.ABSTRACT)
                                + screen.side_score(total.subscores, screen.REVIEW), 2)

def test_no_review_leaves_the_review_half_at_zero_rather_than_guessing_it():
    assert screen.review_screen(()).score == 0.0
    assert screen.combine(screen.screen(*AUDIT), screen.review_screen(())).score == \
        screen.screen(*AUDIT).score

def test_harvest_manifest_row_carries_the_screening():
    row = screened_row()
    assert row["id"] == "aBcD"
    assert row["tier"] == screen.POSSIBLE
    assert row["pdf"].endswith("aBcD")
    assert not row["has_pdf"]
    assert any(signal.startswith("model:") for signal in row["signals"])
    assert row["subscores"]["model"] == 2.0

def test_the_screening_rules_version_changes_only_when_a_tuning_knob_changes():
    knobs = {"strong_at": 4.0}
    first = screen.fingerprint(screen.RULESETS, knobs)
    assert first == screen.fingerprint(screen.RULESETS, dict(knobs))
    assert first != screen.fingerprint(screen.RULESETS, {"strong_at": 3.5})
    widened = screen.ruleset(screen.ABSTRACT.name, dict(
        screen.ABSTRACT.groups,
        xai=screen.Group(2.0, 2, screen.XAI.stems + ("newly added term",), screen.XAI.words)))
    assert first != screen.fingerprint((widened, screen.REVIEW), knobs)
    assert len(screen.RULES_VERSION) == screen.VERSION_LENGTH

def test_the_rules_version_also_moves_when_only_the_review_side_changes():
    knobs = {"strong_at": screen.STRONG_AT}
    widened = screen.ruleset(screen.REVIEW.name, dict(
        screen.REVIEW.groups,
        **{"r-analysis": screen.Group(1.0, 4, screen.ANALYSIS.stems + ("newly added",), ())}))
    assert screen.fingerprint(screen.RULESETS, knobs) != \
        screen.fingerprint((screen.ABSTRACT, widened), knobs)
