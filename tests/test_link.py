import pytest
import tempfile
import yaml
from pathlib import Path

from modelpedia.build import database
from modelpedia import graph as graph_json
from modelpedia.ingest import answers
from modelpedia.ingest import link
from modelpedia.ingest import proposals
from modelpedia.ingest import registries
from modelpedia.ingest import report
from modelpedia.ingest import tagging
from tests.helpers import answer_with, index_of, sample_db, split_of


def test_every_registry_entity_resolves_to_its_own_slug():
    db = database.load()
    misses = []
    for node_type in graph_json.REGISTRY_TYPES:
        index = link.index_of(db.entities, node_type)
        for key, entity in db.entities.items():
            if entity["type"] != node_type:
                continue
            for probe in (link.display_name(key, entity), key, key.partition(":")[2]):
                if link.resolve(probe, index).slug != key:
                    misses.append((key, probe))
    assert misses == []

def test_an_index_can_be_narrowed_to_several_types_at_once():
    entities = sample_db().entities
    everything = link.index_of(entities)
    registries = link.index_of(entities, graph_json.REGISTRY_TYPES)
    variants = [key for key in everything.identifiers if key.startswith("variant:")]
    assert variants, "the fixture must hold a variant for this to test anything"
    assert all(key not in registries.identifiers for key in variants)
    assert set(registries.identifiers) < set(everything.identifiers)

def test_a_variant_name_never_resolves_in_a_registry_only_index():
    entities = sample_db().entities
    registries = link.index_of(entities, graph_json.REGISTRY_TYPES)
    for key in link.index_of(entities).identifiers:
        if key.startswith("variant:"):
            assert link.resolve(key, registries).slug != key

def test_a_known_name_resolves_exactly():
    assert link.resolve("Probe", index_of(graph_json.METHOD)).how == link.BY_NAME

def test_a_full_identifier_resolves_without_searching():
    assert link.resolve("method:probe", index_of(graph_json.METHOD)).how == link.BY_KEY

def test_a_near_match_is_offered_but_never_taken_automatically():
    resolution = link.resolve("Probes", index_of(graph_json.METHOD))
    assert resolution.kind == link.CANDIDATES
    assert resolution.slug is None
    assert "method:probe" in resolution.candidates

def test_two_equally_close_names_stay_undecided():
    def twins(entities):
        entities["method:probe-two"] = {"type": graph_json.METHOD, "name": "Probe",
                                        "anchor": "https://example.org"}
    resolution = link.resolve("Probe", index_of(graph_json.METHOD, entities=twins))
    assert resolution.kind == link.CANDIDATES
    assert resolution.candidates == ("method:probe", "method:probe-two")

def test_an_unknown_name_misses_rather_than_guessing():
    assert link.resolve("Fisher vectors", index_of(graph_json.METHOD)).kind == link.MISS

def test_a_short_shared_suffix_is_not_enough_to_suggest_a_match():
    def numbered(entities):
        entities["model:siglip-2"] = {"type": graph_json.MODEL, "name": "SigLIP-2",
                                      "modality": ["image"], "variants": {}}
    assert link.resolve("GPT-2", index_of(graph_json.MODEL, entities=numbered)).kind == link.MISS

def test_the_wrong_registry_is_never_searched():
    assert link.resolve("Probe", index_of(graph_json.DATASET)).kind == link.MISS

def test_gram_blocking_matches_an_exhaustive_scan():
    db = database.load()
    index = link.index_of(db.entities)
    for query in ("spectral clustering", "Moran", "GPT-2", "probing classifier", "xyzzy"):
        target = link.normalise(query)
        scanned = {name for name in index.by_name
                   if link.similarity(target, name) >= link.THRESHOLD}
        blocked = {name for name in link.comparable(target, index)
                   if link.similarity(target, name) >= link.THRESHOLD}
        assert scanned == blocked, query

def proposal_of(name, papers=("p1",), state="confirmed", candidates=()):
    return proposals.Proposal(name, "methods", tuple(papers), "method", "", state, "",
                              tuple(candidates))

def concept_answers(**changes):
    fields = {"proposals": (), "refusals": (), "silent": (), "without_concept": 0, "stray": 0}
    fields.update(changes)
    return proposals.ConceptAnswers(**fields)

