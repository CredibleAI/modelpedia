import csv
import copy
import json
import os
import tempfile
import yaml
from pathlib import Path

from modelpedia.commands import check
from modelpedia.commands import harvest
from modelpedia import atomic
from modelpedia.build import database
from modelpedia import paths
from modelpedia import graph as graph_json
from modelpedia.ingest import answers
from modelpedia.ingest import manifest as store
from modelpedia.ingest import citations
from modelpedia.ingest import link
from modelpedia.ingest import openreview
from modelpedia.ingest import prompt
from modelpedia.ingest import proposals
from modelpedia.ingest import registries
from modelpedia.ingest import report
from modelpedia.ingest import split as splitter
from modelpedia.ingest import tagging
from modelpedia.build import validate
from modelpedia.ingest import screen
from modelpedia.ingest import text
from tests.test_build import sample_db

CANDIDATE = {
    "id": "XX-999",
    "extracted_by": "automatic-extraction",
    "title": "A candidate",
    "description": "A description.",
    "evidence_type": "observational",
    "concepts": [{"ref": "concept:idea"}],
    "models": [{"ref": "model:thing", "variant": "variant:thing-small"}],
    "sources": [{"ref": "source:the-paper"}],
    "datasets": [{"ref": "dataset:pile", "role": "eval"}],
    "methods": [{"ref": "method:probe", "role": "primary"}],
    "related_work": [],
    "related_findings": [],
}


def candidate(**changes):
    record = copy.deepcopy(CANDIDATE)
    record.update(changes)
    return record


def manifest_line(paper_id, tier, **extra):
    row = {"id": paper_id, "tier": tier, "venue": "V/2026"}
    row.update(extra)
    return json.dumps(row) + "\n"


def index_of(node_type=None, **changes):
    return link.index_of(sample_db(**changes).entities, node_type)


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


def test_a_word_broken_across_a_line_is_still_found():
    doc = text.from_text("t", "cited as Ol-\nmoEarth in the introduction")
    assert "olmoearth" not in doc.pages[0].lower()
    assert text.contains(doc, "OlmoEarth")


def test_a_hyphenated_name_matches_with_or_without_the_hyphen():
    doc = text.from_text("t", "the Koppen-Geiger classification")
    assert text.contains(doc, "Koppen Geiger")
    assert text.contains(doc, "Köppen-Geiger")


def test_a_small_caps_heading_split_by_the_extractor_is_rejoined():
    doc = text.from_text("t", "A BSTRACT\nwe study I NTRODUCTION and the NL-E YE benchmark")
    assert text.contains(doc, "abstract")
    assert text.contains(doc, "introduction")
    assert text.contains(doc, "NL-Eye")
    assert "abstract" in doc.text
    assert "introduction" in doc.text


def test_rejoining_small_caps_never_touches_text_that_did_not_come_from_a_pdf():
    for prose in ("A BERT model for classification", "We use A GPT-4 baseline",
                  "an LLM and A VLM", "A model was trained", "the AI Act"):
        assert text.normalise(prose) == prose.lower()
        assert text.flatten(prose) == prose.lower().replace(" ", "").replace("-", "")


def test_a_small_caps_split_is_repaired_only_when_reading_a_document():
    assert "abstract" not in text.normalise("A BSTRACT")
    assert "abstract" in text.from_text("t", "A BSTRACT").text


def test_absent_text_is_reported_absent():
    assert not text.contains(text.from_text("t", "nothing to see"), "OlmoEarth")


def test_page_numbers_are_one_based():
    doc = text.from_text("t", "first page\fsecond page with OlmoEarth")
    assert text.pages_with(doc, "OlmoEarth") == (2,)


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


AUDIT = ("What does CLIP actually look at?",
         "We analyze CLIP and find that it relies on printed text in images rather than on "
         "depicted content. Our analysis reveals a systematic shortcut.")

OPTIMISER = ("A faster optimizer for large-scale training",
             "We propose a new optimizer. Our method outperforms Adam and achieves "
             "state-of-the-art convergence on ImageNet.")


AUDIT_REVIEWS = (
    "The paper analyzes what CLIP attends to and finds that the model relies on printed text. "
    "The probing experiments are convincing and the shortcut is well characterized.",
    "This is an empirical study of CLIP. The authors probe the representation and show that "
    "it fails to use depicted content. A careful analysis of a known failure mode.",
    "The submission analyzes CLIP and reveals that the model cannot separate text from content. "
    "The probing setup is sound and the shortcut it characterizes is real.",
)

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


def test_openreview_unwraps_the_value_envelope_of_api_v2():
    assert openreview.value_of({"value": "a title"}) == "a title"
    assert openreview.value_of("a title") == "a title"
    assert openreview.flat_content({"title": {"value": "t"}, "plain": "p"}) == \
        {"title": "t", "plain": "p"}
    assert openreview.flat_content(None) == {}


def screened_row(paper_id="aBcD"):
    content = openreview.flat_content(
        {"title": {"value": AUDIT[0]}, "abstract": {"value": AUDIT[1]}})
    return store.row_for(paper_id, content, "V/2026", screen.screen(*AUDIT),
                         screen.RULES_VERSION, openreview.pdf_url(paper_id))


def test_harvest_manifest_row_carries_the_screening():
    row = screened_row()
    assert row["id"] == "aBcD"
    assert row["tier"] == screen.POSSIBLE
    assert row["pdf"].endswith("aBcD")
    assert not row["has_pdf"]
    assert any(signal.startswith("model:") for signal in row["signals"])
    assert row["subscores"]["model"] == 2.0


def test_harvest_downloads_only_tiers_it_was_asked_for():
    assert screen.WEAK not in harvest.DOWNLOAD_TIERS
    assert screen.STRONG in harvest.DOWNLOAD_TIERS


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


def test_report_blocks_are_separated_by_exactly_one_blank_line():
    assert report.render([["a"], [], ["b", "c"], []]) == "a\n\nb\nc"
    assert report.render([[], ["only"]]) == "only"
    assert report.render([[], []]) == ""


def test_a_candidate_close_to_a_registry_entry_is_named_for_a_human_to_decide():
    close = proposal_of("Linear probing", candidates=("method:probe",))
    shown = proposed_report(found=(close,), kept=(close,))
    assert "close to something already in a registry, a human decides:" in shown
    assert "Linear probing" in shown and "method:probe" in shown


def test_the_citation_report_derives_its_total_and_names_the_rejected_state():
    tally = {"confirmed": 3, "partial": 1, "rejected": 2, "absent": 4}
    lines = report.for_citations(tally, tuple(tally), "rejected", "out.jsonl").split("\n")
    assert lines[-1] == "10 entities, 2 rejected, report in out.jsonl"
    assert lines[0].split() == ["confirmed", "3", "30%"]
    assert [line.split()[0] for line in lines[:4]] == list(tally)


def test_the_manifest_store_takes_its_path_so_a_second_venue_can_have_its_own():
    with tempfile.TemporaryDirectory() as directory:
        first = Path(directory) / "iclr.jsonl"
        second = Path(directory) / "icml.jsonl"
        first.write_text(manifest_line("a", "strong"), encoding="utf-8")
        second.write_text(manifest_line("b", "weak"), encoding="utf-8")
        assert [row["id"] for row in store.load(first).rows] == ["a"]
        assert [row["id"] for row in store.load(second).rows] == ["b"]
        assert store.load(Path(directory) / "absent.jsonl").rows == ()


def test_the_store_reports_damage_instead_of_printing_it():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "manifest.jsonl"
        path.write_text('{"id": "a", "tier": "strong"}\n'
                        + manifest_line("b", "weak")
                        + manifest_line("b", "strong"), encoding="utf-8")
        held = store.load(path)
        assert [row["id"] for row in held.rows] == ["b"]
        assert held.repeated == 1
        assert len(held.complaints) == 1 and "lacks venue" in held.complaints[0]


def test_counting_seen_ids_never_builds_the_rows_it_throws_away():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "manifest.jsonl"
        path.write_text(manifest_line("a", "strong")
                        + '{"id": "damaged", "tier": "strong"}\n'
                        + manifest_line("c", "weak"), encoding="utf-8")
        assert store.ids_in(path) == {"a", "c"}


def test_an_atomic_write_stages_beside_the_target_without_losing_its_extension():
    with tempfile.TemporaryDirectory() as directory:
        target = Path(directory) / "paper.pdf"
        assert atomic.staging_path(target).name == "paper.pdf.part"
        atomic.write_text(Path(directory) / "paper.txt", "body")
        assert atomic.staging_path(Path(directory) / "paper.txt").name == "paper.txt.part"
        assert list(Path(directory).glob("*" + paths.PARTIAL)) == []


