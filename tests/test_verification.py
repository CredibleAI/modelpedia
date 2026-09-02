import io
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

import yaml

from modelpedia.commands import verify
from modelpedia.build import database
from modelpedia import graph as graph_json
from modelpedia.ingest import anchors
from modelpedia.ingest import citations
from modelpedia.ingest import text
from modelpedia.ingest import verification


FINDING = {
    "id": "XX-001",
    "key_metric": "rho = -0.32, accuracy 0.90 across 12 setups",
    "caveat": "The comparison metric favours subtraction by construction.",
    "models": [{"ref": "model:thing"}],
    "concepts": [{"ref": "concept:idea"}],
    "sources": [{"ref": "source:paper"}],
    "datasets": [{"ref": "dataset:pile", "role": "eval"}],
    "methods": [{"ref": "method:probe", "role": "primary"}],
    "related_work": [{"ref": "method:earlier", "role": "context"}],
}

ENTITIES = {
    "model:thing": {"type": graph_json.MODEL, "name": "Thing"},
    "concept:idea": {"type": graph_json.CONCEPT, "name": "Idea"},
    "source:paper": {"type": graph_json.SOURCE, "title": "The paper"},
    "dataset:pile": {"type": graph_json.DATASET, "name": "The Pile"},
    "method:probe": {"type": graph_json.METHOD, "name": "Linear probing",
                     "anchor": "https://arxiv.org/abs/1610.01644"},
    "method:earlier": {"type": graph_json.METHOD, "name": "Earlier work",
                       "anchor": "https://doi.org/10.1000/example"},
}


def document(raw):
    return text.from_text("source", raw)


def test_numeric_evidence_normalises_decimal_precision():
    doc = document("We report rho = −.32 and accuracy .9 across twelve experiments.")
    checks = verification.number_checks(FINDING, doc)
    by_subject = {check.subject: check for check in checks}
    assert by_subject["-0.32"].state == verification.FOUND
    assert by_subject["0.90"].state == verification.FOUND
    assert by_subject["12"].state == verification.MISSING


def test_a_percent_sign_the_source_omits_is_review_not_missing():
    finding = dict(FINDING, key_metric="accuracy 90%")
    checks = verification.number_checks(finding, document("accuracy was 90"))
    assert checks[0].state == verification.REVIEW
    assert checks[0].pages == (1,)


def test_a_number_absent_in_any_notation_is_still_missing():
    finding = dict(FINDING, key_metric="accuracy 90%")
    checks = verification.number_checks(finding, document("accuracy was 71"))
    assert checks[0].state == verification.MISSING


def test_a_number_with_a_different_claimed_unit_requires_review():
    finding = dict(FINDING, key_metric="24 probe setups")
    checks = verification.number_checks(finding, document("The study ran 24 experiments."))
    assert checks[0].state == verification.REVIEW
    assert checks[0].pages == (1,)


def test_a_non_numeric_metric_is_explicitly_skipped():
    checks = verification.number_checks(dict(FINDING, key_metric=None), document("anything"))
    assert checks == [verification.Check("key_metric", verification.SKIPPED, (),
                                         "no numeric key metric")]


def test_entity_mentions_are_located_by_page():
    doc = document("Thing uses Linear probing.\fThe Pile appears here.")
    checks = {check.subject: check
              for check in verification.entity_checks(FINDING, ENTITIES, doc)}
    assert checks["model:thing"].pages == (1,)
    assert checks["dataset:pile"].pages == (2,)
    assert checks["method:earlier"].state == verification.REVIEW
    assert "concept:idea" not in checks
    assert "source:paper" not in checks


def test_stable_anchor_identifiers_are_checked_in_the_source():
    doc = document("References include arXiv:1610.01644.\fDOI 10.1000/example")
    checks = {check.subject: check
              for check in verification.anchor_checks(FINDING, ENTITIES, doc)}
    assert checks["method:probe"].pages == (1,)
    assert checks["method:earlier"].pages == (2,)


def test_caveat_locator_requires_human_judgment():
    doc = document("Limitations. The metric favours subtraction by construction.")
    check = verification.caveat_check(FINDING, doc)
    assert check.state == verification.LOCATED
    assert check.pages == (1,)
    assert "human judgment required" in check.detail


def test_a_caveat_the_source_does_not_discuss_is_not_reported_as_located():
    doc = document("Bananas grow in tropical climates and ripen after picking.")
    check = verification.caveat_check(FINDING, doc)
    assert check.state == verification.MISSING
    assert check.pages == ()


