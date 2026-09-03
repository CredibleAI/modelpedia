import copy
import tempfile
from pathlib import Path

from modelpedia.commands import check
from modelpedia import atomic
from modelpedia.build import database
from modelpedia import paths
from modelpedia import graph as graph_json
from modelpedia.ingest import link
from modelpedia.ingest import prompt
from modelpedia.ingest import report
from modelpedia.build import validate
from tests.helpers import candidate, index_of, sample_db


def test_punctuation_and_accents_do_not_block_a_match():
    def accented(entities):
        entities["method:koppen"] = {"type": graph_json.METHOD, "name": "Köppen-Geiger",
                                     "anchor": "https://example.org"}
    index = index_of(graph_json.METHOD, entities=accented)
    assert link.resolve("Koppen Geiger", index).slug == "method:koppen"

def test_an_alias_after_a_spaced_slash_is_searchable():
    def aliased(entities):
        entities["method:shap"] = {"type": graph_json.METHOD, "name": "Shapley values / SHAP",
                                   "anchor": "https://example.org"}
    index = index_of(graph_json.METHOD, entities=aliased)
    assert link.resolve("SHAP", index).slug == "method:shap"

def test_a_slash_inside_a_name_is_not_an_alias_separator():
    def variants(entities):
        entities["method:b16"] = {"type": graph_json.METHOD, "name": "ViT-B/16",
                                  "anchor": "https://example.org"}
        entities["method:b32"] = {"type": graph_json.METHOD, "name": "ViT-B/32",
                                  "anchor": "https://example.org"}
    index = index_of(graph_json.METHOD, entities=variants)
    assert link.resolve("ViT-B/16", index).slug == "method:b16"
    assert link.resolve("ViT-B/32", index).slug == "method:b32"

def test_check_reports_no_new_errors_for_a_valid_candidate():
    db = sample_db()
    fid, errors = check.schema_errors(candidate(), db)
    assert (fid, errors) == ("XX-999", [])

def test_check_blames_only_the_candidate_for_errors():
    def break_registry(entities):
        entities["method:probe"]["date"] = 2026
    db = sample_db(entities=break_registry)
    _, errors = check.schema_errors(candidate(), db)
    assert errors == []

def test_check_reports_an_unknown_role_on_the_candidate():
    _, errors = check.schema_errors(
        candidate(methods=[{"ref": "method:probe", "role": "invented"}]), sample_db())
    assert errors == ["XX-999: unknown role invented on method:probe"]

def test_check_rejects_an_id_that_already_exists():
    record = copy.deepcopy(database.load().findings["TM-001"])
    fid, errors = check.schema_errors(record, database.load())
    assert fid == "TM-001"
    assert errors == ["TM-001: id already exists in data/findings"]

def test_check_reports_a_non_string_id_instead_of_crashing():
    fid, errors = check.schema_errors(candidate(id=["bad"]), sample_db())
    assert fid == "CANDIDATE"
    assert errors == ["CANDIDATE: id is not a string"]

def test_check_resolves_a_free_text_reference_to_a_slug():
    rows = check.resolve_links(candidate(methods=[{"ref": "Probe", "role": "primary"}]),
                               sample_db())
    assert ("methods", "Probe", check.RESOLVED, "method:probe", "matched by name") in rows

def test_check_proposes_a_new_entity_when_nothing_matches():
    rows = check.resolve_links(
        candidate(methods=[{"ref": "method:fisher-vectors", "role": "primary"}]), sample_db())
    states = {ref: state for _, ref, state, _, _ in rows}
    assert states["method:fisher-vectors"] == check.PROPOSED

def test_check_leaves_a_near_match_undecided():
    rows = check.resolve_links(candidate(methods=[{"ref": "Probes", "role": "primary"}]),
                               sample_db())
    states = {ref: state for _, ref, state, _, _ in rows}
    assert states["Probes"] == check.UNDECIDED

def test_check_does_not_call_an_entity_resolved_in_the_wrong_field():
    rows = check.resolve_links(candidate(methods=[{"ref": "dataset:pile", "role": "primary"}]),
                               sample_db())
    states = {ref: state for _, ref, state, _, _ in rows}
    assert states["dataset:pile"] == check.UNDECIDED

def test_report_blocks_are_separated_by_exactly_one_blank_line():
    assert report.render([["a"], [], ["b", "c"], []]) == "a\n\nb\nc"
    assert report.render([[], ["only"]]) == "only"
    assert report.render([[], []]) == ""

