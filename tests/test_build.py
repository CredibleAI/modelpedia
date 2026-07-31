import copy
import io
import tempfile
from pathlib import Path
from contextlib import redirect_stdout

import build
from modelpedia import graph as graph_json
import yaml

VOCABULARIES = {
    graph_json.FINDING: {
        "evidence_type": ["observational", "correlational", "interventional"],
        "review_status": ["draft", "verified"],
        "extracted_by": ["manual", "automatic-extraction"],
    },
    graph_json.MODEL: {
        "modality": ["image", "text"],
        "domain": ["geospatial"],
        "task": ["generative"],
    },
    build.ROLE_SCOPE: {
        "datasets": ["train", "eval"],
        "methods": ["primary"],
        "related_work": ["builds-on"],
    },
}

ENTITIES = {
    "model:thing": {"type": graph_json.MODEL, "name": "Thing", "modality": ["image"],
                    "variants": {"variant:thing-small": {"name": "Thing small"}}},
    "variant:thing-small": {"type": graph_json.VARIANT, "name": "Thing small",
                            "parent": "model:thing"},
    "concept:idea": {"type": graph_json.CONCEPT, "name": "Idea"},
    "method:probe": {"type": graph_json.METHOD, "name": "Probe", "anchor": "https://example.org"},
    "dataset:pile": {"type": graph_json.DATASET, "name": "Pile", "anchor": "https://example.org"},
    "rw:earlier": {"type": graph_json.RELATED_WORK, "name": "Earlier work"},
    "person:ada-lovelace": {"type": graph_json.PERSON, "name": "Ada Lovelace"},
    "source:the-paper": {"type": graph_json.SOURCE, "name": "The paper", "date": "2026-01",
                         "authors": [{"ref": "person:ada-lovelace"}]},
}

FINDING = {
    "id": "XX-001",
    "review_status": "verified",
    "extracted_by": "manual",
    "title": "A title",
    "description": "A description.",
    "evidence_type": "observational",
    "concepts": [{"ref": "concept:idea"}],
    "models": [{"ref": "model:thing", "variant": "variant:thing-small"}],
    "sources": [{"ref": "source:the-paper"}],
    "datasets": [{"ref": "dataset:pile", "role": "eval"}],
    "methods": [{"ref": "method:probe", "role": "primary"}],
    "related_work": [{"ref": "rw:earlier", "role": "builds-on"}],
    "related_findings": [],
}


def database(**changes):
    db = build.Database(vocabularies=copy.deepcopy(VOCABULARIES),
                        entities=copy.deepcopy(ENTITIES),
                        findings={"XX-001": copy.deepcopy(FINDING)})
    for key, mutate in changes.items():
        mutate(getattr(db, key))
    return db


def only_error(db):
    errors = build.validate(db)
    assert len(errors) == 1, errors
    return errors[0]


def test_the_fixture_is_valid():
    assert build.validate(database()) == []


def test_the_real_data_is_valid():
    assert build.validate(build.load()) == []


def test_entity_key_must_be_kebab_case():
    def add_bad_key(entities):
        entities["concept:Bad_Idea"] = {"type": graph_json.CONCEPT, "name": "Bad idea"}
    assert "kebab-case" in only_error(database(entities=add_bad_key))


def test_entity_key_prefix_must_match_its_registry():
    def add_mistyped(entities):
        entities["model:second-probe"] = {"type": graph_json.METHOD, "name": "Second probe"}
    assert "must start with method:" in only_error(database(entities=add_mistyped))


def test_date_must_be_a_quoted_iso_string():
    def break_date(entities):
        entities["source:the-paper"]["date"] = 2026
    assert "quoted ISO string" in only_error(database(entities=break_date))


def test_author_must_be_a_reference():
    def break_author(entities):
        entities["source:the-paper"]["authors"] = ["Ada Lovelace"]
    assert "not a reference" in only_error(database(entities=break_author))


def test_author_must_be_a_person():
    def wrong_registry(entities):
        entities["source:the-paper"]["authors"] = [{"ref": "model:thing"}]
    assert "not a person" in only_error(database(entities=wrong_registry))


