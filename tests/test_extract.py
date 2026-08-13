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