def proposed_report(**changes):
    fields = {"found": (proposal_of("Probe"),), "kept": (proposal_of("Probe"),),
              "families": (), "concepts": concept_answers(), "unknown": (), "misshapen": (),
              "papers": 1, "least": 1}
    fields.update(changes)
    return report.for_proposals(report.Proposed(**fields))

def test_a_report_section_with_nothing_to_say_leaves_no_trace():
    quiet = proposed_report()
    assert "close to something" not in quiet
    assert "proposed concepts" not in quiet
    assert "did not fire" not in quiet
    assert not quiet.startswith("\n") and not quiet.endswith("\n")
    assert "\n\n\n" not in quiet

def test_a_candidate_close_to_a_registry_entry_is_named_for_a_human_to_decide():
    close = proposal_of("Linear probing", candidates=("method:probe",))
    shown = proposed_report(found=(close,), kept=(close,))
    assert "close to something already in a registry, a human decides:" in shown
    assert "Linear probing" in shown and "method:probe" in shown

def test_a_name_already_in_a_registry_is_not_proposed_again():
    db = sample_db()
    documents = {"p1": answer_with(finding={"methods": [{"name": "Probe"}, {"name": "Widgetron"}]})}
    found = proposals.gather(documents, db.entities)
    assert [item.name for item in found] == ["Widgetron"]

def test_a_proposal_counts_the_papers_it_came_from_and_ranks_by_reach():
    db = sample_db()
    documents = {
        "p1": answer_with(finding={"methods": [{"name": "Widgetron"}, {"name": "Onceler"}]}),
        "p2": answer_with(finding={"methods": [{"name": "Widgetron"}]}),
    }
    found = proposals.gather(documents, db.entities)
    assert [(item.name, item.reach()) for item in found] == [("Widgetron", 2), ("Onceler", 1)]
    assert found[0].papers == ("p1", "p2")

def test_a_near_match_is_offered_as_a_candidate_never_as_a_link():
    db = sample_db()
    documents = {"p1": answer_with(finding={"methods": [{"name": "Probes"}]})}
    found = proposals.gather(documents, db.entities)
    assert found[0].candidates == ("method:probe",)

def test_names_sharing_their_words_are_grouped_into_one_decision():
    def proposal(name):
        return proposals.Proposal(name, "methods", ("p1",), "method", "", "absent", "", ())
    family = proposals.families([proposal("Balanced Forman Curvature (BFC)"),
                                 proposal("Augmented Forman Curvature with 3-cycles (AFC3)"),
                                 proposal("Integrated gradients")])
    assert len(family) == 1
    assert {member.name for member in family[0].members} == {
        "Balanced Forman Curvature (BFC)", "Augmented Forman Curvature with 3-cycles (AFC3)"}

def test_a_single_name_is_never_reported_as_a_family():
    def proposal(name):
        return proposals.Proposal(name, "methods", ("p1",), "method", "", "absent", "", ())
    assert proposals.families([proposal("Integrated gradients")]) == []

def test_a_proposed_concept_is_gathered_with_every_paper_that_asked_for_it():
    documents = {
        "p1": {"findings": [], "concepts_considered": [
            {"name": "Positional bias", "definition": "output depends on position",
             "instead_of": "shortcut is about data"}]},
        "p2": {"findings": [], "concepts_considered": [{"name": "positional  bias"}]},
    }
    gathered = proposals.concept_answers(documents).proposals
    assert len(gathered) == 1
    assert gathered[0]["papers"] == ["p1", "p2"]
    assert gathered[0]["definitions"] == ["output depends on position"]

VARIANT_ENTITIES = {
    "model:llama-3-1": {"type": "model", "name": "Llama 3.1"},
    "model:llama-2": {"type": "model", "name": "Llama 2"},
    "variant:llama-3-1-8b": {"type": "variant", "name": "Llama 3.1 8B",
                             "parent": "model:llama-3-1"},
    "variant:orphan": {"type": "variant", "name": "Orphan checkpoint"},
}

def variant_setup():
    return (link.index_of(VARIANT_ENTITIES, "model"),
            link.index_of(VARIANT_ENTITIES, "variant"),
            link.parents_of(VARIANT_ENTITIES))