def test_a_caveat_with_some_shared_terms_stays_for_review_rather_than_located():
    finding = dict(FINDING, caveat="The comparison metric favours subtraction by construction.")
    doc = document("The metric section lists eleven alternatives and their construction.")
    check = verification.caveat_check(finding, doc)
    assert check.state == verification.REVIEW
    assert check.pages == (1,)


def test_an_absent_caveat_is_skipped_not_reported_missing():
    check = verification.caveat_check(dict(FINDING, caveat=None), document("anything at all"))
    assert check.state == verification.SKIPPED


def test_the_summary_counts_every_blocking_check_not_only_the_numeric_ones():
    finding = dict(FINDING, key_metric=None, caveat=None,
                   methods=[{"ref": "method:absent-from-the-registry", "role": "primary"}])
    report = verification.run(finding, ENTITIES, document("nothing relevant here"))
    blocked = verification.blocking(report)
    assert [check.subject for check in blocked] == ["method:absent-from-the-registry"]
    assert "1 blocking check(s)" in verification.render("XX-001", report)


def test_the_verifier_exits_non_zero_when_a_check_is_blocking():
    code, output = run_verify(FINDING, "unrelated source text")
    assert code == 1
    assert "blocking check(s)" in output


def test_the_verifier_exits_zero_only_when_something_was_actually_checked():
    source = "Thing uses Linear probing on The Pile. rho = -0.32, accuracy 0.90 in 12 setups."
    finding = dict(FINDING, caveat=None)
    code, output = run_verify(finding, source)
    assert code == 0
    assert "0 blocking check(s)" in output
    assert "not a pass" not in output


def test_a_record_with_nothing_to_check_never_reports_success():
    code, output = run_verify({"id": "XX-002"}, "unrelated source text")
    assert code == 1
    assert "0 blocking check(s)" in output
    assert "offered nothing to check against the source, so 0 blocking is not a pass" in output


def test_an_empty_list_is_rejected_rather_than_read_as_an_empty_record():
    code, output = run_verify([], "unrelated source text")
    assert code == 1
    assert "is not a mapping" in output


def test_an_empty_file_is_a_record_with_nothing_to_check():
    code, output = run_verify(None, "unrelated source text")
    assert code == 1
    assert "not a pass" in output


def test_a_report_with_one_real_check_is_not_called_empty():
    report = verification.run(dict(FINDING, key_metric=None, caveat=None),
                              ENTITIES, document("Thing appears here."))
    assert not verification.nothing_verified(report)


def test_a_report_where_every_check_was_skipped_is_called_empty():
    report = verification.run({"id": "XX-002"}, ENTITIES, document("anything"))
    assert verification.nothing_verified(report)


def run_verify(finding, raw):
    original_document, original_registries = text.document, database.load_registries
    text.document = lambda path: document(raw)
    database.load_registries = lambda: ENTITIES
    try:
        with tempfile.TemporaryDirectory() as directory:
            finding_path = Path(directory) / "XX-001.yaml"
            finding_path.write_text(yaml.safe_dump(finding), encoding="utf-8")
            source_path = Path(directory) / "source.pdf"
            source_path.write_bytes(b"%PDF-1.7")
            stream = io.StringIO()
            with redirect_stdout(stream):
                code = verify.main(["verify.py", str(finding_path), str(source_path)])
            return code, stream.getvalue()
    finally:
        text.document = original_document
        database.load_registries = original_registries


def test_missing_evidence_blocks_the_cli_but_never_changes_data():
    report = verification.run(FINDING, ENTITIES, document("unrelated source text"))
    assert verification.blocking(report)
    rendered = verification.render("XX-001", report)
    assert "never promotes a record" in rendered


def test_repeated_model_variants_check_the_model_once_and_each_variant_once():
    finding = dict(FINDING, models=[
        {"ref": "model:thing", "variant": "variant:small"},
        {"ref": "model:thing", "variant": "variant:large"},
    ])
    entities = dict(ENTITIES,
                    **{"variant:small": {"type": graph_json.VARIANT, "name": "Thing small"},
                       "variant:large": {"type": graph_json.VARIANT, "name": "Thing large"}})
    checks = verification.entity_checks(
        finding, entities, document("Thing small and Thing large were evaluated."))
    assert [check.subject for check in checks] == [
        "model:thing", "variant:small", "variant:large", "dataset:pile",
        "method:probe", "method:earlier",
    ]