def test_the_citation_report_derives_its_total_and_names_the_rejected_state():
    tally = {"confirmed": 3, "partial": 1, "rejected": 2, "absent": 4}
    lines = report.for_citations(tally, tuple(tally), "rejected", "out.jsonl").split("\n")
    assert lines[-1] == "10 entities, 2 rejected, report in out.jsonl"
    assert lines[0].split() == ["confirmed", "3", "30%"]
    assert [line.split()[0] for line in lines[:4]] == list(tally)

def test_an_atomic_write_stages_beside_the_target_without_losing_its_extension():
    with tempfile.TemporaryDirectory() as directory:
        target = Path(directory) / "paper.pdf"
        assert atomic.staging_path(target).name == "paper.pdf.part"
        atomic.write_text(Path(directory) / "paper.txt", "body")
        assert atomic.staging_path(Path(directory) / "paper.txt").name == "paper.txt.part"
        assert list(Path(directory).glob("*" + paths.PARTIAL)) == []

def test_a_pdf_is_written_atomically():
    with tempfile.TemporaryDirectory() as folder:
        target = Path(folder) / "paper.pdf"
        atomic.write_bytes(target, b"%PDF-1.7 body")
        assert target.read_bytes() == b"%PDF-1.7 body"
        assert list(Path(folder).glob("*" + paths.PARTIAL)) == []

def test_an_interrupted_write_is_cleared_before_the_next_run():
    with tempfile.TemporaryDirectory() as folder:
        leftover = Path(folder) / ("half" + paths.PARTIAL)
        leftover.write_bytes(b"%PDF truncated")
        atomic.clear_partials(Path(folder))
        assert not leftover.exists()

def test_check_accepts_a_baseline_so_a_batch_validates_once():
    db = sample_db()
    baseline = set(validate.errors(db))
    assert check.schema_errors(candidate(), db, baseline) == ("XX-999", [])

def test_a_prompt_carries_the_paper_and_the_closed_concept_list():
    concepts = {"concept:shortcut": {"name": "Shortcut", "description": "relies on a proxy"}}
    body, truncated = prompt.build("A title", "Body text of the paper.", concepts, [], {})
    assert not truncated
    assert "concept:shortcut" in body and "relies on a proxy" in body
    assert "A title" in body
    assert "body text of the paper." in body

def test_a_long_paper_is_clipped_from_the_middle_and_says_so():
    body, truncated = prompt.build("T", "x" * 5000, {}, [], {}, limit=1000)
    assert truncated
    assert prompt.OMITTED.strip() in body

def test_worked_examples_show_names_and_never_registry_identifiers():
    record = {"id": "TM-001", "title": "t", "description": "d", "key_metric": "", "caveat": "",
              "models": [{"ref": "model:terramind", "variant": "variant:terramind-v1-tiny"}],
              "methods": [{"ref": "method:probing-classifiers", "role": "primary"}],
              "concepts": [{"ref": "concept:shortcut"}]}
    names = {"model:terramind": "TerraMind", "method:probing-classifiers": "Probing classifiers",
             "variant:terramind-v1-tiny": "TerraMind 1.0 tiny"}
    shown = prompt.as_example(record, names)
    assert "TerraMind" in shown and "Probing classifiers" in shown
    assert "model:terramind" not in shown and "method:probing-classifiers" not in shown
    assert "concept:shortcut" in shown
    assert "TM-001" not in shown

def test_a_registry_name_carrying_a_qualifier_is_found_by_its_bare_form():
    def qualified(entities):
        entities["method:mds"] = {"type": graph_json.METHOD,
                                  "name": "MDS (Mahalanobis Distance-based Score)",
                                  "anchor": "https://example.org"}
    index = index_of(graph_json.METHOD, entities=qualified)
    assert link.resolve("MDS", index).slug == "method:mds"

def test_facet_values_outside_the_closed_list_are_refused():
    from modelpedia.ingest import facets
    allowed = {"modality": ["text", "image"], "task": ["generative"], "domain": ["medical"]}
    taken, refused = facets.chosen({"modality": ["text", "smell"], "task": "generative",
                                    "domain": []}, allowed)
    assert taken == {"modality": ["text"], "task": ["generative"]}
    assert refused == [("modality", "smell")]

def test_a_model_that_already_carries_a_facet_is_left_alone():
    from modelpedia.ingest import facets
    entities = {"model:a": {"type": "model", "name": "A"},
                "model:b": {"type": "model", "name": "B", "modality": ["text"]},
                "dataset:c": {"type": "dataset", "name": "C"}}
    assert list(facets.wanted(entities)) == ["model:a"]