def test_a_checkpoint_name_resolves_to_its_model_and_records_the_variant():
    models, variants, parents = variant_setup()
    found, variant = link.resolve_model("Llama 3.1 8B", models, variants, parents)
    assert found.kind == link.HIT
    assert found.slug == "model:llama-3-1"
    assert variant == "variant:llama-3-1-8b"

def test_a_model_name_still_resolves_directly_and_names_no_variant():
    models, variants, parents = variant_setup()
    found, variant = link.resolve_model("Llama 2", models, variants, parents)
    assert (found.kind, found.slug, variant) == (link.HIT, "model:llama-2", "")

def test_a_variant_with_no_parent_never_becomes_a_hit_on_an_empty_slug():
    models, variants, parents = variant_setup()
    found, variant = link.resolve_model("Orphan checkpoint", models, variants, parents)
    assert found.kind != link.HIT
    assert not found.slug

def test_a_name_written_without_separators_still_finds_its_entity():
    models, variants, parents = variant_setup()
    for written in ("llama3.1", "Llama-3.1", "LLAMA 3.1", "llama31"):
        found, _ = link.resolve_model(written, models, variants, parents)
        assert (found.kind, found.slug) == (link.HIT, "model:llama-3-1"), written

def test_a_name_matching_nothing_is_still_a_miss():
    models, variants, parents = variant_setup()
    found, variant = link.resolve_model("Widgetron 9000", models, variants, parents)
    assert (found.kind, variant) == (link.MISS, "")

def test_a_concept_the_model_invented_is_reported_rather_than_taken_on_trust():
    documents = {"p1": {"findings": [{"title": "one", "concepts": ["concept:shortcut"]},
                                     {"title": "two", "concepts": ["concept:vibes"]}]}}
    unknown, misshapen = proposals.off_list(documents, {"concept:shortcut"})
    assert [item.value for item in unknown] == ["concept:vibes"]
    assert misshapen == ()

def test_a_concept_written_in_the_wrong_shape_is_read_but_still_reported():
    documents = {"p1": {"findings": [{"title": "one", "concepts": [{"concept": "shortcut"}]}]}}
    unknown, misshapen = proposals.off_list(documents, {"concept:shortcut"})
    assert unknown == ()
    assert [item.value for item in misshapen] == ["concept:shortcut"]

def test_an_entry_carrying_no_name_is_a_refusal_and_not_a_dropped_record():
    documents = {"p1": {"findings": [{"title": "CLIP leans on the caption", "concepts": []}],
                        "concepts_considered": [{"finding": "CLIP leans on the caption",
                                                 "closest": "concept:shortcut",
                                                 "why": "the mechanism is this paper's own"}]}}
    answered = proposals.concept_answers(documents)
    assert answered.proposals == ()
    assert [refusal.closest for refusal in answered.refusals] == ["concept:shortcut"]
    assert answered.silent == ()
    assert answered.without_concept == 1 and answered.answered() == 1

def test_an_untagged_finding_nobody_answered_for_is_reported_rather_than_passed_over():
    documents = {"p1": {"findings": [{"title": "CLIP leans on the caption", "concepts": []},
                                     {"title": "SigLIP does not", "concepts": []}],
                        "concepts_considered": [{"finding": "CLIP leans on the caption",
                                                 "why": "nothing fits"}]}}
    answered = proposals.concept_answers(documents)
    assert answered.without_concept == 2 and answered.answered() == 1
    assert [gap.finding for gap in answered.silent] == ["SigLIP does not"]

def test_a_missing_concepts_key_counts_the_same_as_an_empty_one():
    documents = {"p1": {"findings": [{"title": "CLIP leans on the caption"}],
                        "concepts_considered": []}}
    assert proposals.concept_answers(documents).without_concept == 1

def test_a_finding_that_took_a_concept_is_never_asked_to_account_for_itself():
    documents = {"p1": {"findings": [{"title": "CLIP leans on the caption",
                                      "concepts": ["concept:shortcut"]}]}}
    answered = proposals.concept_answers(documents)
    assert answered.without_concept == 0 and answered.silent == ()

