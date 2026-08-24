import contextlib
import json
import tempfile
from pathlib import Path

import yaml

import extract

CORPUS_CONSTANTS = ("PROMPTS", "ANSWERS", "TEXTS", "PDFS", "REPORT", "PROPOSED", "TAGS", "META")

CITATION = ("R. Selvaraju, M. Cogswell, A. Das. Grad-CAM: visual explanations "
            "from deep networks via gradient-based localization. In ICCV, 2017.")

UNRELATED = ("K. Autor, L. Autor. Colloidal coagulation of graphene heterostructures "
             "under infrared spectroscopy. In Journal of Unrelated Physics, 2003.")


@contextlib.contextmanager
def swapped_corpus():
    original = {name: getattr(extract, name) for name in CORPUS_CONSTANTS}
    with tempfile.TemporaryDirectory() as directory:
        root = Path(directory)
        extract.PROMPTS = root / "prompts"
        extract.ANSWERS = root / "answers"
        extract.TEXTS = root / "text"
        extract.PDFS = root / "pdf"
        extract.REPORT = root / "entities.jsonl"
        extract.PROPOSED = root / "proposed.jsonl"
        extract.TAGS = root / "tags"
        extract.META = root / "meta"
        try:
            yield root
        finally:
            for name, value in original.items():
                setattr(extract, name, value)


def answer_with(root, paper, entities):
    extract.ANSWERS.mkdir(parents=True, exist_ok=True)
    document = {"findings": [], "entities": entities}
    (extract.ANSWERS / ("%s.yaml" % paper)).write_text(
        yaml.safe_dump(document, allow_unicode=True, sort_keys=False), encoding="utf-8")


def text_with(root, paper, body):
    extract.TEXTS.mkdir(parents=True, exist_ok=True)
    (extract.TEXTS / ("%s.txt" % paper)).write_text(body, encoding="utf-8")


def test_no_arguments_prints_usage_and_exits_2():
    assert extract.main(["extract.py"]) == 2


def test_an_unknown_command_prints_usage_and_exits_2():
    assert extract.main(["extract.py", "bogus"]) == 2


def test_collect_needs_a_directory():
    assert extract.main(["extract.py", "collect"]) == 1


def test_collect_rejects_an_assignment_that_does_not_pair_a_file_with_a_paper():
    with tempfile.TemporaryDirectory() as directory:
        assert extract.main(["extract.py", "collect", directory, "oops"]) == 1
        assert extract.main(["extract.py", "collect", directory, "a.txt="]) == 1
        assert extract.main(["extract.py", "collect", directory, "=paper"]) == 1


def test_assignments_pair_files_with_papers():
    assert extract.assignments_from([]) == {}
    assert extract.assignments_from(["a.txt=p1", "b.md=p2"]) == {"a.txt": "p1", "b.md": "p2"}
    assert extract.assignments_from(["a=x=y"]) == {"a": "x=y"}


def test_propose_rejects_words_and_non_positive_numbers():
    assert extract.main(["extract.py", "propose", "abc"]) == 1
    assert extract.main(["extract.py", "propose", "0"]) == 1
    assert extract.main(["extract.py", "propose", "-3"]) == 1


def test_prompts_without_extracted_text_fails():
    with swapped_corpus():
        assert extract.main(["extract.py", "prompts"]) == 1


def test_verify_without_collected_answers_fails():
    with swapped_corpus():
        assert extract.main(["extract.py", "verify"]) == 1


def test_verify_confirms_a_citation_the_text_contains_and_exits_0():
    with swapped_corpus() as root:
        answer_with(root, "paperA", [{"name": "Grad-CAM", "kind": "method",
                                      "citation": CITATION}])
        text_with(root, "paperA", "First page.\fReferences\n%s\n" % CITATION)
        assert extract.main(["extract.py", "verify"]) == 0
        rows = [json.loads(line) for line in extract.REPORT.read_text().splitlines()]
        assert len(rows) == 1
        assert rows[0]["state"] == "confirmed"
        assert rows[0]["page"] == 2


def test_verify_rejects_a_citation_the_text_does_not_contain_and_exits_1():
    with swapped_corpus() as root:
        answer_with(root, "paperA", [{"name": "Grad-CAM", "kind": "method",
                                      "citation": UNRELATED}])
        text_with(root, "paperA", "First page.\fReferences\n%s\n" % CITATION)
        assert extract.main(["extract.py", "verify"]) == 1
        rows = [json.loads(line) for line in extract.REPORT.read_text().splitlines()]
        assert rows[0]["state"] == "rejected"


def test_verify_and_propose_share_one_judgement_for_the_same_citation():
    with swapped_corpus() as root:
        answer_with(root, "paperA", [{"name": "Grad-CAM", "kind": "method",
                                      "citation": CITATION}])
        text_with(root, "paperA", "First page.\fReferences\n%s\n" % CITATION)
        assert extract.main(["extract.py", "verify"]) == 0
        reported = json.loads(extract.REPORT.read_text().splitlines()[0])["state"]
        verdicts = extract.verdicts_for(extract.collected())
        assert verdicts[("paperA", "gradcam")] == reported == "confirmed"