def test_the_api_module_signals_rather_than_exiting_so_the_entry_point_decides():
    kept = {name: os.environ.pop(name, None)
            for name in (openreview.USERNAME_ENV, openreview.PASSWORD_ENV)}
    try:
        try:
            openreview.credentials()
            raise AssertionError("missing credentials were accepted")
        except openreview.Unavailable as error:
            assert openreview.USERNAME_ENV in str(error)
    finally:
        for name, value in kept.items():
            if value is not None:
                os.environ[name] = value


def test_a_manifest_row_is_rejected_at_read_time_when_a_consumer_would_crash_on_it():
    assert store.row_complaint({"id": "aaa", "tier": "weak", "venue": "V/2026"}) is None
    assert "lacks venue" in store.row_complaint({"id": "aaa", "tier": "weak"})
    assert "lacks id and tier" in store.row_complaint({"venue": "V/2026"})
    assert "is not a record" in store.row_complaint([1, 2])
    assert "cannot become a filename" in store.row_complaint(
        {"id": "../../etc/passwd", "tier": "weak", "venue": "V/2026"})


def test_a_row_a_consumer_would_crash_on_never_reaches_the_consumer():
    original = harvest.MANIFEST
    try:
        with tempfile.TemporaryDirectory() as directory:
            harvest.MANIFEST = Path(directory) / "manifest.jsonl"
            harvest.MANIFEST.write_text(
                '{"id": "../../etc/passwd", "tier": "strong", "venue": "V/2026"}\n'
                '{"id": "novenue", "tier": "strong"}\n'
                '{"id": "good", "tier": "strong", "venue": "V/2026"}\n',
                encoding="utf-8")
            assert [row["id"] for row in harvest.manifest_rows()] == ["good"]
            assert harvest.show_stats() == 0
            assert harvest.harvest_pdfs(ids=["../../etc/passwd"]) == 1
    finally:
        harvest.MANIFEST = original


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


def test_every_harvested_row_records_which_rules_screened_it():
    screening = screen.screen(*AUDIT)

    row = screened_row()
    assert row["rules_version"] == screen.RULES_VERSION
    assert store.row_complaint(row) is None


def test_an_explicit_id_list_selects_rows_and_reports_the_ones_it_cannot_find():
    original = harvest.MANIFEST
    try:
        with tempfile.TemporaryDirectory() as directory:
            harvest.MANIFEST = Path(directory) / "manifest.jsonl"
            harvest.MANIFEST.write_text(
                manifest_line("aaa", "weak") + manifest_line("bbb", "strong"),
                encoding="utf-8")
            assert [row["id"] for row in harvest.selected_rows((), ["bbb", "aaa"])] == ["bbb", "aaa"]
            assert [row["id"] for row in harvest.selected_rows((), ["bbb", "ccc"])] == ["bbb"]
            assert [row["id"] for row in harvest.selected_rows(("strong",), None)] == ["bbb"]
    finally:
        harvest.MANIFEST = original


def test_an_id_file_rejects_anything_that_is_not_an_identifier():
    with tempfile.TemporaryDirectory() as directory:
        good = Path(directory) / "good.txt"
        good.write_text("# comment\naaa\n\nbbb\naaa\n", encoding="utf-8")
        assert store.read_ids(good) == ["aaa", "bbb"]
        hostile = Path(directory) / "hostile.txt"
        hostile.write_text("aaa\n../../etc/passwd\n", encoding="utf-8")
        try:
            store.read_ids(hostile)
            raise AssertionError("accepted a path as an identifier")
        except ValueError:
            pass
    assert harvest.main(["harvest.py", "pdfs", "--ids", str(hostile)]) == 1


class Note:
    def __init__(self, paper_id, title, abstract="", keywords=()):
        self.id = paper_id
        self.content = {"title": {"value": title},
                        "abstract": {"value": abstract},
                        "keywords": {"value": list(keywords)}}

    def to_json(self):
        return {"id": self.id, "content": self.content}


class FakeSubmissions:
    def __init__(self, notes=()):
        self.notes = list(notes)

    def get_all_notes(self, **query):
        return self.notes

    def get_notes(self, **query):
        return ([], len(self.notes)) if query.get("with_count") else []


def harvested(notes, already="", venue_id="V/2026"):
    kept = (harvest.MANIFEST, harvest.META, harvest.connect)
    try:
        with tempfile.TemporaryDirectory() as directory:
            harvest.MANIFEST = Path(directory) / "manifest.jsonl"
            harvest.META = Path(directory) / "meta"
            if already:
                harvest.MANIFEST.write_text(already, encoding="utf-8")
            harvest.connect = lambda generation=openreview.API2: FakeSubmissions(notes)
            code = harvest.harvest_meta(venue_id)
            return (code, list(store.load(harvest.MANIFEST).rows),
                    sorted(path.name for path in harvest.META.glob("*.json")))
    finally:
        harvest.MANIFEST, harvest.META, harvest.connect = kept


def test_harvesting_metadata_screens_each_note_and_writes_one_row_per_paper():
    code, rows, metas = harvested([Note("aaa", *AUDIT), Note("bbb", *OPTIMISER)])
    assert code == 0
    assert [row["id"] for row in rows] == ["aaa", "bbb"]
    assert rows[0]["tier"] == screen.POSSIBLE
    assert rows[1]["tier"] == screen.WEAK
    assert metas == ["aaa.json", "bbb.json"]
    assert all(row["venue"] == "V/2026" for row in rows)
    assert all(row["pdf"].endswith(row["id"]) for row in rows)


def review_line(paper_id, review_id, fields, rating=None, venue_id="V/2026"):
    return json.dumps(store.review_row(paper_id, venue_id, review_id, rating, fields)) + "\n"


def with_corpus(manifest_lines, review_lines, run):
    kept = (harvest.MANIFEST, harvest.REVIEWS)
    try:
        with tempfile.TemporaryDirectory() as directory:
            harvest.MANIFEST = Path(directory) / "manifest.jsonl"
            harvest.REVIEWS = Path(directory) / "reviews"
            harvest.REVIEWS.mkdir()
            harvest.MANIFEST.write_text("".join(manifest_lines), encoding="utf-8")
            (harvest.REVIEWS / store.store_name("V/2026")).write_text(
                "".join(review_lines), encoding="utf-8")
            return run(Path(directory))
    finally:
        harvest.MANIFEST, harvest.REVIEWS = kept


LINE_SEPARATOR = "\u2028"


def test_a_record_carrying_a_unicode_line_separator_is_still_one_row():
    def check(directory):
        held = store.load_reviews(harvest.REVIEWS)
        assert held.complaints == ()
        assert held.count("aaa") == 2
        assert LINE_SEPARATOR in " ".join(held.texts("aaa"))
        return 0

    with_corpus([manifest_line("aaa", "weak")],
                [review_line("aaa", "r1", {"summary": "a shortcut%sand a second clause" % LINE_SEPARATOR}),
                 review_line("aaa", "r2", {"summary": "plain prose"})], check)


class FakeReviewer:
    def __init__(self, refuse_first=0, refuse_after=None, reviews_per_paper=2):
        self.refuse_first = refuse_first
        self.refuse_after = refuse_after
        self.reviews_per_paper = reviews_per_paper
        self.asked = []

    def get_notes(self, **query):
        return ([], 1) if query.get("with_count") else []

    def get_all_notes(self, **query):
        forum = query["forum"]
        self.asked.append(forum)
        spent = (len(self.asked) <= self.refuse_first
                 or self.refuse_after is not None and len(self.asked) > self.refuse_after)
        if spent:
            raise RuntimeError("quota spent")
        return [Reply("%s-r%d" % (forum, number),
                      {"summary": "the paper analyzes a model"})
                for number in range(self.reviews_per_paper)]


class Reply:
    def __init__(self, note_id, content):
        self.id = note_id
        self.content = content
        self.invitations = ["V/2026/Submission1/-/Official_Review"]


class NoClock:
    def __init__(self, elapsed=0.0):
        self.elapsed = elapsed
        self.paused = []
        self.now = 0.0

    def monotonic(self):
        return self.now

    def sleep(self, seconds):
        if seconds:
            self.paused.append(seconds)
        self.now += self.elapsed


