import copy
import json
import os
import tempfile
from pathlib import Path

import check
import harvest
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


def test_a_paper_auditing_a_named_model_screens_strong():
    assert screen.screen(*AUDIT).tier == screen.STRONG


def test_a_paper_with_nothing_to_do_with_explanation_screens_weak():
    assert screen.screen(*OPTIMISER).tier == screen.WEAK


def test_proposing_a_method_never_vetoes_a_paper_that_also_reports_findings():
    both = (AUDIT[0], AUDIT[1] + " We propose a new attribution method. Our approach "
                                 "outperforms prior work and is state-of-the-art.")
    assert screen.screen(*both).tier == screen.STRONG


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


def test_registry_names_raise_the_score():
    terms = frozenset({"moransi"})
    plain = screen.screen("spatial statistics", "we compute a statistic")
    known = screen.screen("spatial statistics", "we compute Moran's I", terms=terms)
    assert known.score > plain.score
    assert any(s.group == "registry" for s in known.signals)


def test_registry_terms_ignore_names_too_short_to_be_distinctive():
    entities = {"method:pca": {"type": graph_json.METHOD, "name": "PCA"},
                "method:knn": {"type": graph_json.METHOD, "name": "k-nearest neighbours"}}
    assert screen.registry_terms(entities) == frozenset({"knearestneighbours"})


def test_registry_matching_survives_punctuation_in_the_registered_name():
    entities = {"method:morans-i": {"type": graph_json.METHOD, "name": "Moran's I"}}
    terms = screen.registry_terms(entities)
    for spelling in ("Moran's I", "Morans I", "moran s i"):
        found = screen.screen("spatial statistics", "we compute %s here" % spelling, terms=terms)
        assert any(s.group == "registry" for s in found.signals), spelling


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
    assert row["tier"] == screen.STRONG
    assert row["pdf"].endswith("aBcD")
    assert not row["has_pdf"]
    assert any(signal.startswith("model:") for signal in row["signals"])


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
    first = screen.fingerprint(screen.GROUPS, knobs)
    assert first == screen.fingerprint(screen.GROUPS, dict(knobs))
    assert first != screen.fingerprint(screen.GROUPS, {"strong_at": 3.5})
    widened = dict(screen.GROUPS)
    widened["xai"] = screen.Group(2.0, screen.XAI.stems + ("newly added term",), screen.XAI.words)
    assert first != screen.fingerprint(widened, knobs)
    assert len(screen.RULES_VERSION) == screen.VERSION_LENGTH


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


def harvested(notes, already="", venue_id="V/2026"):
    kept = (harvest.MANIFEST, harvest.META, harvest.connect)

    class Connection:
        def get_all_notes(self, **query):
            return notes

    try:
        with tempfile.TemporaryDirectory() as directory:
            harvest.MANIFEST = Path(directory) / "manifest.jsonl"
            harvest.META = Path(directory) / "meta"
            if already:
                harvest.MANIFEST.write_text(already, encoding="utf-8")
            harvest.connect = lambda: Connection()
            code = harvest.harvest_meta(venue_id)
            return (code, list(store.load(harvest.MANIFEST).rows),
                    sorted(path.name for path in harvest.META.glob("*.json")))
    finally:
        harvest.MANIFEST, harvest.META, harvest.connect = kept


def test_harvesting_metadata_screens_each_note_and_writes_one_row_per_paper():
    code, rows, metas = harvested([Note("aaa", *AUDIT), Note("bbb", *OPTIMISER)])
    assert code == 0
    assert [row["id"] for row in rows] == ["aaa", "bbb"]
    assert rows[0]["tier"] == screen.STRONG
    assert metas == ["aaa.json", "bbb.json"]
    assert all(row["venue"] == "V/2026" for row in rows)
    assert all(row["pdf"].endswith(row["id"]) for row in rows)


def test_harvesting_screens_against_the_registries_so_a_named_entity_lifts_a_tier():
    terms = screen.registry_terms(database.load_registries())
    assert screen.screen(*OPTIMISER).tier == screen.WEAK
    assert screen.screen(*OPTIMISER, terms=terms).tier == screen.POSSIBLE
    _, rows, _ = harvested([Note("bbb", *OPTIMISER)])
    assert rows[0]["tier"] == screen.POSSIBLE
    assert any(signal.startswith("registry:") for signal in rows[0]["signals"])


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


def test_related_work_that_is_only_an_author_citation_with_no_anchor_is_dropped():
    kept, _, _ = split_of([{"title": "A claim", "description": "d",
                            "models": [{"name": "Llama 2"}],
                            "related_work": [{"name": "Geva et al. (2022)"},
                                             {"name": "Nostalgebraist (2020)"},
                                             {"name": "A real title about registers"}]}])
    assert [r["name"] for r in kept[0].record["related_work"]] == ["A real title about registers"]


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