def test_author_must_exist():
    def unknown(entities):
        entities["source:the-paper"]["authors"] = [{"ref": "person:nobody"}]
    assert "unknown author" in only_error(database(entities=unknown))


def test_model_facet_must_be_a_list():
    def scalar(entities):
        entities["model:thing"]["modality"] = "image"
    assert "not a list" in only_error(database(entities=scalar))


def test_model_facet_terms_must_be_in_the_vocabulary():
    def unknown(entities):
        entities["model:thing"]["modality"] = ["hologram"]
    assert "unknown modality hologram" in only_error(database(entities=unknown))


def test_missing_vocabulary_scope_is_reported_not_raised():
    def drop(vocabularies):
        del vocabularies[build.ROLE_SCOPE]
    assert "role is missing" in only_error(database(vocabularies=drop))


def test_missing_vocabulary_name_is_reported_not_raised():
    def drop(vocabularies):
        del vocabularies[graph_json.FINDING]["evidence_type"]
    assert "finding.evidence_type is missing" in only_error(database(vocabularies=drop))


def test_vocabulary_terms_must_be_kebab_case():
    def shout(vocabularies):
        vocabularies[build.ROLE_SCOPE]["methods"] = ["Primary"]
    assert "not kebab-case" in only_error(database(vocabularies=shout))


def test_required_field_must_be_present():
    def drop(findings):
        findings["XX-001"]["review_status"] = None
    assert "missing review_status" in only_error(database(findings=drop))


def test_field_outside_the_schema_is_rejected():
    def invent(findings):
        findings["XX-001"]["robustness"] = "high"
    assert "robustness is not a field in the schema" in only_error(database(findings=invent))


def test_closed_field_value_must_be_in_the_vocabulary():
    def invent(findings):
        findings["XX-001"]["evidence_type"] = "anecdotal"
    assert "unknown evidence_type anecdotal" in only_error(database(findings=invent))


def test_link_must_be_a_reference():
    def bare_string(findings):
        findings["XX-001"]["methods"] = ["method:probe"]
    assert "not a reference" in only_error(database(findings=bare_string))


def test_link_must_point_at_the_right_registry():
    def wrong(findings):
        findings["XX-001"]["methods"] = [{"ref": "dataset:pile", "role": "primary"}]
    assert "does not belong in methods" in only_error(database(findings=wrong))


def test_link_prefix_must_be_a_known_registry():
    def invented(findings):
        findings["XX-001"]["related_work"] = [{"ref": "blog:somewhere"}]
    assert "has no registry" in only_error(database(findings=invented))


def test_link_must_resolve_to_an_entity():
    def missing(findings):
        findings["XX-001"]["methods"] = [{"ref": "method:absent", "role": "primary"}]
    assert "not in any registry" in only_error(database(findings=missing))


def test_role_must_be_in_the_vocabulary_for_its_field():
    def borrowed(findings):
        findings["XX-001"]["datasets"] = [{"ref": "dataset:pile", "role": "primary"}]
    assert "unknown role primary" in only_error(database(findings=borrowed))


def test_related_work_accepts_any_registry():
    def any_registry(findings):
        findings["XX-001"]["related_work"] = [{"ref": "method:probe", "role": "builds-on"}]
    assert build.validate(database(findings=any_registry)) == []


def test_variant_must_be_known():
    def invented(findings):
        findings["XX-001"]["models"] = [{"ref": "model:thing", "variant": "variant:huge"}]
    assert "not a known variant" in only_error(database(findings=invented))


def test_variant_must_point_at_a_variant_entity():
    def wrong_type(findings):
        findings["XX-001"]["models"] = [{"ref": "model:thing", "variant": "model:thing"}]
    assert "is not a variant" in only_error(database(findings=wrong_type))