def fetched(papers, reviewer, limit=None, pause=None, clock=None):
    clock = clock or NoClock()
    kept = (harvest.MANIFEST, harvest.REVIEWS, harvest.connect, harvest.time)
    try:
        with tempfile.TemporaryDirectory() as directory:
            harvest.MANIFEST = Path(directory) / "manifest.jsonl"
            harvest.REVIEWS = Path(directory) / "reviews"
            harvest.MANIFEST.write_text(
                "".join(manifest_line(paper, "weak") for paper in papers), encoding="utf-8")
            harvest.connect = lambda generation=openreview.API2: reviewer
            harvest.time = clock
            code = harvest.harvest_reviews("V/2026", limit=limit, delay=0, pause=pause)
            return code, store.load_reviews(harvest.REVIEWS), clock.paused
    finally:
        harvest.MANIFEST, harvest.REVIEWS, harvest.connect, harvest.time = kept


def test_one_command_keeps_fetching_batch_after_batch_until_the_venue_is_done():
    reviewer = FakeReviewer()
    code, held, paused = fetched(["p%d" % n for n in range(7)], reviewer, limit=3, pause=3600)
    assert code == 0
    assert reviewer.asked == ["p%d" % n for n in range(7)]
    assert sorted(held.by_paper) == ["p%d" % n for n in range(7)]
    assert paused == [3600, 3600]


def test_a_quota_running_out_mid_batch_gives_the_batch_up_instead_of_grinding_through_it():
    reviewer = FakeReviewer(refuse_after=4)
    code, held, paused = fetched(["p%d" % n for n in range(200)], reviewer,
                                 limit=200, pause=3600)
    assert code == 1
    assert sorted(held.by_paper) == ["p%d" % n for n in range(4)]
    assert len(reviewer.asked) == 4 + harvest.GIVE_UP_AFTER * (1 + harvest.DEAD_BATCHES)
    assert paused == [3600] * harvest.DEAD_BATCHES
    assert len(reviewer.asked) < 200


def test_a_paper_left_unasked_by_a_given_up_batch_is_the_first_one_asked_next_time():
    reviewer = FakeReviewer(refuse_after=4)
    fetched(["p%d" % n for n in range(200)], reviewer, limit=200, pause=3600)
    resumed = 4 + harvest.GIVE_UP_AFTER
    assert reviewer.asked[:5] == ["p0", "p1", "p2", "p3", "p4"]
    assert reviewer.asked[resumed] == "p4"


class FakeAttachments:
    def __init__(self, refuse_after=None, not_a_pdf=()):
        self.refuse_after = refuse_after
        self.not_a_pdf = set(not_a_pdf)
        self.asked = []

    def get_notes(self, **query):
        return ([], 1) if query.get("with_count") else []

    def get_attachment(self, field_name, id):
        self.asked.append(id)
        if self.refuse_after is not None and len(self.asked) > self.refuse_after:
            raise RuntimeError("quota spent")
        return b"not a pdf at all" if id in self.not_a_pdf else b"%PDF-1.7 body"


def downloaded(papers, connection, limit=None, pause=None, on_disk=()):
    clock = NoClock()
    kept = (harvest.MANIFEST, harvest.PDFS, harvest.connect, harvest.time)
    try:
        with tempfile.TemporaryDirectory() as directory:
            harvest.MANIFEST = Path(directory) / "manifest.jsonl"
            harvest.PDFS = Path(directory) / "pdf"
            harvest.PDFS.mkdir()
            for paper in on_disk:
                (harvest.PDFS / ("%s.pdf" % paper)).write_bytes(b"%PDF-1.7 held")
            harvest.MANIFEST.write_text(
                "".join(manifest_line(paper, "strong") for paper in papers), encoding="utf-8")
            harvest.connect = lambda generation=openreview.API2: connection
            harvest.time = clock
            code = harvest.harvest_pdfs(limit=limit, delay=0, pause=pause)
            return code, sorted(path.stem for path in harvest.PDFS.glob("*.pdf")), clock.paused
    finally:
        harvest.MANIFEST, harvest.PDFS, harvest.connect, harvest.time = kept


def test_pdfs_keep_downloading_batch_after_batch_like_the_reviews_do():
    connection = FakeAttachments()
    code, on_disk, paused = downloaded(["p%d" % n for n in range(7)], connection,
                                       limit=3, pause=3600)
    assert code == 0
    assert on_disk == ["p%d" % n for n in range(7)]
    assert paused == [3600, 3600]


def test_a_pdf_already_on_disk_is_not_asked_for_again():
    connection = FakeAttachments()
    code, on_disk, _ = downloaded(["p0", "p1", "p2"], connection, on_disk=["p1"])
    assert connection.asked == ["p0", "p2"]
    assert on_disk == ["p0", "p1", "p2"]
    assert code == 0


def test_a_response_that_is_not_a_pdf_counts_as_answered_so_the_run_can_end():
    connection = FakeAttachments(not_a_pdf=["p1"])
    code, on_disk, paused = downloaded(["p0", "p1", "p2"], connection, limit=3, pause=3600)
    assert connection.asked == ["p0", "p1", "p2"]
    assert on_disk == ["p0", "p2"]
    assert paused == []
    assert code == 1


def test_a_pdf_quota_running_out_mid_batch_gives_the_batch_up_too():
    connection = FakeAttachments(refuse_after=2)
    code, on_disk, paused = downloaded(["p%d" % n for n in range(100)], connection,
                                       limit=100, pause=3600)
    assert on_disk == ["p0", "p1"]
    assert len(connection.asked) == 2 + harvest.GIVE_UP_AFTER * (1 + harvest.DEAD_BATCHES)
    assert code == 1


def test_pdfs_ask_each_venue_through_the_api_generation_that_answers_for_it():
    asked = []

    class PerVenue:
        def __init__(self, venue):
            self.venue = venue

        def get_notes(self, **query):
            return ([], 1) if query.get("with_count") else []

        def get_attachment(self, field_name, id):
            asked.append((self.venue, id))
            return b"%PDF-1.7 body"

    kept = (harvest.MANIFEST, harvest.PDFS, harvest.source_for, harvest.time)
    try:
        with tempfile.TemporaryDirectory() as directory:
            harvest.MANIFEST = Path(directory) / "manifest.jsonl"
            harvest.PDFS = Path(directory) / "pdf"
            harvest.PDFS.mkdir()
            harvest.MANIFEST.write_text(
                manifest_line("aaa", "strong")
                + json.dumps({"id": "ccc", "tier": "strong", "venue": "W/2023"}) + "\n",
                encoding="utf-8")
            harvest.source_for = lambda venue: (PerVenue(venue), openreview.API2)
            harvest.time = NoClock()
            assert harvest.harvest_pdfs(delay=0) == 0
    finally:
        harvest.MANIFEST, harvest.PDFS, harvest.source_for, harvest.time = kept

    assert sorted(asked) == [("V/2026", "aaa"), ("W/2023", "ccc")]


def test_a_pause_without_a_batch_size_is_refused_for_pdfs_as_well():
    assert harvest.main(["harvest.py", "pdfs", "--pause", "3600"]) == 1


def test_a_tier_reaches_every_conference_until_venue_narrows_it():
    kept = harvest.MANIFEST
    try:
        with tempfile.TemporaryDirectory() as directory:
            harvest.MANIFEST = Path(directory) / "manifest.jsonl"
            harvest.MANIFEST.write_text(
                manifest_line("aaa", "strong")
                + json.dumps({"id": "ccc", "tier": "strong", "venue": "W/2025"}) + "\n",
                encoding="utf-8")
            everywhere = harvest.selected_rows((screen.STRONG,), None)
            narrowed = harvest.selected_rows((screen.STRONG,), None, "W/2025")
            assert sorted(row["id"] for row in everywhere) == ["aaa", "ccc"]
            assert [row["id"] for row in narrowed] == ["ccc"]
            try:
                harvest.selected_rows((screen.STRONG,), None, "X/1999")
                raise AssertionError("an absent venue should be refused, not answered empty")
            except ValueError as error:
                assert "X/1999" in str(error) and "W/2025" in str(error)
    finally:
        harvest.MANIFEST = kept


def test_a_limit_without_a_pause_is_still_one_batch_and_then_stop():
    reviewer = FakeReviewer()
    code, held, paused = fetched(["p0", "p1", "p2"], reviewer, limit=2)
    assert code == 0
    assert reviewer.asked == ["p0", "p1"]
    assert paused == []


def test_the_pause_is_measured_from_the_start_of_the_batch_not_its_end():
    clock = NoClock()
    kept = harvest.time
    try:
        harvest.time = clock
        clock.now = 900.0
        harvest.sleep_until_next_batch(3600, 0.0, 4)
    finally:
        harvest.time = kept
    assert clock.paused == [2700.0]