def test_an_entry_matches_its_finding_even_when_the_title_is_shortened():
    documents = {"p1": {"findings": [{"title": "CLIP leans on the caption, not the image",
                                      "concepts": []}],
                        "concepts_considered": [{"finding": "CLIP leans on the caption",
                                                 "why": "nothing fits"}]}}
    assert proposals.concept_answers(documents).silent == ()

def test_every_reader_refuses_a_malformed_block_that_it_actually_reads():
    """Each reader validates the blocks it uses and ignores the ones it does not: `named_in`
    never looks at `entities`, `entity_notes` never looks at `findings`. The earlier version of
    this test asked all three to refuse the same document and passed when one of them simply
    returned, which is how it hid that `named_in` accepts a broken `entities` block."""
    readers = (("named_in", lambda d: answers.named_in(d), ("findings", "considered")),
               ("entity_notes", lambda d: proposals.entity_notes(d), ("entities",)),
               ("gather", lambda d: proposals.gather({"p": d}, {}), ("findings", "entities")))
    for name, call, blocks in readers:
        for block in blocks:
            document = {"findings": [], block: "oops"}
            with pytest.raises(answers.Unreadable, match=block):
                call(document)

def test_the_splitter_and_the_propose_report_agree_on_misshapen_concepts():
    kept, _, _ = split_of([{"title": "A claim", "description": "d",
                            "models": [{"name": "Llama 2"}],
                            "concepts": [{"id": "concept:shortcut"}]}])
    assert kept[0].record["concepts"] == [{"ref": "concept:shortcut"}]
    _, misshapen = proposals.off_list(
        {"p1": {"findings": [{"title": "A claim",
                              "concepts": [{"id": "concept:shortcut"}]}]}},
        {"concept:shortcut"})
    assert len(misshapen) == 1

TAG_CONCEPTS = {
    "concept:shortcut": {"type": "concept", "name": "Shortcut",
                         "description": "The model relies on a feature that correlates with the "
                                        "target but does not cause it."},
}

def test_a_tagging_prompt_carries_the_finding_and_the_definitions_and_nothing_else():
    finding = {"title": "CLIP leans on the caption", "description": "A long story.",
               "key_metric": "42% drop", "caveat": "one dataset only"}
    body = tagging.build(finding, TAG_CONCEPTS)
    assert "CLIP leans on the caption" in body
    assert "correlates with the" in body
    assert "42% drop" not in body and "one dataset only" not in body

def test_tagging_asks_for_the_phrase_of_the_definition_not_just_an_identifier():
    body = tagging.build({"title": "t", "description": "d"}, TAG_CONCEPTS)
    assert "because" in body
    assert "quote" in body.lower()

def test_only_findings_with_no_concept_are_offered_for_tagging_by_default():
    db = sample_db()
    db.findings["XX-002"] = {"title": "b", "description": "d", "concepts": []}
    db.findings["XX-001"]["concepts"] = [{"ref": "concept:idea"}]
    assert list(tagging.wanted(db)) == ["XX-002"]
    assert sorted(tagging.wanted(db, only_untagged=False)) == ["XX-001", "XX-002"]

def test_agreement_counts_what_a_re_tagging_run_actually_changed():
    before = {"XX-001": ["concept:a"], "XX-002": []}
    after = {"XX-001": ["concept:a"], "XX-002": ["concept:b"]}
    assert tagging.agreement(before, after) == {"findings": 2, "unchanged": 1,
                                                "added": 1, "removed": 0}

def test_a_checkpoint_already_in_the_registry_as_a_variant_is_not_proposed():
    from modelpedia.ingest import proposals
    entities = {"model:llama-3": {"type": "model", "name": "Llama 3"},
                "variant:llama-3-8b": {"type": "variant", "name": "Llama-3-8B",
                                       "parent": "model:llama-3"}}
    documents = {"paperA": {"findings": [{"models": [{"name": "Llama-3-8B"}]}]}}
    assert proposals.gather(documents, entities) == []
    documents = {"paperA": {"findings": [{"models": [{"name": "Tulu-2-13B"}]}]}}
    assert [item.name for item in proposals.gather(documents, entities)] == ["Tulu-2-13B"]