def test_a_url_split_across_a_line_break_is_rejoined():
    assert list(text.urls_in("meta. llama 3.1. https://ai.meta.com/ blog/meta-llama-3-1/ (2024)")) \
        == ["https://ai.meta.com/blog/meta-llama-3-1/"]
    assert list(text.urls_in("bavishi et al. 2023. url https://www.adept. ai/blog/fuyu-8b")) \
        == ["https://www.adept.ai/blog/fuyu-8b"]


def test_prose_after_a_finished_url_is_not_swallowed_into_it():
    assert list(text.urls_in("google. gemini pro vision. https://ai.google.dev. multimodal model.")) \
        == ["https://ai.google.dev"]


def test_a_url_the_extractor_cut_short_is_not_usable():
    assert text.usable_url("https://openreview.net/forum?id=") == ""
    assert text.usable_url("https://openreview.net/forum?id=F76bwRSLeK") \
        == "https://openreview.net/forum?id=F76bwRSLeK"


def test_a_path_gets_its_case_back_from_the_untouched_source_text():
    packed = text.squeezed("see https://www.alignmentforum.org/posts/AcKRB8wDpdaN6v6ru/logit-lens")
    assert text.with_source_case(
        "https://www.alignmentforum.org/posts/ackrb8wdpdan6v6ru/logit-lens", packed) \
        == "https://www.alignmentforum.org/posts/AcKRB8wDpdaN6v6ru/logit-lens"


def test_case_recovery_leaves_a_url_the_source_does_not_carry_alone():
    assert text.with_source_case("https://example.org/nope", text.squeezed("nothing here")) \
        == "https://example.org/nope"


def test_an_identifier_still_wins_over_a_bare_url_in_the_same_citation():
    citation = "A. Author. A paper. arXiv:2301.00001, 2023. url https://example.org/blog/a-paper"
    assert citations.anchor_from(citation) == "https://arxiv.org/abs/2301.00001"


def test_a_citation_with_no_identifier_falls_back_to_its_own_url():
    citation = "meta. introducing llama 3, 2024. url https://ai.meta.com/blog/meta-llama-3/."
    assert citations.anchor_from(citation) == "https://ai.meta.com/blog/meta-llama-3/"


def test_a_citation_with_neither_yields_no_anchor():
    assert citations.anchor_from("ian goodfellow. explaining adversarial examples. iclr, 2015.") == ""


def test_the_title_guess_may_be_sloppy_because_the_score_is_what_decides():
    citation = ("shaoqing ren, kaiming he, and jian sun. faster r-cnn: towards real-time object"
                " detection. in neurips, 2015.")
    assert anchors.queries(citation)[0].startswith("faster r-cnn")
    assert anchors.match_score("Faster R-CNN: Towards Real-Time Object Detection", citation) == 1.0
    assert anchors.match_score("Attention Is All You Need Somewhere", citation) \
        < anchors.DBLP_MATCH_AT


def test_a_title_too_thin_to_say_anything_scores_zero_rather_than_one():
    citation = ("geoffrey hinton, oriol vinyals, and jeff dean. distilling the knowledge"
                " in a neural network. nips workshop, 2015.")
    assert anchors.match_score("Geoffrey E. Hinton", citation) == 0.0
    assert anchors.match_score("Distilling the Knowledge in a Neural Network", citation) == 1.0


def test_a_doi_is_preferred_over_a_preprint_when_both_are_offered():
    assert anchors.url_from(["http://arxiv.org/abs/1506.01497",
                             "https://doi.org/10.1109/TPAMI.2016.2577031"]) \
        == "https://doi.org/10.1109/TPAMI.2016.2577031"
    assert anchors.url_from(["http://arxiv.org/abs/1506.01497"]) == "https://arxiv.org/abs/1506.01497"
    assert anchors.url_from([]) == ""


def test_crossref_is_asked_with_the_whole_reference_not_a_title_guess():
    citation = ("edmond awad, sohan dsouza, and iyad rahwan. the moral machine experiment."
                " nature, 563(7729):59, 2018.")
    query = anchors.bibliographic(citation)
    assert "awad" in query and "moral machine experiment" in query and "nature" in query


def test_a_doi_becomes_a_resolver_url_and_an_empty_one_stays_empty():
    assert anchors.doi_url("10.1038/s41586-018-0637-6") == "https://doi.org/10.1038/s41586-018-0637-6"
    assert anchors.doi_url("") == ""
    assert anchors.doi_url(None) == ""
