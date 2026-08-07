import copy
import os
import tempfile
from pathlib import Path

import check
import harvest
from modelpedia.build import database
from modelpedia import paths
from modelpedia import graph as graph_json
from modelpedia.ingest import link
from modelpedia.build import validate
from modelpedia.ingest import screen
from modelpedia.ingest import text
from tests.test_build import sample_db

CANDIDATE = {
    "id": "XX-999",
    "review_status": "draft",
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


def test_a_small_caps_heading_split_by_pdftotext_is_rejoined():
    doc = text.from_text("t", "A BSTRACT\nwe study I NTRODUCTION and the NL-E YE benchmark")
    assert text.contains(doc, "abstract")
    assert text.contains(doc, "introduction")
    assert text.contains(doc, "NL-Eye")
    assert "abstract" in doc.text
    assert "introduction" in doc.text


def test_rejoining_small_caps_leaves_ordinary_prose_alone():
    assert text.normalise("A model was trained") == "a model was trained"
    assert text.normalise("see Table A for details") == "see table a for details"
    assert text.normalise("the AI Act") == "the ai act"


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


def test_harvest_unwraps_the_value_envelope_of_api_v2():
    assert harvest.value_of({"value": "a title"}) == "a title"
    assert harvest.value_of("a title") == "a title"


def test_harvest_manifest_row_carries_the_screening():
    screening = screen.screen(*AUDIT)

    class Note:
        id = "aBcD"
        content = {"title": {"value": AUDIT[0]}, "abstract": {"value": AUDIT[1]}}

    row = harvest.row_for(Note(), harvest.flatten_content(Note()), "V/2026", screening)
    assert row["id"] == "aBcD"
    assert row["tier"] == screen.STRONG
    assert row["pdf"].endswith("aBcD")
    assert not row["has_pdf"]
    assert any(signal.startswith("model:") for signal in row["signals"])


def test_harvest_downloads_only_tiers_it_was_asked_for():
    assert screen.WEAK not in harvest.DOWNLOAD_TIERS
    assert screen.STRONG in harvest.DOWNLOAD_TIERS


def test_an_explicit_id_list_selects_rows_and_reports_the_ones_it_cannot_find():
    original = harvest.MANIFEST
    try:
        with tempfile.TemporaryDirectory() as directory:
            harvest.MANIFEST = Path(directory) / "manifest.jsonl"
            harvest.MANIFEST.write_text(
                '{"id": "aaa", "tier": "weak"}\n{"id": "bbb", "tier": "strong"}\n',
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
        assert harvest.read_ids(good) == ["aaa", "bbb"]
        hostile = Path(directory) / "hostile.txt"
        hostile.write_text("aaa\n../../etc/passwd\n", encoding="utf-8")
        try:
            harvest.read_ids(hostile)
            raise AssertionError("accepted a path as an identifier")
        except SystemExit:
            pass


def test_accepted_only_harvest_never_needs_the_submission_invitation():
    class Connection:
        def get_all_notes(self, **query):
            return sorted(query)

        def get_group(self, venue_id):
            raise AssertionError("accepted-only reached the invitation lookup")

    assert harvest.submissions(Connection(), "V/2026") == ["content"]


def test_a_paper_identifier_is_never_used_as_a_path():
    assert harvest.safe_id("aBcD3f") == "aBcD3f"
    for hostile in ("../../etc/passwd", "a/b", "", None, "x" * 70):
        try:
            harvest.safe_id(hostile)
            raise AssertionError("accepted %r" % (hostile,))
        except ValueError:
            pass


def test_an_interrupted_manifest_line_does_not_swallow_the_next_row():
    original = harvest.MANIFEST
    try:
        with tempfile.TemporaryDirectory() as directory:
            harvest.MANIFEST = Path(directory) / "manifest.jsonl"
            harvest.MANIFEST.write_text('{"id": "kept"}\n{"id": "interrupted"',
                                        encoding="utf-8")
            assert harvest.close_unterminated_line()
            with harvest.MANIFEST.open("a", encoding="utf-8") as log:
                log.write('{"id": "next"}\n')
            assert [row["id"] for row in harvest.manifest_rows()] == ["kept", "next"]
    finally:
        harvest.MANIFEST = original


def test_a_manifest_that_already_ends_cleanly_is_left_alone():
    original = harvest.MANIFEST
    try:
        with tempfile.TemporaryDirectory() as directory:
            harvest.MANIFEST = Path(directory) / "manifest.jsonl"
            harvest.MANIFEST.write_text('{"id": "kept"}\n', encoding="utf-8")
            assert not harvest.close_unterminated_line()
            assert harvest.MANIFEST.read_text(encoding="utf-8") == '{"id": "kept"}\n'
    finally:
        harvest.MANIFEST = original


def test_only_a_real_pdf_is_accepted_as_one():
    assert harvest.is_pdf(b"%PDF-1.7\nrest")
    assert not harvest.is_pdf(b"<!doctype html><html>403</html>")
    assert not harvest.is_pdf(b"")
    assert not harvest.is_pdf(None)


def test_credentials_use_the_documented_environment_names():
    previous = {name: os.environ.get(name)
                for name in (harvest.USERNAME_ENV, harvest.PASSWORD_ENV)}
    os.environ[harvest.USERNAME_ENV] = "user@example.org"
    os.environ[harvest.PASSWORD_ENV] = "secret"
    try:
        assert harvest.credentials() == ("user@example.org", "secret")
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

    assert harvest.client_contract(Package) == ()


def test_pdf_download_uses_the_documented_attachment_endpoint():
    class Connection:
        calls = []

        def get_attachment(self, **arguments):
            self.calls.append(arguments)
            return b"%PDF-1.7 body"

    import tempfile
    with tempfile.TemporaryDirectory() as folder:
        target = Path(folder) / "paper.pdf"
        connection = Connection()
        assert harvest.fetch_pdf(connection, "paper-id", target, delay=0)
        assert connection.calls == [{"field_name": "pdf", "id": "paper-id"}]
        assert target.read_bytes().startswith(b"%PDF")


def test_only_transient_openreview_failures_are_retried():
    transient = Exception({"status": 429})
    permanent = Exception({"status": 403})
    assert harvest.retryable(transient)
    assert not harvest.retryable(permanent)


def test_pdf_download_retries_a_transient_failure():
    class Connection:
        attempts = 0

        def get_attachment(self, **arguments):
            self.attempts += 1
            if self.attempts == 1:
                raise Exception({"status": 503})
            return b"%PDF-1.7 body"

    import tempfile
    with tempfile.TemporaryDirectory() as folder:
        target = Path(folder) / "paper.pdf"
        connection = Connection()
        assert harvest.fetch_pdf(connection, "paper-id", target, retries=2, delay=0)
        assert connection.attempts == 2


def test_a_pdf_is_written_atomically(tmp=None):
    import tempfile
    with tempfile.TemporaryDirectory() as folder:
        target = Path(folder) / "paper.pdf"
        harvest.save_bytes(b"%PDF-1.7 body", target)
        assert target.read_bytes() == b"%PDF-1.7 body"
        assert list(Path(folder).glob("*" + paths.PARTIAL)) == []


def test_an_interrupted_write_is_cleared_before_the_next_run():
    import tempfile
    with tempfile.TemporaryDirectory() as folder:
        leftover = Path(folder) / ("half" + paths.PARTIAL)
        leftover.write_bytes(b"%PDF truncated")
        harvest.clear_partials(Path(folder))
        assert not leftover.exists()


def test_one_damaged_manifest_line_does_not_lose_the_rest(monkeypatch=None):
    import tempfile
    with tempfile.TemporaryDirectory() as folder:
        manifest = Path(folder) / "manifest.jsonl"
        manifest.write_text('{"id": "a", "tier": "strong"}\n'
                            '{"id": "b", "tier": "wea\n'
                            '{"id": "c", "tier": "weak"}\n', encoding="utf-8")
        original = harvest.MANIFEST
        harvest.MANIFEST = manifest
        try:
            assert {row["id"] for row in harvest.manifest_rows()} == {"a", "c"}
            assert harvest.seen_ids() == {"a", "c"}
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
    assert harvest.submission_invitation(connection, "ICML.cc/2026/Conference") == \
        "ICML.cc/2026/Conference/-/Blind_Submission"


def test_submission_invitation_falls_back_when_the_group_does_not_name_it():
    for content in ({}, None, {"submission_name": None}):
        connection = FakeConnection(content)
        assert harvest.submission_invitation(connection, "V/2026").endswith("/-/Submission")