def test_one_key_is_indexed_once_per_name_however_many_spellings_it_has():
    entities = {"method:autodan": {"type": "method", "name": "AutoDAN / AutoDan"}}
    index = link.index_of(entities, "method")
    assert link.resolve("AutoDAN", index).slug == "method:autodan"
    assert link.resolve("AutoDan", index).slug == "method:autodan"

ADOPTION_FAMILIES = (("model:llama-3-1", "Llama 3.1"),)

def test_an_invented_family_identifier_is_refused_not_written():
    from modelpedia.ingest import adoption
    row = {"name": "Llama-3.1-405B", "field": "models", "papers": ["p"], "citation": ""}
    answer = {"decision": "adopt", "title": "Llama-3.1-405B", "family": "model:llama-4",
              "anchor": ""}
    verdict = adoption.judge(row, answer, [], ADOPTION_FAMILIES)
    assert not verdict.adopted() and "closed list" in verdict.problem

def test_an_anchor_the_paper_does_not_carry_is_dropped():
    from modelpedia.ingest import adoption
    row = {"name": "HellaSwag", "field": "datasets", "papers": ["p"], "citation": ""}
    answer = {"decision": "adopt", "title": "HellaSwag", "anchor": "https://arxiv.org/abs/1905.07830"}
    kept = adoption.judge(row, answer, ["as shown in arXiv:1905.07830 we"], ADOPTION_FAMILIES)
    assert kept.anchor.endswith("1905.07830") and not kept.problem
    dropped = adoption.judge(row, answer, ["a paper that never cites it"], ADOPTION_FAMILIES)
    assert dropped.anchor == "" and "not in any citing paper" in dropped.problem

def test_a_refusal_may_name_the_entry_it_duplicates():
    from modelpedia.ingest import adoption
    row = {"name": "BBH", "field": "datasets", "papers": ["p"], "citation": ""}
    verdict = adoption.judge(row, {"decision": "refuse", "alias_of": "dataset:big-bench-hard",
                                   "why": "already held"}, [], ADOPTION_FAMILIES)
    assert verdict.decision == adoption.REFUSE and verdict.alias_of == "dataset:big-bench-hard"

def test_the_new_family_sentinel_is_read_with_or_without_its_prefix():
    from modelpedia.ingest import adoption
    row = {"name": "Falcon", "field": "models", "papers": ["p"], "citation": ""}
    for written in ("new", "model:new"):
        verdict = adoption.judge(row, {"decision": "adopt", "title": "Falcon",
                                       "family": written, "anchor": ""}, [], ADOPTION_FAMILIES)
        assert verdict.adopted() and verdict.family == "new"

def _adopted(name, title, field="models", family="new"):
    from modelpedia.ingest import adoption
    return adoption.Verdict(name, field, adoption.ADOPT, title, family, "", "", "", "")

def test_checkpoints_of_one_absent_family_become_one_family_with_variants():
    from modelpedia.ingest import registries
    placed = registries.regrouped([_adopted("Vicuna-7B", "Vicuna-7B"),
                                   _adopted("Vicuna-13B", "Vicuna-13B"),
                                   _adopted("Vicuna-7B-v1.5", "Vicuna-7B-v1.5")])
    assert set(placed.values()) == {"model:vicuna"}
    assert len(placed) == 3

def test_a_family_named_among_the_proposals_becomes_the_parent_itself():
    from modelpedia.ingest import registries
    placed = registries.regrouped([_adopted("GPT-2", "GPT-2"),
                                   _adopted("GPT-2 small", "GPT-2 small")])
    assert placed == {"GPT-2 small": "model:gpt-2"}

def test_a_lone_new_model_is_left_alone():
    from modelpedia.ingest import registries
    assert registries.regrouped([_adopted("Falcon", "Falcon")]) == {}

def test_an_identifier_the_model_overlooked_is_taken_from_the_citation():
    from modelpedia.ingest import adoption
    row = {"name": "HotpotQA", "field": "datasets", "papers": ["p"],
           "citation": "Zhilin Yang et al. HotpotQA. arXiv:1809.09600, 2018.", "state": "confirmed"}
    verdict = adoption.judge(row, {"decision": "adopt", "title": "HotpotQA", "anchor": ""},
                             [], ADOPTION_FAMILIES)
    assert verdict.anchor == "https://arxiv.org/abs/1809.09600"

