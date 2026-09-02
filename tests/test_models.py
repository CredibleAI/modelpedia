import yaml

from modelpedia import models
from modelpedia import paths
from modelpedia import schema


CANDIDATE = {
    "id": "XX-001",
    "title": "A title",
    "description": "A description.",
    "models": [{"ref": "model:thing", "variant": "variant:thing-small"}],
    "concepts": [{"ref": "concept:idea"}],
    "sources": [{"ref": "source:paper"}],
    "datasets": [{"ref": "dataset:pile", "role": "eval"}],
    "methods": [{"ref": "method:probe", "role": "primary"}],
    "related_work": [{"name": "Outside work", "anchor": "https://example.org/x",
                      "role": "context"}],
    "evidence_type": "observational",
    "key_metric": "rho = 0.3",
    "caveat": "A caveat.",
    "extracted_by": "automatic-extraction",
    "related_findings": ["XX-002"],
}


def test_a_reference_keeps_only_the_keys_it_was_given():
    assert models.Ref(ref="method:probe").to_dict() == {"ref": "method:probe"}
    assert models.Ref(ref="dataset:pile", role="eval").to_dict() == {"ref": "dataset:pile",
                                                                    "role": "eval"}


def test_an_inline_entry_carries_its_anchor():
    entry = models.Inline(name="Outside work", anchor="https://example.org/x", role="context")
    assert entry.to_dict() == {"name": "Outside work", "anchor": "https://example.org/x",
                               "role": "context"}


def test_a_link_is_read_as_a_reference_or_as_an_inline_entry_by_its_keys():
    assert isinstance(models.link_from({"ref": "method:probe"}), models.Ref)
    assert isinstance(models.link_from({"name": "W", "anchor": "https://e.org"}), models.Inline)


def test_a_full_record_round_trips_unchanged():
    assert models.Finding.from_dict(CANDIDATE).to_dict() == CANDIDATE


def test_the_key_order_is_the_declared_one_and_not_the_input_order():
    shuffled = dict(reversed(list(CANDIDATE.items())))
    written = models.Finding.from_dict(shuffled).to_dict()
    assert list(written) == [name for name in models.FIELD_ORDER if name in written]
    assert written == CANDIDATE


def test_an_empty_link_list_is_written_because_a_stated_gap_is_not_a_missing_value():
    bare = dict(CANDIDATE, concepts=[], datasets=[], methods=[], related_work=[])
    written = models.Finding.from_dict(bare).to_dict()
    for field in ("concepts", "datasets", "methods", "related_work"):
        assert written[field] == [], field


def test_an_absent_optional_stays_absent():
    without = {key: value for key, value in CANDIDATE.items()
               if key not in ("key_metric", "caveat", schema.RELATED_FINDINGS_FIELD)}
    written = models.Finding.from_dict(without).to_dict()
    assert "key_metric" not in written
    assert "caveat" not in written
    assert schema.RELATED_FINDINGS_FIELD not in written


def test_an_optional_written_as_null_becomes_absent_rather_than_null():
    written = models.Finding.from_dict(dict(CANDIDATE, key_metric=None)).to_dict()
    assert "key_metric" not in written


def test_a_record_with_no_id_omits_it_because_the_splitter_adds_it_later():
    written = models.Finding.from_dict({key: value for key, value in CANDIDATE.items()
                                        if key != "id"}).to_dict()
    assert "id" not in written
    assert list(written)[0] == "title"


def test_every_field_the_schema_knows_has_a_place_in_the_record():
    covered = set(models.FIELD_ORDER)
    assert schema.KNOWN_FIELDS <= covered, schema.KNOWN_FIELDS - covered


def test_the_real_records_survive_a_round_trip_through_the_model():
    exact = benign = 0
    for path in sorted(paths.FINDINGS.glob("*.yaml")):
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        back = models.Finding.from_dict(raw).to_dict()
        if back == raw:
            exact += 1
            continue
        dropped = {key for key in raw if key not in back}
        assert not [key for key in back if key not in raw], path.name
        assert not [key for key in raw if key in back and raw[key] != back[key]], path.name
        assert all(not raw[key] for key in dropped), path.name
        assert dropped <= {"key_metric", "caveat", schema.RELATED_FINDINGS_FIELD}, path.name
        benign += 1
    assert exact + benign == len(list(paths.FINDINGS.glob("*.yaml")))
    assert benign <= 10, "more records than measured lose an explicitly empty optional"