def test_a_batch_slower_than_the_pause_starts_the_next_one_without_waiting():
    clock = NoClock()
    kept = harvest.time
    try:
        harvest.time = clock
        clock.now = 5000.0
        harvest.sleep_until_next_batch(3600, 0.0, 4)
    finally:
        harvest.time = kept
    assert clock.paused == []


def test_a_paper_whose_request_failed_is_asked_about_again_next_batch():
    reviewer = FakeReviewer(refuse_first=2)
    code, held, _ = fetched(["p0", "p1", "p2"], reviewer, limit=3, pause=60)
    assert code == 1
    assert reviewer.asked == ["p0", "p1", "p2", "p0", "p1"]
    assert sorted(held.by_paper) == ["p0", "p1", "p2"]


def test_batches_that_answer_for_nothing_stop_the_loop_instead_of_waiting_out_the_clock():
    reviewer = FakeReviewer(refuse_first=10 ** 6)
    code, held, paused = fetched(["p0", "p1"], reviewer, limit=1, pause=3600)
    assert code == 1
    assert held.by_paper == {}
    assert len(paused) == harvest.DEAD_BATCHES - 1


def test_a_pause_without_a_batch_size_is_refused_rather_than_ignored():
    assert harvest.main(["harvest.py", "reviews", "V/2026", "--pause", "3600"]) == 1
    assert harvest.main(["harvest.py", "reviews", "V/2026", "--pause", "0", "--limit", "5"]) == 1


def test_a_venue_identifier_becomes_exactly_one_store_file_name():
    assert store.store_name("ICLR.cc/2025/Conference") == "ICLR.cc-2025-Conference.jsonl"
    assert store.store_name("ICLR.cc/2023/Conference") != store.store_name("ICLR.cc/2024/Conference")


def test_a_review_store_row_survives_the_round_trip():
    def check(directory):
        held = store.load_reviews(harvest.REVIEWS)
        assert held.count("aaa") == 2
        assert held.rating("aaa") == 7.0
        assert "printed text" in " ".join(held.texts("aaa"))
        return 0

    with_corpus([manifest_line("aaa", "weak")],
                [review_line("aaa", "r1", {"summary": "relies on printed text"}, 8.0),
                 review_line("aaa", "r2", {"summary": "a shortcut"}, 6.0)], check)


def test_a_paper_the_fetch_found_no_review_for_is_not_asked_about_again():
    def check(directory):
        path = harvest.REVIEWS / store.store_name("V/2026")
        assert store.reviewed_ids(path) == {"aaa", "bbb"}
        assert store.load_reviews(harvest.REVIEWS).count("bbb") == 0
        return 0

    with_corpus([manifest_line("aaa", "weak"), manifest_line("bbb", "weak")],
                [review_line("aaa", "r1", {"summary": "a shortcut"}),
                 review_line("bbb", "bbb-none", {})], check)


def test_the_same_review_written_by_two_runs_is_counted_once():
    def check(directory):
        assert store.load_reviews(harvest.REVIEWS).count("aaa") == 1
        return 0

    with_corpus([manifest_line("aaa", "weak")],
                [review_line("aaa", "r1", {"summary": "a shortcut"}),
                 review_line("aaa", "r1", {"summary": "a shortcut"})], check)


def test_rescreen_folds_the_reviews_in_without_touching_the_network():
    def check(directory):
        assert harvest.rescreen() == 0
        row = list(store.load(harvest.MANIFEST).rows)[0]
        assert row["reviews"] == len(AUDIT_REVIEWS)
        assert row["tier"] == screen.STRONG
        assert row["title"] == AUDIT[0]
        assert row["rules_version"] == screen.RULES_VERSION
        assert screen.side_score(row["subscores"], screen.REVIEW) > 0
        return 0

    with_corpus([manifest_line("aaa", "weak", title=AUDIT[0], abstract=AUDIT[1])],
                [review_line("aaa", "r%d" % number, {"summary": text})
                 for number, text in enumerate(AUDIT_REVIEWS)], check)


def test_rescreen_keeps_every_field_the_fetch_wrote():
    def check(directory):
        harvest.rescreen()
        row = list(store.load(harvest.MANIFEST).rows)[0]
        assert row["pdf"] == "https://openreview.net/pdf?id=aaa"
        assert row["keywords"] == ["interpretability"]
        return 0

    with_corpus([manifest_line("aaa", "weak", title=AUDIT[0], abstract=AUDIT[1],
                               keywords=["interpretability"],
                               pdf="https://openreview.net/pdf?id=aaa")], [], check)


