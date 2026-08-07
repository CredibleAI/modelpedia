import io
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

import yaml

import verify
from modelpedia.build import database
from modelpedia import graph as graph_json
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
    "related_work": [{"ref": "rw:earlier", "role": "context"}],
}

ENTITIES = {
    "model:thing": {"type": graph_json.MODEL, "name": "Thing"},
    "concept:idea": {"type": graph_json.CONCEPT, "name": "Idea"},
    "source:paper": {"type": graph_json.SOURCE, "title": "The paper"},
    "dataset:pile": {"type": graph_json.DATASET, "name": "The Pile"},
    "method:probe": {"type": graph_json.METHOD, "name": "Linear probing",
                     "anchor": "https://arxiv.org/abs/1610.01644"},
    "rw:earlier": {"type": graph_json.RELATED_WORK, "name": "Earlier work",
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
    assert checks["rw:earlier"].state == verification.REVIEW
    assert "concept:idea" not in checks
    assert "source:paper" not in checks


def test_stable_anchor_identifiers_are_checked_in_the_source():
    doc = document("References include arXiv:1610.01644.\fDOI 10.1000/example")
    checks = {check.subject: check
              for check in verification.anchor_checks(FINDING, ENTITIES, doc)}
    assert checks["method:probe"].pages == (1,)
    assert checks["rw:earlier"].pages == (2,)


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
        "method:probe", "rw:earlier",
    ]