def test_pages_of_reads_the_cached_text_instead_of_the_pdf():
    with swapped_corpus() as root:
        text_with(root, "paperA", "page one\fpage two")
        pages = extract.pages_of("paperA")
        assert len(pages) == 2
        assert "page two" in pages[1]


def test_an_empty_answer_directory_is_no_collected_answers():
    with swapped_corpus():
        extract.ANSWERS.mkdir(parents=True)
        assert extract.collected() == {}
        assert extract.main(["extract.py", "verify"]) == 1


ANSWER_A = """
considered:
- model: "CLIP"
  released: true
  why: "released"
findings:
- title: "CLIP loses 12% accuracy under blur"
  description: "measured"
  evidence_type: correlational
  key_metric: "12% under blur"
  caveat: ""
  models:
  - name: "CLIP"
  concepts: ["concept:shortcut"]
entities:
- name: "CLIP"
  kind: model
  citation: "CITATION"
"""

ANSWER_B = """
considered:
- model: "CLIP"
  released: true
  why: "released"
findings:
- title: "CLIP loses accuracy under blur"
  description: "measured"
  evidence_type: interventional
  key_metric: "99% drop"
  caveat: ""
  models:
  - name: "clip"
  concepts: []
"""

PAPER = "CLIP drops 12% under blur.\fReferences\nCITATION\n"


def with_citation(template, citation):
    return template.replace("CITATION", citation)


def answer_dir(root, name, bodies):
    folder = root / name
    folder.mkdir(parents=True, exist_ok=True)
    for paper, body in bodies.items():
        (folder / ("%s.txt" % paper)).write_text(body, encoding="utf-8")
    return folder


def test_compare_needs_two_directories():
    assert extract.main(["extract.py", "compare", "corpus/answers"]) == 1


def test_compare_refuses_a_directory_that_is_not_there():
    assert extract.main(["extract.py", "compare", "nowhere", "corpus/answers"]) == 1


def test_a_number_absent_from_the_paper_is_counted_missing_on_the_side_that_wrote_it():
    with swapped_corpus() as root:
        text_with(root, "paperA", with_citation(PAPER, CITATION))
        left = answer_dir(root, "left", {"paperA": with_citation(ANSWER_A, CITATION)})
        right = answer_dir(root, "right", {"paperA": ANSWER_B})
        rows = extract.comparison.rows(extract.raw_answers_in(left),
                                       extract.raw_answers_in(right), extract.document_of)
        assert rows[0].left.numbers["found"] == 1 and rows[0].left.numbers["missing"] == 0
        assert rows[0].right.numbers["missing"] == 1


def test_two_spellings_of_one_model_count_as_agreement():
    with swapped_corpus() as root:
        text_with(root, "paperA", with_citation(PAPER, CITATION))
        left = answer_dir(root, "left", {"paperA": with_citation(ANSWER_A, CITATION)})
        right = answer_dir(root, "right", {"paperA": ANSWER_B})
        rows = extract.comparison.rows(extract.raw_answers_in(left),
                                       extract.raw_answers_in(right), extract.document_of)
        assert rows[0].shared() == frozenset({"clip"})
        assert extract.comparison.agreement(rows)["of_left"] == 1.0


def test_a_paper_only_one_side_answered_is_left_out_of_the_comparison():
    with swapped_corpus() as root:
        text_with(root, "paperA", with_citation(PAPER, CITATION))
        text_with(root, "paperB", with_citation(PAPER, CITATION))
        left = answer_dir(root, "left", {"paperA": with_citation(ANSWER_A, CITATION),
                                         "paperB": with_citation(ANSWER_A, CITATION)})
        right = answer_dir(root, "right", {"paperA": ANSWER_B})
        rows = extract.comparison.rows(extract.raw_answers_in(left),
                                       extract.raw_answers_in(right), extract.document_of)
        assert [row.paper for row in rows] == ["paperA"]


def test_an_answer_that_will_not_parse_is_named_rather_than_crashing_the_run():
    with swapped_corpus() as root:
        text_with(root, "paperA", with_citation(PAPER, CITATION))
        left = answer_dir(root, "left", {"paperA": "not: [yaml"})
        right = answer_dir(root, "right", {"paperA": ANSWER_B})
        rows = extract.comparison.rows(extract.raw_answers_in(left),
                                       extract.raw_answers_in(right), extract.document_of)
        assert rows[0].left.unreadable and rows[0].left.findings == 0
        assert rows[0].right.findings == 1


def test_a_citation_the_paper_does_not_carry_is_not_confirmed():
    with swapped_corpus() as root:
        text_with(root, "paperA", with_citation(PAPER, CITATION))
        left = answer_dir(root, "left", {"paperA": with_citation(ANSWER_A, UNRELATED)})
        right = answer_dir(root, "right", {"paperA": ANSWER_B})
        rows = extract.comparison.rows(extract.raw_answers_in(left),
                                       extract.raw_answers_in(right), extract.document_of)
        assert rows[0].left.quotes["rejected"] == 1
        assert rows[0].right.quotes == {"confirmed": 0, "partial": 0, "rejected": 0, "absent": 0}