def test_rank_writes_one_row_per_paper_ordered_by_the_total():
    def check(directory):
        target = directory / "ranking.csv"
        assert harvest.rank(target) == 0
        with target.open(encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        assert [row["id"] for row in rows] == ["aaa", "bbb"]
        assert [int(row["pos"]) for row in rows] == [1, 2]
        assert float(rows[0]["total"]) > float(rows[1]["total"])
        assert float(rows[0]["abstract"]) + float(rows[0]["review"]) == float(rows[0]["total"])
        assert rows[0]["url"].endswith("forum?id=aaa")
        return 0

    with_corpus([manifest_line("aaa", "weak", title=AUDIT[0], abstract=AUDIT[1]),
                 manifest_line("bbb", "weak", title=OPTIMISER[0], abstract=OPTIMISER[1])],
                [review_line("aaa", "r%d" % number, {"summary": text})
                 for number, text in enumerate(AUDIT_REVIEWS)], check)


def two_venue_corpus():
    return [manifest_line("aaa", "weak", title=AUDIT[0], abstract=AUDIT[1]),
            manifest_line("bbb", "weak", title=OPTIMISER[0], abstract=OPTIMISER[1]),
            json.dumps({"id": "ccc", "tier": "weak", "venue": "W/2025",
                        "title": AUDIT[0], "abstract": AUDIT[1]}) + "\n"]


def test_ranking_writes_one_file_per_venue_beside_the_combined_one():
    def check(directory):
        target = directory / "ranking.csv"
        assert harvest.rank(target) == 0
        assert target.exists()
        for venue, expected in (("V/2026", ["aaa", "bbb"]), ("W/2025", ["ccc"])):
            beside = harvest.venue_target(target, venue)
            assert beside.exists(), beside
            with beside.open(encoding="utf-8") as handle:
                rows = list(csv.DictReader(handle))
            assert [row["id"] for row in rows] == expected
            assert {row["venue"] for row in rows} == {venue}
        return 0

    with_corpus(two_venue_corpus(), [], check)


def test_a_position_in_a_per_venue_file_is_that_venue_s_own_position():
    def check(directory):
        target = directory / "ranking.csv"
        harvest.rank(target, "W/2025")
        with target.open(encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        assert [row["id"] for row in rows] == ["ccc"]
        assert rows[0]["pos"] == "1"
        assert not harvest.venue_target(target, "V/2026").exists()
        return 0

    with_corpus(two_venue_corpus(), [], check)


def test_a_venue_the_manifest_does_not_hold_is_refused_not_written_empty():
    def check(directory):
        target = directory / "ranking.csv"
        assert harvest.rank(target, "X/1999") == 1
        assert not target.exists()
        return 0

    with_corpus(two_venue_corpus(), [], check)


def test_a_half_fetched_venue_is_marked_in_the_table_not_in_a_footnote():
    assert harvest.reviewed_share([{"reviews": 2}, {"reviews": 0}]) == 0.5
    assert harvest.reviewed_share([{"reviews": 2}]) == 1.0
    assert harvest.reviewed_share([]) == 0.0
    row = harvest.venue_row("V/2026", [{"reviews": 0, "tier": screen.WEAK, "total": 1.0},
                                       {"reviews": 3, "tier": screen.STRONG, "total": 9.0}])
    assert "50.0%" in row


def test_one_venue_alone_gets_no_redundant_copy_of_itself():
    def check(directory):
        target = directory / "ranking.csv"
        assert harvest.rank(target) == 0
        assert list(directory.glob("ranking-*.csv")) == []
        return 0

    with_corpus([manifest_line("aaa", "weak", title=AUDIT[0], abstract=AUDIT[1])], [], check)


def test_the_ranking_is_replaced_in_one_step_like_every_other_artifact():
    def check(directory):
        target = directory / "ranking.csv"
        target.write_text("stare, nieprawdziwe dane\n", encoding="utf-8")
        assert harvest.rank(target) == 0
        assert list(directory.glob("*%s" % paths.PARTIAL)) == []
        with target.open(encoding="utf-8") as handle:
            assert [row["id"] for row in csv.DictReader(handle)] == ["aaa"]
        return 0

    with_corpus([manifest_line("aaa", "weak", title=AUDIT[0], abstract=AUDIT[1])], [], check)


def test_every_ranking_column_comes_from_a_rule_set_or_is_named_once():
    for rules in screen.RULESETS:
        for name in rules.groups:
            assert name in harvest.RANK_COLUMNS
    assert len(set(harvest.RANK_COLUMNS)) == len(harvest.RANK_COLUMNS)


def test_a_freshly_harvested_row_says_it_has_seen_no_review_yet():
    _, rows, _ = harvested([Note("aaa", *AUDIT)])
    assert rows[0]["reviews"] == 0
    assert rows[0]["rating"] is None
    assert rows[0]["score"] == screen.screen(*AUDIT).score


def test_harvesting_records_the_rules_that_screened_each_row():
    _, rows, _ = harvested([Note("aaa", *AUDIT)])
    assert rows[0]["rules_version"] == screen.RULES_VERSION
    assert store.row_complaint(rows[0]) is None


def test_harvesting_skips_papers_the_manifest_already_holds():
    _, rows, metas = harvested([Note("aaa", *AUDIT), Note("bbb", *AUDIT)],
                               already=manifest_line("aaa", "weak"))
    assert [row["id"] for row in rows] == ["aaa", "bbb"]
    assert rows[0]["tier"] == "weak"
    assert metas == ["bbb.json"]


def test_one_unusable_identifier_from_the_api_does_not_end_the_whole_harvest():
    code, rows, metas = harvested([Note("aaa", *AUDIT),
                                   Note("../../etc/passwd", *AUDIT),
                                   Note("bbb", *AUDIT)])
    assert [row["id"] for row in rows] == ["aaa", "bbb"]
    assert metas == ["aaa.json", "bbb.json"]
    assert code == 1


def test_harvesting_closes_an_interrupted_last_line_before_appending_to_it():
    _, rows, _ = harvested([Note("bbb", *AUDIT)],
                           already='{"id": "aaa", "tier": "weak", "venue": "V/2026"}')
    assert [row["id"] for row in rows] == ["aaa", "bbb"]


def test_accepted_only_harvest_never_needs_the_submission_invitation():
    class Connection:
        def get_all_notes(self, **query):
            return sorted(query)

        def get_group(self, venue_id):
            raise AssertionError("accepted-only reached the invitation lookup")

    assert openreview.submissions(Connection(), "V/2026") == ["content"]


def test_a_paper_identifier_is_never_used_as_a_path():
    assert store.safe_id("aBcD3f") == "aBcD3f"
    for hostile in ("../../etc/passwd", "a/b", "", None, "x" * 70):
        try:
            store.safe_id(hostile)
            raise AssertionError("accepted %r" % (hostile,))
        except ValueError:
            pass


def test_an_interrupted_manifest_line_does_not_swallow_the_next_row():
    original = harvest.MANIFEST
    try:
        with tempfile.TemporaryDirectory() as directory:
            harvest.MANIFEST = Path(directory) / "manifest.jsonl"
            harvest.MANIFEST.write_text(
                manifest_line("kept", "weak") + '{"id": "interrupted", "tier"',
                encoding="utf-8")
            assert store.close_unterminated_line(harvest.MANIFEST)
            with harvest.MANIFEST.open("a", encoding="utf-8") as log:
                log.write(manifest_line("next", "weak"))
            assert [row["id"] for row in harvest.manifest_rows()] == ["kept", "next"]
    finally:
        harvest.MANIFEST = original


def test_a_manifest_that_already_ends_cleanly_is_left_alone():
    original = harvest.MANIFEST
    try:
        with tempfile.TemporaryDirectory() as directory:
            harvest.MANIFEST = Path(directory) / "manifest.jsonl"
            harvest.MANIFEST.write_text(manifest_line("kept", "weak"), encoding="utf-8")
            assert not store.close_unterminated_line(harvest.MANIFEST)
            assert harvest.MANIFEST.read_text(encoding="utf-8") == manifest_line("kept", "weak")
    finally:
        harvest.MANIFEST = original


def test_only_a_real_pdf_is_accepted_as_one():
    assert harvest.is_pdf(b"%PDF-1.7\nrest")
    assert not harvest.is_pdf(b"<!doctype html><html>403</html>")
    assert not harvest.is_pdf(b"")
    assert not harvest.is_pdf(None)


def test_credentials_use_the_documented_environment_names():
    previous = {name: os.environ.get(name)
                for name in (openreview.USERNAME_ENV, openreview.PASSWORD_ENV)}
    os.environ[openreview.USERNAME_ENV] = "user@example.org"
    os.environ[openreview.PASSWORD_ENV] = "secret"
    try:
        assert openreview.credentials() == ("user@example.org", "secret")
    finally:
        for name, value in previous.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def test_openreview_client_contract_names_every_api_method_the_harvester_uses():
    class Complete:
        get_all_notes = get_notes = get_group = get_invitation = get_attachment = lambda: None

    class Api:
        OpenReviewClient = Complete

    class Package:
        api = Api

    assert openreview.missing_methods(Package) == ()


def test_pdf_download_uses_the_documented_attachment_endpoint():
    class Connection:
        calls = []

        def get_attachment(self, **arguments):
            self.calls.append(arguments)
            return b"%PDF-1.7 body"

    with tempfile.TemporaryDirectory() as folder:
        target = Path(folder) / "paper.pdf"
        connection = Connection()
        assert harvest.fetch_pdf(connection, "paper-id", target, delay=0)
        assert connection.calls == [{"field_name": "pdf", "id": "paper-id"}]
        assert target.read_bytes().startswith(b"%PDF")


def test_only_transient_openreview_failures_are_retried():
    transient = Exception({"status": 429})
    permanent = Exception({"status": 403})
    assert openreview.retryable(transient)
    assert not openreview.retryable(permanent)


def test_pdf_download_retries_a_transient_failure():
    class Connection:
        attempts = 0

        def get_attachment(self, **arguments):
            self.attempts += 1
            if self.attempts == 1:
                raise Exception({"status": 503})
            return b"%PDF-1.7 body"

    with tempfile.TemporaryDirectory() as folder:
        target = Path(folder) / "paper.pdf"
        connection = Connection()
        assert harvest.fetch_pdf(connection, "paper-id", target, retries=2, delay=0)
        assert connection.attempts == 2


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


def test_one_damaged_manifest_line_does_not_lose_the_rest(monkeypatch=None):
    with tempfile.TemporaryDirectory() as folder:
        manifest = Path(folder) / "manifest.jsonl"
        manifest.write_text(manifest_line("a", "strong")
                            + '{"id": "b", "tier": "wea\n'
                            + manifest_line("c", "weak"), encoding="utf-8")
        original = harvest.MANIFEST
        harvest.MANIFEST = manifest
        try:
            assert {row["id"] for row in harvest.manifest_rows()} == {"a", "c"}
            assert store.ids_in(manifest) == {"a", "c"}
        finally:
            harvest.MANIFEST = original


def test_check_accepts_a_baseline_so_a_batch_validates_once():
    db = sample_db()
    baseline = set(validate.errors(db))
    assert check.schema_errors(candidate(), db, baseline) == ("XX-999", [])


class FakeGroup:
    def __init__(self, content):
        self.content = content


class FakeConnection:
    def __init__(self, content):
        self.group = FakeGroup(content)

    def get_group(self, venue_id):
        return self.group


ICLR_2024_REVIEW = {"summary": "the paper analyzes CLIP", "strengths": "clear",
                    "weaknesses": "narrow", "questions": "why?",
                    "soundness": "3 good", "rating": "8: accept, good paper",
                    "confidence": "4: You are confident but not absolutely certain"}

ICLR_2023_REVIEW = {"summary_of_the_paper": "the authors probe a model",
                    "strength_and_weaknesses": "well written but narrow",
                    "clarity,_quality,_novelty_and_reproducibility": "clear and reproducible",
                    "summary_of_the_review": "a solid empirical study",
                    "recommendation": "6: marginally above the acceptance threshold",
                    "technical_novelty_and_significance": "2: marginally novel"}


def test_review_prose_keeps_the_text_a_reviewer_wrote_whatever_the_form_called_it():
    assert sorted(openreview.prose_fields(ICLR_2024_REVIEW)) == \
        ["questions", "strengths", "summary", "weaknesses"]
    assert sorted(openreview.prose_fields(ICLR_2023_REVIEW)) == \
        ["clarity,_quality,_novelty_and_reproducibility", "strength_and_weaknesses",
         "summary_of_the_paper", "summary_of_the_review"]


def test_a_rating_is_read_off_the_front_of_whatever_the_form_calls_it():
    assert openreview.rating_of(ICLR_2024_REVIEW) == 8.0
    assert openreview.rating_of(ICLR_2023_REVIEW) == 6.0
    assert openreview.rating_of({"summary": "no score here"}) is None


def test_a_note_counts_as_a_review_only_under_the_review_invitation():
    class Reply:
        def __init__(self, names):
            self.invitations = names

    assert openreview.is_review(Reply(["ICLR.cc/2024/Conference/Submission1/-/Official_Review"]))
    assert not openreview.is_review(Reply(["ICLR.cc/2024/Conference/Submission1/-/Comment"]))
    assert not openreview.is_review(Reply([]))


def test_api1_reads_acceptance_off_the_venue_field():
    class Submission:
        def __init__(self, venue):
            self.content = {"venue": venue}

    assert openreview.accepted(Submission("ICLR 2023 poster"))
    assert openreview.accepted(Submission("ICLR 2023 notable top 5%"))
    assert not openreview.accepted(Submission("Submitted to ICLR 2023"))
    assert not openreview.accepted(Submission("ICLR 2023 Withdrawn Submission"))
    assert not openreview.accepted(Submission(""))


class FakeAdapter:
    def __init__(self, retry):
        self.max_retries = retry


class FakeSession:
    def __init__(self, retry):
        self.adapters = {"https://": FakeAdapter(retry), "http://": FakeAdapter(retry)}


class Mounted:
    def __init__(self, retry):
        self.session = FakeSession(retry)


def test_one_throttled_request_can_no_longer_park_the_whole_run():
    from urllib3.util.retry import Retry

    connection = Mounted(Retry(total=10, backoff_factor=1, backoff_max=120,
                               status_forcelist=[429, 500, 502, 503, 504]))
    assert openreview.bound_waiting(connection) == 2
    for adapter in connection.session.adapters.values():
        bounded = adapter.max_retries
        assert bounded.respect_retry_after_header is False
        assert bounded.backoff_max <= openreview.RETRY_AFTER_CAP
        assert bounded.total == openreview.RETRY_TOTAL
        assert 429 in bounded.status_forcelist


def test_bounding_reports_how_many_adapters_it_actually_reached():
    class Bare:
        pass

    assert openreview.bound_waiting(Bare()) == 0
    assert openreview.bound_waiting(Mounted(object())) == 0


RATE_LIMITED_LOGIN = {
    "name": "RateLimitError",
    "message": "Too many requests: You have made 4 requests, surpassing the limit of 3 requests. "
               "Please try again in 54 seconds (2026-08-24-6210796)",
    "status": 429,
    "details": {"limit": 3, "remaining": 0},
}


def test_the_wait_a_refused_login_asks_for_is_read_off_the_refusal():
    class Refusal(Exception):
        pass

    assert openreview.asked_to_wait(Refusal(RATE_LIMITED_LOGIN)) == 54
    longer = dict(RATE_LIMITED_LOGIN, message="Please try again in 2 minutes and 6 seconds")
    assert openreview.asked_to_wait(Refusal(longer)) == 126
    vague = dict(RATE_LIMITED_LOGIN, message="Too many requests")
    assert openreview.asked_to_wait(Refusal(vague)) == openreview.LOGIN_WAIT
    capped = dict(RATE_LIMITED_LOGIN, message="Please try again in 90 minutes and 0 seconds")
    assert openreview.asked_to_wait(Refusal(capped)) == openreview.LOGIN_WAIT_CAP


def test_a_refusal_that_is_not_a_rate_limit_is_not_something_to_wait_out():
    class Refusal(Exception):
        pass

    assert openreview.asked_to_wait(Refusal({"status": 403, "message": "nope"})) is None
    assert openreview.asked_to_wait(Refusal("plain string")) is None
    assert openreview.asked_to_wait(Refusal()) is None


def test_the_api_generation_is_asked_for_rather_than_hardcoded_per_year():
    class Answering:
        def __init__(self, count):
            self.count = count

        def get_notes(self, **query):
            return [], self.count

    assert openreview.generation_of(Answering(2260), "ICLR.cc/2024/Conference") == openreview.API2
    assert openreview.generation_of(Answering(0), "ICLR.cc/2023/Conference") == openreview.API1


def test_submission_invitation_reads_the_name_from_the_venue_group():
    connection = FakeConnection({"submission_name": {"value": "Blind_Submission"}})
    assert openreview.submission_invitation(connection, "ICML.cc/2026/Conference") == \
        "ICML.cc/2026/Conference/-/Blind_Submission"


def test_submission_invitation_falls_back_when_the_group_does_not_name_it():
    for content in ({}, None, {"submission_name": None}):
        connection = FakeConnection(content)
        assert openreview.submission_invitation(connection, "V/2026").endswith("/-/Submission")


ANSWER = """considered:
- model: "GPT-4o"
  released: true
  why: "public API"
findings:
- title: "GPT-4o mislabels chart axes"
  models:
  - name: GPT-4o
entities:
- name: "MS COCO"
  kind: dataset
  citation: "Lin et al. Microsoft COCO: common objects in context, 2014."
"""


def test_a_fenced_answer_is_read_without_its_fence():
    answer = answers.read("```yaml\n" + ANSWER + "```")
    assert not answer.repaired
    assert answer.document[answers.FINDINGS][0]["title"] == "GPT-4o mislabels chart axes"


def test_unquoted_prose_containing_a_colon_is_repaired_not_rejected():
    raw = 'findings:\n- title: We compare two setups: greedy and sampled\n'
    answer = answers.read(raw)
    assert answer.repaired
    assert answer.document[answers.FINDINGS][0]["title"] == "We compare two setups: greedy and sampled"


def test_a_key_indented_by_one_space_is_repaired():
    raw = 'considered:\n- model: "A"\n released: true\nfindings: []\n'
    answer = answers.read(raw)
    assert answer.repaired
    assert answer.document["considered"][0]["released"] is True


def test_an_answer_without_findings_is_refused():
    for raw in ("[]", "notes: nothing", "findings: 3", "", "just prose"):
        try:
            answers.read(raw)
            raise AssertionError("accepted %r" % raw)
        except answers.Unreadable:
            pass


def test_an_empty_result_still_carries_marks_from_the_considered_block():
    answer = answers.read('considered:\n- model: "Chinchilla"\nfindings: []\n')
    assert "chinchilla" in answers.named_in(answer.document)


def test_a_name_shared_by_every_paper_cannot_decide_the_match():
    corpus = {"a": "bert and gpt4 and widgetron", "b": "bert and gpt4", "c": "bert and gpt4"}
    document = {answers.FINDINGS: [], "considered": [{"model": "BERT"}, {"model": "GPT-4"}]}
    assert not answers.match(document, corpus).confident()
    document["considered"].append({"model": "Widgetron"})
    found = answers.match(document, corpus)
    assert found.paper == "a" and found.confident()


def test_matching_an_empty_corpus_is_not_confident():
    assert not answers.match({answers.FINDINGS: []}, {}).confident()


def test_a_citation_present_in_the_source_is_confirmed():
    pages = ("nothing here", "Lin et al. Microsoft COCO: common objects in context, 2014.")
    verdict = citations.judge("Lin et al. Microsoft COCO: common objects in context, 2014.", pages)
    assert verdict.state == citations.CONFIRMED
    assert verdict.page == 2 and verdict.usable()


def test_a_citation_absent_from_the_source_is_rejected():
    verdict = citations.judge("Herzog et al. OlmoEarth foundation models, 2026.",
                              ("a page about something else entirely",))
    assert verdict.state == citations.REJECTED
    assert not verdict.usable()


def test_an_empty_citation_is_absent_not_rejected():
    for blank in ("", None, "   "):
        verdict = citations.judge(blank, ("any page",))
        assert verdict.state == citations.ABSENT
        assert verdict.usable()


def test_an_identifier_is_read_out_of_a_citation_when_the_paper_printed_one():
    assert citations.identifier_in("Lin et al. arxiv.org/abs/1405.0312, 2014.") == "arXiv:1405.0312"
    assert citations.identifier_in("Lin et al. In ECCV, 2014.") == ""


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


def with_manifest(body, rows):
    original = harvest.MANIFEST
    try:
        with tempfile.TemporaryDirectory() as directory:
            harvest.MANIFEST = Path(directory) / "manifest.jsonl"
            harvest.MANIFEST.write_text(rows, encoding="utf-8")
            return body()
    finally:
        harvest.MANIFEST = original


def test_an_option_without_a_value_is_refused_instead_of_widening_the_download():
    taken = []
    original = harvest.harvest_pdfs
    harvest.harvest_pdfs = lambda *args, **kw: taken.append((args, kw)) or 0
    try:
        for argv in (["h", "pdfs", "--ids"], ["h", "pdfs", "--limit"], ["h", "pdfs", "--tier"]):
            assert harvest.main(argv) == 1
        assert taken == []
    finally:
        harvest.harvest_pdfs = original


def test_an_unknown_option_or_tier_is_refused():
    assert harvest.main(["h", "pdfs", "--everything"]) == 1
    assert harvest.main(["h", "pdfs", "--tier", "enormous"]) == 1
    assert harvest.main(["h", "pdfs", "--limit", "bad"]) == 1
    assert harvest.main(["h", "pdfs", "--limit", "0"]) == 1


def test_the_default_tiers_are_still_used_when_no_option_is_given():
    assert harvest.chosen_tiers(None) == harvest.DOWNLOAD_TIERS
    assert harvest.chosen_tiers("weak,strong") == ("weak", "strong")
    assert harvest.positive("5") == 5 and harvest.positive(None) is None


def test_a_manifest_row_of_the_wrong_shape_is_skipped_not_crashed_on():
    rows = '42\n"text"\n{"id": "a"}\n' + manifest_line("b", "strong")
    kept, weak = with_manifest(
        lambda: (harvest.manifest_rows(), harvest.selected_rows(("weak",), None)), rows)
    assert [row["id"] for row in kept] == ["b"]
    assert weak == []


def test_a_repeated_manifest_id_is_collapsed_to_its_last_row():
    rows = manifest_line("a", "weak") + manifest_line("a", "strong")
    kept = with_manifest(lambda: harvest.manifest_rows(), rows)
    assert len(kept) == 1 and kept[0]["tier"] == "strong"


def test_requesting_only_ids_the_manifest_does_not_hold_is_a_failure():
    rows = manifest_line("real", "strong")
    assert with_manifest(lambda: harvest.harvest_pdfs(ids=["ghost"]), rows) == 1
    assert with_manifest(lambda: harvest.selected_rows((), ["real", "ghost"]), rows) != []


def answer_with(**fields):
    document = {"findings": [{"models": [], "methods": [], "datasets": [], "related_work": []}]}
    document["findings"][0].update(fields.pop("finding", {}))
    document.update(fields)
    return document


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


def test_a_proposal_carries_the_citation_and_its_verdict():
    db = sample_db()
    documents = {"p1": answer_with(
        finding={"methods": [{"name": "Widgetron"}]},
        entities=[{"name": "Widgetron", "kind": "method",
                   "citation": "Ada Lovelace. The widgetron, 1843."}])}
    verdicts = {("p1", text.flatten("Widgetron")): citations.CONFIRMED}
    found = proposals.gather(documents, db.entities, verdicts)
    assert found[0].state == citations.CONFIRMED
    assert found[0].citation.startswith("Ada Lovelace")
    assert found[0].kind == "method"


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


def test_a_registry_name_carrying_a_qualifier_is_found_by_its_bare_form():
    def qualified(entities):
        entities["method:mds"] = {"type": graph_json.METHOD,
                                  "name": "MDS (Mahalanobis Distance-based Score)",
                                  "anchor": "https://example.org"}
    index = index_of(graph_json.METHOD, entities=qualified)
    assert link.resolve("MDS", index).slug == "method:mds"


def test_a_name_matching_nothing_is_still_a_miss():
    models, variants, parents = variant_setup()
    found, variant = link.resolve_model("Widgetron 9000", models, variants, parents)
    assert (found.kind, variant) == (link.MISS, "")


def test_an_identifier_printed_the_way_a_bibliography_prints_it_is_recognised():
    for line, wanted in (
            ("Tsung-Yi Lin et al. Microsoft COCO. arXiv preprint arXiv:1405.0312, 2014.",
             "arXiv:1405.0312"),
            ("A. Author. A paper. arXiv 2305.12345v2, 2023.", "arXiv:2305.12345"),
            ("B. Author. A paper. Nature, 2024. 10.1038/s41586-024-07421-0",
             "DOI:10.1038/s41586-024-07421-0")):
        assert citations.identifier_in(line) == wanted


def test_a_citation_with_no_identifier_yields_none_rather_than_a_guess():
    for line in ("R. R. Selvaraju et al. Grad-CAM. In ICCV, 2017.",
                 "K. He et al. Deep residual learning. In CVPR, pages 770-778, 2016."):
        assert citations.identifier_in(line) == ""
        assert citations.anchor_from(line) == ""


def test_a_recognised_identifier_becomes_a_canonical_anchor_url():
    assert citations.anchor_from("arXiv preprint arXiv:1405.0312, 2014.") == \
        "https://arxiv.org/abs/1405.0312"
    assert citations.anchor_from("see https://openreview.net/forum?id=AbC123 for details") == \
        "https://openreview.net/forum?id=AbC123"


def test_a_url_form_and_a_bare_form_of_one_identifier_agree():
    assert citations.identifier_in("https://arxiv.org/abs/1405.0312") == \
        citations.identifier_in("arXiv:1405.0312")


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


def test_a_concept_definition_with_an_uncited_colon_is_repaired_rather_than_lost():
    raw = ('findings:\n- title: "CLIP leans on the caption"\n  concepts: []\n'
           'concepts_considered:\n'
           '- finding: CLIP leans on the caption: the image barely matters\n'
           '  name: Positional bias\n'
           '  definition: the output depends on position: answer A beats answer D\n'
           '  instead_of: shortcut is closest: it is about data, not position\n')
    answer = answers.read(raw)
    assert answer.repaired
    entry = answer.document["concepts_considered"][0]
    assert entry["finding"] == "CLIP leans on the caption: the image barely matters"
    assert entry["definition"] == "the output depends on position: answer A beats answer D"


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


def test_a_citation_too_short_to_carry_information_is_never_confirmed():
    page = "this paper reports on iccv 2017 and everything else besides"
    for thin in ("This paper.", "In ICCV, 2017."):
        assert citations.judge(thin, (page,)).state == citations.PARTIAL


def test_a_block_that_is_not_a_list_of_records_is_refused_at_the_gate():
    for raw in ('findings: []\nentities: "oops"\n',
                'findings:\n- "just a string"\n',
                'findings: []\nconsidered: 5\n',
                'findings: []\nentities:\n- null\n',
                'findings: []\nconcepts_considered: "no"\n'):
        try:
            answers.read(raw)
            raise AssertionError("accepted %r" % raw)
        except answers.Unreadable:
            pass


def test_every_reader_of_a_block_goes_through_the_same_gate():
    document = {"findings": [], "entities": "oops"}
    for call in (lambda: answers.named_in(document),
                 lambda: proposals.entity_notes(document),
                 lambda: proposals.gather({"p": document}, {})):
        try:
            call()
        except answers.Unreadable:
            continue
        except Exception as error:
            raise AssertionError("leaked %s instead of Unreadable" % type(error).__name__)


SPLIT_ENTITIES = {
    "model:llama-2": {"type": "model", "name": "Llama 2"},
    "variant:llama-2-7b-chat": {"type": "variant", "name": "Llama 2 7B Chat",
                                "parent": "model:llama-2"},
    "method:probe": {"type": "method", "name": "Probing classifiers"},
    "concept:shortcut": {"type": "concept", "name": "Shortcut"},
}


SPLIT_ROLES = {"methods": ["primary"], "datasets": ["eval"],
               "related_work": ["builds-on", "context"]}


def split_of(findings, entities=None, papers={"p1": "source:the-paper"}):
    documents = {"p1": {"findings": findings, "entities": entities or []}}
    return splitter.split(documents, SPLIT_ENTITIES, papers, "IC", {"concept:shortcut"},
                          SPLIT_ROLES)


def test_a_finding_whose_title_names_an_unresolved_model_is_refused_not_reassigned():
    kept, dropped, refused = split_of([
        {"title": "Safety survives in Llama-2-chat-vl but not elsewhere",
         "description": "d", "models": [{"name": "Llama-2-chat-vl"}, {"name": "Llama 2"}]}])
    assert kept == []
    assert refused[0].why == "the title names a model that resolved to nothing"


def test_a_secondary_unresolved_model_does_not_refuse_the_finding():
    kept, dropped, refused = split_of([
        {"title": "Retrieval heads are sparse across open models",
         "description": "d", "models": [{"name": "Llama-2-chat-vl"}, {"name": "Llama 2"}]}])
    assert refused == []
    assert [m["ref"] for m in kept[0].record["models"]] == ["model:llama-2"]


def test_a_finding_with_no_resolvable_model_is_refused():
    kept, dropped, refused = split_of([
        {"title": "Something about a private model", "description": "d",
         "models": [{"name": "Widgetron"}]}])
    assert kept == [] and refused[0].why == "no model resolved to a registry entry"


def test_a_role_from_the_wrong_field_never_reaches_the_record():
    kept, _, _ = split_of([{"title": "A claim", "description": "d",
                            "models": [{"name": "Llama 2"}],
                            "methods": [{"name": "Probing classifiers", "role": "builds-on"}]}])
    assert kept[0].record["methods"] == [{"ref": "method:probe"}]


def test_an_unresolved_method_is_dropped_but_the_finding_survives():
    kept, dropped, refused = split_of([
        {"title": "Llama 2 leans on position", "description": "d",
         "models": [{"name": "Llama 2"}],
         "methods": [{"name": "Probing classifiers"}, {"name": "Adam optimizer"}]}])
    assert [m["ref"] for m in kept[0].record["methods"]] == ["method:probe"]
    assert [d.name for d in dropped] == ["Adam optimizer"]


def test_a_checkpoint_name_becomes_a_model_reference_carrying_its_variant():
    kept, _, _ = split_of([{"title": "A claim", "description": "d",
                            "models": [{"name": "Llama 2 7B Chat"}]}])
    assert kept[0].record["models"] == [{"ref": "model:llama-2",
                                         "variant": "variant:llama-2-7b-chat"}]


def test_related_work_naming_an_existing_node_becomes_a_reference_not_an_inline_copy():
    kept, _, _ = split_of([{"title": "A claim", "description": "d",
                            "models": [{"name": "Llama 2"}],
                            "related_work": [{"name": "Probing classifiers", "role": "builds-on"}]}])
    assert kept[0].record["related_work"] == [{"ref": "method:probe", "role": "builds-on"}]


def test_related_work_is_written_inline_with_the_anchor_from_its_citation():
    kept, _, _ = split_of(
        [{"title": "A claim", "description": "d", "models": [{"name": "Llama 2"}],
          "related_work": [{"name": "Earlier work", "role": "builds-on"}]}],
        entities=[{"name": "Earlier work", "citation": "A. Author. Earlier work. arXiv:2301.00001, 2023."}])
    assert kept[0].record["related_work"] == [
        {"name": "Earlier work", "anchor": "https://arxiv.org/abs/2301.00001", "role": "builds-on"}]


def test_related_work_with_no_anchor_is_dropped_and_the_drop_is_reported():
    kept, dropped, _ = split_of([{"title": "A claim", "description": "d",
                                  "models": [{"name": "Llama 2"}],
                                  "related_work": [{"name": "Geva et al. (2022)"},
                                                   {"name": "A real title about registers"}]}])
    assert kept[0].record["related_work"] == []
    assert [(item.field, item.name) for item in dropped] == [
        ("related_work", "Geva et al. (2022)"),
        ("related_work", "A real title about registers")]


def test_related_work_takes_a_bare_url_from_its_citation_when_there_is_no_identifier():
    kept, _, _ = split_of(
        [{"title": "A claim", "description": "d", "models": [{"name": "Llama 2"}],
          "related_work": [{"name": "Meta Llama 3 announcement", "role": "context"}]}],
        entities=[{"name": "Meta Llama 3 announcement",
                   "citation": "meta. introducing llama 3, 2024. url https://ai.meta.com/ blog/meta-llama-3/."}])
    assert kept[0].record["related_work"] == [
        {"name": "Meta Llama 3 announcement", "anchor": "https://ai.meta.com/blog/meta-llama-3/",
         "role": "context"}]


def test_an_anchor_gets_its_case_back_from_the_paper_text_the_citation_lowercased():
    documents = {"p1": {"findings": [{"title": "A claim", "description": "d",
                                      "models": [{"name": "Llama 2"}],
                                      "related_work": [{"name": "Logit lens"}]}],
                        "entities": [{"name": "Logit lens",
                                      "citation": "nostalgebraist. the logit lens. https://www.alignmentforum.org/posts/ackrb8wdpdan6v6ru/x"}]}}
    kept, _, _ = splitter.split(
        documents, SPLIT_ENTITIES, {"p1": "source:the-paper"}, "IC", {"concept:shortcut"},
        SPLIT_ROLES,
        texts={"p1": "as shown in https://www.alignmentforum.org/posts/AcKRB8wDpdaN6v6ru/x"})
    assert kept[0].record["related_work"][0]["anchor"] \
        == "https://www.alignmentforum.org/posts/AcKRB8wDpdaN6v6ru/x"


def test_an_author_citation_that_does_carry_an_anchor_is_kept_because_it_is_clickable():
    kept, _, _ = split_of(
        [{"title": "A claim", "description": "d", "models": [{"name": "Llama 2"}],
          "related_work": [{"name": "Geva et al. (2022)"}]}],
        entities=[{"name": "Geva et al. (2022)", "citation": "M. Geva. A paper. arXiv:2203.14680, 2022."}])
    assert kept[0].record["related_work"][0]["anchor"] == "https://arxiv.org/abs/2203.14680"


def test_every_written_record_is_from_automatic_extraction():
    kept, _, _ = split_of([{"title": "A claim", "description": "d",
                            "models": [{"name": "Llama 2"}]}])
    assert kept[0].record["extracted_by"] == "automatic-extraction"
    assert kept[0].identifier == "IC-001"


def test_a_concept_outside_the_closed_list_never_reaches_the_record():
    kept, _, _ = split_of([{"title": "A claim", "description": "d",
                            "models": [{"name": "Llama 2"}],
                            "concepts": ["concept:shortcut", "concept:vibes"]}])
    assert kept[0].record["concepts"] == [{"ref": "concept:shortcut"}]


def test_one_concept_parser_reads_every_shape_the_model_has_produced():
    finding = {"concepts": ["concept:shortcut", "shortcut", {"concept": "shortcut"},
                            {"id": "concept:shortcut"}, {"name": "shortcut"}, "", {}, None]}
    values = [value for value, _ in answers.concepts_of(finding)]
    assert values == ["concept:shortcut"] * 5


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


def test_a_finding_with_no_source_for_its_paper_is_refused():
    kept, _, refused = split_of([{"title": "A claim", "description": "d",
                                  "models": [{"name": "Llama 2"}]}], papers={})
    assert kept == [] and refused[0].why == "no source entry for the paper"


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


def test_a_paper_that_yielded_no_finding_needs_no_source_entry():
    documents = {"p1": {"findings": [], "entities": []},
                 "p2": {"findings": [{"title": "A claim", "description": "d",
                                      "models": [{"name": "Llama 2"}]}], "entities": []}}
    kept, _, _ = splitter.split(documents, SPLIT_ENTITIES,
                                {"p1": "source:empty", "p2": "source:the-paper"},
                                "IC", set(), SPLIT_ROLES)
    needed = {link["ref"] for candidate in kept for link in candidate.record["sources"]}
    assert needed == {"source:the-paper"}


def test_a_bare_model_is_dropped_when_the_same_model_appears_with_a_variant():
    kept, _, _ = split_of([{"title": "A claim", "description": "d",
                            "models": [{"name": "Llama 2"}, {"name": "Llama 2 7B Chat"}]}])
    assert kept[0].record["models"] == [{"ref": "model:llama-2",
                                         "variant": "variant:llama-2-7b-chat"}]


def test_a_bare_model_survives_when_no_variant_of_it_is_named():
    kept, _, _ = split_of([{"title": "A claim", "description": "d",
                            "models": [{"name": "Llama 2"}]}])
    assert kept[0].record["models"] == [{"ref": "model:llama-2"}]


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


def test_findings_from_one_paper_name_each_other():
    from modelpedia.ingest import split as splitter
    kept = [splitter.Candidate("IC-001", "paperA", {"title": "a"}),
            splitter.Candidate("IC-002", "paperA", {"title": "b"}),
            splitter.Candidate("IC-003", "paperB", {"title": "c"})]
    linked = {item.identifier: item.record.get("related_findings")
              for item in splitter.cross_linked(kept)}
    assert linked["IC-001"] == ["IC-002"]
    assert linked["IC-002"] == ["IC-001"]
    assert linked["IC-003"] is None


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


def test_a_source_slug_from_a_title_that_starts_with_a_digit_is_valid():
    from modelpedia.ingest import split as splitter
    from modelpedia import schema
    slug = splitter.slug_from("3D-PC: a benchmark for visual perspective taking")
    assert schema.SLUG.fullmatch(slug), slug
    assert slug.endswith("-3d")


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
