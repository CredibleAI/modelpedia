
from modelpedia.ingest import answers
from modelpedia.ingest import citations
from modelpedia.ingest import link
from modelpedia.ingest import proposals
from modelpedia.ingest import split as splitter
from modelpedia.ingest import text
from tests.helpers import SPLIT_ENTITIES, SPLIT_ROLES, answer_with, candidate, sample_db, split_of


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

def test_a_url_form_and_a_bare_form_of_one_identifier_agree():
    assert citations.identifier_in("https://arxiv.org/abs/1405.0312") == \
        citations.identifier_in("arXiv:1405.0312")

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

def test_a_finding_with_no_source_for_its_paper_is_refused():
    kept, _, refused = split_of([{"title": "A claim", "description": "d",
                                  "models": [{"name": "Llama 2"}]}], papers={})
    assert kept == [] and refused[0].why == "no source entry for the paper"

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

def test_a_source_slug_from_a_title_that_starts_with_a_digit_is_valid():
    from modelpedia.ingest import split as splitter
    from modelpedia import schema
    slug = splitter.slug_from("3D-PC: a benchmark for visual perspective taking")
    assert schema.SLUG.fullmatch(slug), slug
    assert slug.endswith("-3d")