def test_a_citation_the_paper_never_carried_is_not_mined_for_an_anchor():
    from modelpedia.ingest import adoption
    row = {"name": "Ghost", "field": "datasets", "papers": ["p"],
           "citation": "Nobody et al. arXiv:1234.56789, 2019.", "state": "absent"}
    verdict = adoption.judge(row, {"decision": "adopt", "title": "Ghost", "anchor": ""},
                             [], ADOPTION_FAMILIES)
    assert verdict.anchor == ""

def test_an_alias_pointing_outside_the_written_registries_is_skipped_not_crashed():
    from modelpedia.ingest import adoption, registries
    refusal = adoption.Verdict("Linear rep", "methods", adoption.REFUSE, "", "", "",
                               "concept:linear-representation", "already held", "")
    done = registries.apply([refusal], set(), {})
    assert any("pominiety" in line for line in done)

def test_a_variant_whose_family_is_absent_is_reported_not_raised():
    from modelpedia.ingest import registries
    assert registries.insert_variant("model:nie-ma-takiego", "variant:x", "X") is False

def test_a_name_that_starts_with_a_digit_still_makes_a_valid_slug():
    from modelpedia.ingest import adoption
    from modelpedia import schema
    for title in ("3D Gaussian Splatting", "2WikiMultihopQA", "7B baseline"):
        slug = adoption.slug_for(title, title)
        assert schema.SLUG.fullmatch(slug), (title, slug)
    assert adoption.slug_for("3D Gaussian Splatting", "") == "gaussian-splatting-3d"
    assert adoption.slug_for("2WikiMultihopQA", "") == "wikimultihopqa-2"

def test_two_proposals_with_one_canonical_title_make_one_entry():
    from modelpedia.ingest import registries
    twins = [_adopted("Representation Engineering", "Representation Engineering", "methods", ""),
             _adopted("RepE", "Representation Engineering", "methods", "")]
    done = registries.regrouped(twins)
    assert done == {}

def registry_file(body):
    directory = tempfile.mkdtemp()
    path = Path(directory) / "datasets.yaml"
    path.write_text(body, encoding="utf-8")
    return path

def test_an_anchor_is_written_onto_an_entry_that_has_none():
    path = registry_file("dataset:one:\n  name: One\n\ndataset:two:\n  name: Two\n")
    kept = registries.path_for
    try:
        registries.path_for = lambda field: path
        assert registries.set_anchor("datasets", "dataset:one", "https://example.org/a")
    finally:
        registries.path_for = kept
    held = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert held["dataset:one"]["anchor"] == "https://example.org/a"
    assert "anchor" not in held["dataset:two"]

def test_an_anchor_already_there_is_never_overwritten():
    path = registry_file("dataset:one:\n  name: One\n  anchor: https://example.org/kept\n")
    kept = registries.path_for
    try:
        registries.path_for = lambda field: path
        assert registries.set_anchor("datasets", "dataset:one", "https://example.org/new") is False
    finally:
        registries.path_for = kept
    assert "example.org/kept" in path.read_text(encoding="utf-8")

def test_an_empty_anchor_key_is_filled_rather_than_duplicated():
    path = registry_file("dataset:one:\n  name: One\n  anchor: null\n  note: null\n")
    kept = registries.path_for
    try:
        registries.path_for = lambda field: path
        assert registries.set_anchor("datasets", "dataset:one", "https://example.org/a")
    finally:
        registries.path_for = kept
    body = path.read_text(encoding="utf-8")
    assert body.count("anchor:") == 1
    assert yaml.safe_load(body)["dataset:one"]["note"] is None

def test_writing_an_anchor_leaves_every_other_entry_byte_identical():
    before = "dataset:one:\n  name: One\n\ndataset:two:\n  name: Two\n  note: kept verbatim\n"
    path = registry_file(before)
    kept = registries.path_for
    try:
        registries.path_for = lambda field: path
        registries.set_anchor("datasets", "dataset:one", "https://example.org/a")
    finally:
        registries.path_for = kept
    after = path.read_text(encoding="utf-8")
    assert "dataset:two:\n  name: Two\n  note: kept verbatim\n" in after