def test_a_codepoint_yaml_forbids_is_dropped_rather_than_losing_the_answer():
    from modelpedia.ingest import answers as answerlib
    raw = 'findings: []\nentities:\n- name: "Oracle"\n  citation: "a b￾ c"\n'
    answer = answerlib.read(raw)
    assert answer.repaired
    assert answer.document["entities"][0]["citation"] == "a b c"


def test_a_closed_quote_followed_by_more_text_is_re_encoded_not_lost():
    from modelpedia.ingest import answers as answerlib
    raw = ('findings:\n- title: "A"\n  key_metric: "20% better"; "and 43% better"\n'
           '  caveat: ""\n')
    answer = answerlib.read(raw)
    assert answer.repaired
    assert answer.document["findings"][0]["key_metric"].startswith('"20% better"; ')


def test_a_value_that_yaml_already_reads_is_left_alone():
    from modelpedia.ingest import answers as answerlib
    assert answerlib.one_scalar('  title: "plain"') == '  title: "plain"'
    assert answerlib.one_scalar("  name: [a, b]") == "  name: [a, b]"


def test_a_note_left_in_a_run_directory_is_not_an_answer():
    with swapped_corpus() as root:
        folder = answer_dir(root, "run", {"paperA": ANSWER_B})
        (folder / "_README.txt").write_text("what this run was", encoding="utf-8")
        (folder / "_log.jsonl").write_text("{}\n", encoding="utf-8")
        assert sorted(extract.raw_answers_in(folder)) == ["paperA"]


def test_a_file_named_after_a_paper_is_assigned_to_it():
    with swapped_corpus() as root:
        text_with(root, "paperA", with_citation(PAPER, CITATION))
        text_with(root, "paperB", "an unrelated paper about turbines\fReferences\n")
        folder = answer_dir(root, "run", {"paperB": with_citation(ANSWER_A, CITATION)})
        extract.main(["extract.py", "collect", str(folder)])
        assert (extract.ANSWERS / "paperB.yaml").exists()
        assert not (extract.ANSWERS / "paperA.yaml").exists()


def test_the_name_wins_when_content_says_nothing_confident():
    with swapped_corpus() as root:
        text_with(root, "paperA", with_citation(PAPER, CITATION))
        folder = answer_dir(root, "run", {"paperA": "considered: []\nfindings: []\n"})
        assert extract.main(["extract.py", "collect", str(folder)]) == 0
        assert (extract.ANSWERS / "paperA.yaml").exists()


def test_a_content_match_against_the_name_is_reported_and_the_name_still_wins():
    with swapped_corpus() as root:
        text_with(root, "paperA", "turbines and gearboxes\fReferences\n")
        text_with(root, "paperB", with_citation(PAPER, CITATION))
        folder = answer_dir(root, "run", {"paperA": with_citation(ANSWER_A, CITATION)})
        assert extract.main(["extract.py", "collect", str(folder)]) == 1
        assert (extract.ANSWERS / "paperA.yaml").exists()
        assert not (extract.ANSWERS / "paperB.yaml").exists()


def test_a_second_run_continues_the_numbering_instead_of_starting_over():
    with swapped_corpus() as root:
        findings = root / "findings"
        findings.mkdir(parents=True, exist_ok=True)
        original = extract.paths.FINDINGS
        extract.paths.FINDINGS = findings
        try:
            assert extract.highest_number("IC") == 0
            (findings / "IC-042.yaml").write_text("id: IC-042\n", encoding="utf-8")
            (findings / "IC-007.yaml").write_text("id: IC-007\n", encoding="utf-8")
            assert extract.highest_number("IC") == 42
        finally:
            extract.paths.FINDINGS = original


def test_a_paper_already_in_the_base_is_not_written_a_second_time():
    from modelpedia.ingest import split as splitter
    kept = [splitter.Candidate("IC-050", "paperA", {"sources": [{"ref": "source:a"}]}),
            splitter.Candidate("IC-051", "paperB", {"sources": [{"ref": "source:b"}]})]
    findings = {"IC-001": {"sources": [{"ref": "source:a"}]}}
    fresh, already = extract.without_written_sources(kept, findings)
    assert [item.identifier for item in fresh] == ["IC-051"]
    assert already == 1


def test_a_tag_outside_the_closed_list_is_reported_and_not_written():
    from modelpedia.ingest import tagging
    taken, invented = tagging.chosen({"concepts": [{"id": "concept:shortcut"},
                                                   {"id": "concept:made-up"}]},
                                     {"concept:shortcut"})
    assert taken == ["concept:shortcut"] and invented == ["concept:made-up"]


def test_retag_needs_a_directory():
    assert extract.main(["extract.py", "retag"]) == 1
    assert extract.main(["extract.py", "retag", "nowhere"]) == 1
