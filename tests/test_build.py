import copy
import io
import tempfile
from pathlib import Path
from contextlib import redirect_stdout

from modelpedia.build import assemble
from modelpedia.build import database
from modelpedia import paths
from modelpedia import graph as graph_json
from modelpedia import schema
from modelpedia.build import validate
import build
import yaml

VOCABULARIES = {
    graph_json.FINDING: {
        "evidence_type": ["observational", "correlational", "interventional"],
        "review_status": ["draft", "verified"],
        "extracted_by": ["manual-extraction", "automatic-extraction"],
    },
    graph_json.MODEL: {
        "modality": ["image", "text"],
        "domain": ["geospatial"],
        "task": ["generative"],
    },
    schema.ROLE_SCOPE: {
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
    "source:the-paper": {"type": graph_json.SOURCE, "name": "The paper", "date": "2026-01",
                         "authors": ["Ada Lovelace"]},
}

FINDING = {
    "id": "XX-001",
    "review_status": "verified",
    "extracted_by": "manual-extraction",
    "title": "A title",
    "description": "A description.",
    "evidence_type": "observational",
    "concepts": [{"ref": "concept:idea"}],
    "models": [{"ref": "model:thing", "variant": "variant:thing-small"}],
    "sources": [{"ref": "source:the-paper"}],
    "datasets": [{"ref": "dataset:pile", "role": "eval"}],
    "methods": [{"ref": "method:probe", "role": "primary"}],
    "related_work": [{"name": "Earlier work", "anchor": "https://example.org/earlier",
                      "role": "builds-on"}],
    "related_findings": [],
}


def sample_db(**changes):
    db = database.Database(vocabularies=copy.deepcopy(VOCABULARIES),
                        entities=copy.deepcopy(ENTITIES),
                        findings={"XX-001": copy.deepcopy(FINDING)})
    for key, mutate in changes.items():
        mutate(getattr(db, key))
    return db


def only_error(db):
    errors = validate.errors(db)
    assert len(errors) == 1, errors
    return errors[0]


def test_the_fixture_is_valid():
    assert validate.errors(sample_db()) == []


def test_the_real_data_is_valid():
    assert validate.errors(database.load()) == []


def test_entity_key_must_be_kebab_case():
    def add_bad_key(entities):
        entities["concept:Bad_Idea"] = {"type": graph_json.CONCEPT, "name": "Bad idea"}
    assert "kebab-case" in only_error(sample_db(entities=add_bad_key))


def test_entity_identifier_must_be_a_string():
    def add_number(entities):
        entities[12] = {"type": graph_json.CONCEPT, "name": "Number"}
    assert "identifier 12 is not a string" in only_error(sample_db(entities=add_number))


def test_entity_key_prefix_must_match_its_registry():
    def add_mistyped(entities):
        entities["model:second-probe"] = {"type": graph_json.METHOD, "name": "Second probe"}
    assert "must start with method:" in only_error(sample_db(entities=add_mistyped))


def test_date_must_be_a_quoted_iso_string():
    def break_date(entities):
        entities["source:the-paper"]["date"] = 2026
    assert "quoted ISO string" in only_error(sample_db(entities=break_date))


def test_author_must_be_a_name_not_a_reference():
    def break_author(entities):
        entities["source:the-paper"]["authors"] = [{"ref": "person:ada-lovelace"}]
    assert "author that is not a name" in only_error(sample_db(entities=break_author))


def test_an_empty_author_name_is_reported():
    def blank(entities):
        entities["source:the-paper"]["authors"] = ["   "]
    assert "author that is not a name" in only_error(sample_db(entities=blank))


def test_model_facet_must_be_a_list():
    def scalar(entities):
        entities["model:thing"]["modality"] = "image"
    assert "not a list" in only_error(sample_db(entities=scalar))


def test_model_facet_terms_must_be_in_the_vocabulary():
    def unknown(entities):
        entities["model:thing"]["modality"] = ["hologram"]
    assert "unknown modality hologram" in only_error(sample_db(entities=unknown))


def test_missing_vocabulary_scope_is_reported_not_raised():
    def drop(vocabularies):
        del vocabularies[schema.ROLE_SCOPE]
    assert "role is missing" in only_error(sample_db(vocabularies=drop))


def test_missing_vocabulary_name_is_reported_not_raised():
    def drop(vocabularies):
        del vocabularies[graph_json.FINDING]["evidence_type"]
    assert "finding.evidence_type is missing" in only_error(sample_db(vocabularies=drop))


def test_vocabulary_terms_must_be_kebab_case():
    def shout(vocabularies):
        vocabularies[schema.ROLE_SCOPE]["methods"] = ["Primary"]
    assert "not kebab-case" in only_error(sample_db(vocabularies=shout))


def test_required_field_must_be_present():
    def drop(findings):
        findings["XX-001"]["review_status"] = None
    assert "missing review_status" in only_error(sample_db(findings=drop))


def test_concepts_may_be_an_explicit_empty_list():
    def empty(findings):
        findings["XX-001"]["concepts"] = []
    assert validate.errors(sample_db(findings=empty)) == []


def test_concepts_may_not_be_null():
    def empty(findings):
        findings["XX-001"]["concepts"] = None
    assert "missing concepts" in only_error(sample_db(findings=empty))


def test_field_outside_the_schema_is_rejected():
    def invent(findings):
        findings["XX-001"]["robustness"] = "high"
    assert "robustness is not a field in the schema" in only_error(sample_db(findings=invent))


def test_closed_field_value_must_be_in_the_vocabulary():
    def invent(findings):
        findings["XX-001"]["evidence_type"] = "anecdotal"
    assert "unknown evidence_type anecdotal" in only_error(sample_db(findings=invent))


def test_link_must_be_a_reference():
    def bare_string(findings):
        findings["XX-001"]["methods"] = ["method:probe"]
    assert "not a mapping" in only_error(sample_db(findings=bare_string))


def test_a_field_that_is_not_related_work_still_demands_a_reference():
    def inline(findings):
        findings["XX-001"]["methods"] = [{"name": "Some probe"}]
    assert "not a reference" in only_error(sample_db(findings=inline))


def test_link_field_must_be_a_list():
    def scalar(findings):
        findings["XX-001"]["methods"] = "method:probe"
    assert "methods is not a list" in only_error(sample_db(findings=scalar))


def test_reference_value_must_be_a_string():
    def number(findings):
        findings["XX-001"]["methods"] = [{"ref": 12, "role": "primary"}]
    assert "is not a string reference" in only_error(sample_db(findings=number))


def test_link_must_point_at_the_right_registry():
    def wrong(findings):
        findings["XX-001"]["methods"] = [{"ref": "dataset:pile", "role": "primary"}]
    assert "does not belong in methods" in only_error(sample_db(findings=wrong))


def test_link_prefix_must_be_a_known_registry():
    def invented(findings):
        findings["XX-001"]["related_work"] = [{"ref": "blog:somewhere"}]
    assert "has no registry" in only_error(sample_db(findings=invented))


def test_link_must_resolve_to_an_entity():
    def missing(findings):
        findings["XX-001"]["methods"] = [{"ref": "method:absent", "role": "primary"}]
    assert "not in any registry" in only_error(sample_db(findings=missing))


def test_role_must_be_in_the_vocabulary_for_its_field():
    def borrowed(findings):
        findings["XX-001"]["datasets"] = [{"ref": "dataset:pile", "role": "primary"}]
    assert "unknown role primary" in only_error(sample_db(findings=borrowed))


def test_role_must_be_a_string():
    def list_role(findings):
        findings["XX-001"]["methods"] = [{"ref": "method:probe", "role": ["primary"]}]
    assert "role on method:probe is not a non-empty string" in only_error(
        sample_db(findings=list_role))


def test_an_empty_role_is_rejected_rather_than_silently_ignored():
    def empty_role(findings):
        findings["XX-001"]["methods"] = [{"ref": "method:probe", "role": []}]
    assert "role on method:probe is not a non-empty string" in only_error(
        sample_db(findings=empty_role))


def test_an_absent_role_stays_valid():
    def no_role(findings):
        findings["XX-001"]["methods"] = [{"ref": "method:probe"}]
    assert validate.errors(sample_db(findings=no_role)) == []


def test_related_work_accepts_any_registry():
    def any_registry(findings):
        findings["XX-001"]["related_work"] = [{"ref": "method:probe", "role": "builds-on"}]
    assert validate.errors(sample_db(findings=any_registry)) == []


def test_variant_must_be_known():
    def invented(findings):
        findings["XX-001"]["models"] = [{"ref": "model:thing", "variant": "variant:huge"}]
    assert "not a known variant" in only_error(sample_db(findings=invented))


def test_variant_must_be_a_string():
    def list_variant(findings):
        findings["XX-001"]["models"] = [{"ref": "model:thing", "variant": ["small"]}]
    assert "variant on model:thing is not a non-empty string" in only_error(
        sample_db(findings=list_variant))


def test_an_empty_variant_is_rejected_rather_than_silently_ignored():
    def empty_variant(findings):
        findings["XX-001"]["models"] = [{"ref": "model:thing", "variant": []}]
    assert "variant on model:thing is not a non-empty string" in only_error(
        sample_db(findings=empty_variant))


def test_a_null_variant_means_no_variant_and_stays_valid():
    def null_variant(findings):
        findings["XX-001"]["models"] = [{"ref": "model:thing", "variant": None}]
    assert validate.errors(sample_db(findings=null_variant)) == []


def test_variant_must_point_at_a_variant_entity():
    def wrong_type(findings):
        findings["XX-001"]["models"] = [{"ref": "model:thing", "variant": "model:thing"}]
    assert "is not a variant" in only_error(sample_db(findings=wrong_type))


def test_variant_must_belong_to_the_same_model():
    def second_model(entities):
        entities["model:other"] = {"type": graph_json.MODEL, "name": "Other", "variants": {
            "variant:other-small": {"name": "Other small"}}}
        entities["variant:other-small"] = {"type": graph_json.VARIANT, "name": "Other small",
                                           "parent": "model:other"}

    def wrong_parent(findings):
        findings["XX-001"]["models"] = [{"ref": "model:thing", "variant": "variant:other-small"}]
    assert "does not belong to model:thing" in only_error(
        sample_db(entities=second_model, findings=wrong_parent))


def test_link_rejects_unknown_keys():
    def with_extra_key(findings):
        findings["XX-001"]["methods"] = [{"ref": "method:probe", "role": "primary", "note": "x"}]
    assert "unknown keys: note" in only_error(sample_db(findings=with_extra_key))


def test_variant_may_be_recorded_as_not_specified():
    def unspecified(findings):
        findings["XX-001"]["models"] = [
            {"ref": "model:thing", "variant": graph_json.VARIANT_NOT_SPECIFIED}]
    assert validate.errors(sample_db(findings=unspecified)) == []


def test_related_finding_must_exist():
    def dangling(findings):
        findings["XX-001"]["related_findings"] = ["XX-999"]
    assert "not a known finding" in only_error(sample_db(findings=dangling))


def test_related_findings_must_be_a_list():
    def scalar(findings):
        findings["XX-001"]["related_findings"] = "XX-002"
    assert "related_findings is not a list" in only_error(sample_db(findings=scalar))


def test_a_related_finding_must_link_back():
    def one_way(findings):
        findings["XX-002"] = dict(copy.deepcopy(FINDING), id="XX-002", related_findings=[])
        findings["XX-001"]["related_findings"] = ["XX-002"]
    assert "XX-002 is listed but does not link back" in only_error(sample_db(findings=one_way))


def test_a_reciprocated_pair_of_related_findings_is_accepted():
    def both_ways(findings):
        findings["XX-002"] = dict(copy.deepcopy(FINDING), id="XX-002",
                                  related_findings=["XX-001"])
        findings["XX-001"]["related_findings"] = ["XX-002"]
    assert validate.errors(sample_db(findings=both_ways)) == []


def test_a_dangling_related_finding_is_reported_once_not_twice():
    def dangling(findings):
        findings["XX-001"]["related_findings"] = ["XX-999"]
    assert "not a known finding" in only_error(sample_db(findings=dangling))


def test_related_finding_id_must_be_a_string():
    def number(findings):
        findings["XX-001"]["related_findings"] = [12]
    assert "is not a string id" in only_error(sample_db(findings=number))


def test_a_broken_vocabulary_stops_the_other_checks():
    def break_everything(db):
        del db.vocabularies[graph_json.MODEL]
        db.findings["XX-001"]["evidence_type"] = "anecdotal"
    db = sample_db()
    break_everything(db)
    errors = validate.errors(db)
    assert errors == ["vocabularies: model is missing or is not a mapping"]


def test_graph_carries_the_format_version():
    assert assemble.graph_from(sample_db())["format_version"] == graph_json.FORMAT_VERSION


def test_a_variant_link_adds_an_edge_to_the_variant():
    graph = assemble.graph_from(sample_db())
    variant_edges = [e for e in graph["edges"]
                     if e["type"] == graph_json.EDGE_ABOUT_VARIANT]
    assert [e["target"] for e in variant_edges] == ["variant:thing-small"]


def test_an_author_is_carried_on_the_source_and_makes_no_node():
    graph = assemble.graph_from(sample_db())
    assert not [n for n in graph["nodes"] if n["id"].startswith("person:")]
    source = [n for n in graph["nodes"] if n["id"] == "source:the-paper"][0]
    assert source["data"]["authors"] == ["Ada Lovelace"]


def test_related_work_from_outside_the_registries_makes_no_node_and_no_edge():
    graph = assemble.graph_from(sample_db())
    assert not [n for n in graph["nodes"] if n["id"].startswith("rw:")]
    assert not [e for e in graph["edges"] if e["type"] == graph_json.EDGE_CITES]
    finding = [n for n in graph["nodes"] if n["id"] == "XX-001"][0]
    assert finding["data"]["related_work"][0]["name"] == "Earlier work"


def test_a_yaml_document_that_is_not_a_mapping_is_reported_not_raised():
    try:
        database.as_mapping(["a", "b"], "models.yaml: the top level")
    except ValueError as error:
        assert "models.yaml: the top level is not a mapping" in str(error)
        return
    raise AssertionError("as_mapping accepted a list")


def test_a_key_declared_twice_in_one_file_is_rejected_not_silently_deduplicated():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "concepts.yaml"
        path.write_text("concept:shortcut:\n  name: First\n"
                        "concept:shortcut:\n  name: Second\n", encoding="utf-8")
        try:
            database.read_yaml(path)
        except yaml.YAMLError as error:
            assert "declared twice" in str(error)
            assert "concept:shortcut" in str(error)
            return
    raise AssertionError("read_yaml silently kept one of two identical keys")


def test_a_key_declared_twice_inside_a_nested_mapping_is_also_rejected():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "models.yaml"
        path.write_text("model:thing:\n  variants:\n"
                        "    variant:small:\n      name: A\n"
                        "    variant:small:\n      name: B\n", encoding="utf-8")
        try:
            database.read_yaml(path)
        except yaml.YAMLError as error:
            assert "variant:small" in str(error)
            return
    raise AssertionError("read_yaml silently kept one of two identical nested keys")


def test_distinct_keys_still_load_normally():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "concepts.yaml"
        path.write_text("concept:one:\n  name: One\nconcept:two:\n  name: Two\n",
                        encoding="utf-8")
        assert sorted(database.read_yaml(path)) == ["concept:one", "concept:two"]


def test_an_empty_yaml_document_reads_as_an_empty_mapping():
    assert database.as_mapping(None, "anything") == {}


def test_main_reports_a_missing_data_file_without_traceback():
    original = database.load

    def broken_load():
        raise FileNotFoundError(2, "No such file or directory", "data/registries/models.yaml")

    database.load = broken_load
    try:
        stream = io.StringIO()
        with redirect_stdout(stream):
            code = build.main()
    finally:
        database.load = original
    assert code == 1
    assert "ERROR" in stream.getvalue()
    assert "models.yaml" in stream.getvalue()


def test_add_entity_rejects_duplicate_identifier():
    entities = {}
    database.add_entity(entities, "method:probe", {"type": graph_json.METHOD})
    try:
        database.add_entity(entities, "method:probe", {"type": graph_json.METHOD})
    except ValueError as error:
        assert "duplicated identifier method:probe" in str(error)
        return
    raise AssertionError("add_entity accepted duplicated identifier")


def test_load_findings_rejects_filename_id_mismatch():
    with tempfile.TemporaryDirectory() as directory:
        findings_dir = Path(directory)
        (findings_dir / "XX-001.yaml").write_text('id: "XX-002"\n', encoding="utf-8")
        original = paths.FINDINGS
        paths.FINDINGS = findings_dir
        try:
            try:
                database.load_findings()
            except ValueError as error:
                assert "must contain id XX-001" in str(error)
                return
            raise AssertionError("load_findings accepted mismatched id")
        finally:
            paths.FINDINGS = original


def test_main_reports_load_errors_without_traceback():
    original = database.load

    def broken_load():
        raise ValueError("registry: duplicated identifier method:probe")

    database.load = broken_load
    try:
        stream = io.StringIO()
        with redirect_stdout(stream):
            code = build.main()
    finally:
        database.load = original
    assert code == 1
    assert "ERROR registry: duplicated identifier method:probe" in stream.getvalue()


def test_main_reports_yaml_errors_without_traceback():
    original = database.load

    def broken_load():
        raise yaml.YAMLError("invalid yaml document")

    database.load = broken_load
    try:
        stream = io.StringIO()
        with redirect_stdout(stream):
            code = build.main()
    finally:
        database.load = original
    assert code == 1
    assert "ERROR invalid yaml document" in stream.getvalue()