def test_variant_must_belong_to_the_same_model():
    def second_model(entities):
        entities["model:other"] = {"type": graph_json.MODEL, "name": "Other", "variants": {
            "variant:other-small": {"name": "Other small"}}}
        entities["variant:other-small"] = {"type": graph_json.VARIANT, "name": "Other small",
                                           "parent": "model:other"}

    def wrong_parent(findings):
        findings["XX-001"]["models"] = [{"ref": "model:thing", "variant": "variant:other-small"}]
    assert "does not belong to model:thing" in only_error(
        database(entities=second_model, findings=wrong_parent))


def test_link_rejects_unknown_keys():
    def with_extra_key(findings):
        findings["XX-001"]["methods"] = [{"ref": "method:probe", "role": "primary", "note": "x"}]
    assert "unknown keys: note" in only_error(database(findings=with_extra_key))


def test_variant_may_be_recorded_as_not_specified():
    def unspecified(findings):
        findings["XX-001"]["models"] = [
            {"ref": "model:thing", "variant": graph_json.VARIANT_NOT_SPECIFIED}]
    assert build.validate(database(findings=unspecified)) == []


def test_related_finding_must_exist():
    def dangling(findings):
        findings["XX-001"]["related_findings"] = ["XX-999"]
    assert "not a known finding" in only_error(database(findings=dangling))


def test_a_broken_vocabulary_stops_the_other_checks():
    def break_everything(db):
        del db.vocabularies[graph_json.MODEL]
        db.findings["XX-001"]["evidence_type"] = "anecdotal"
    db = database()
    break_everything(db)
    errors = build.validate(db)
    assert errors == ["vocabularies: model is missing or is not a mapping"]


def test_graph_carries_the_format_version():
    assert build.build(database())["format_version"] == graph_json.FORMAT_VERSION


def test_a_variant_link_adds_an_edge_to_the_variant():
    graph = build.build(database())
    variant_edges = [e for e in graph["edges"]
                     if e["type"] == graph_json.EDGE_ABOUT_VARIANT]
    assert [e["target"] for e in variant_edges] == ["variant:thing-small"]


def test_a_person_is_reached_through_the_source_not_the_finding():
    graph = build.build(database())
    reached = graph_json.findings_reaching(graph)
    assert reached["person:ada-lovelace"] == {"XX-001"}
    assert not [e for e in graph["edges"]
                if e["source"] == "XX-001" and e["target"].startswith("person:")]


def test_add_entity_rejects_duplicate_identifier():
    entities = {}
    build.add_entity(entities, "method:probe", {"type": graph_json.METHOD})
    try:
        build.add_entity(entities, "method:probe", {"type": graph_json.METHOD})
    except ValueError as error:
        assert "duplicated identifier method:probe" in str(error)
        return
    raise AssertionError("add_entity accepted duplicated identifier")


def test_load_findings_rejects_filename_id_mismatch():
    with tempfile.TemporaryDirectory() as directory:
        findings_dir = Path(directory)
        (findings_dir / "XX-001.yaml").write_text('id: "XX-002"\n', encoding="utf-8")
        original = build.FINDINGS
        build.FINDINGS = findings_dir
        try:
            try:
                build.load_findings()
            except ValueError as error:
                assert "must contain id XX-001" in str(error)
                return
            raise AssertionError("load_findings accepted mismatched id")
        finally:
            build.FINDINGS = original


def test_main_reports_load_errors_without_traceback():
    original = build.load

    def broken_load():
        raise ValueError("registry: duplicated identifier method:probe")

    build.load = broken_load
    try:
        stream = io.StringIO()
        with redirect_stdout(stream):
            code = build.main()
    finally:
        build.load = original
    assert code == 1
    assert "ERROR registry: duplicated identifier method:probe" in stream.getvalue()


def test_main_reports_yaml_errors_without_traceback():
    original = build.load

    def broken_load():
        raise yaml.YAMLError("invalid yaml document")

    build.load = broken_load
    try:
        stream = io.StringIO()
        with redirect_stdout(stream):
            code = build.main()
    finally:
        build.load = original
    assert code == 1
    assert "ERROR invalid yaml document" in stream.getvalue()
