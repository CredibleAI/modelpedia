import pytest

from modelpedia.ingest import anchors


def dblp_answering(*records):
    return lambda query: list(records)


def crossref_answering(*items):
    return lambda citation: list(items)


def exploding(_):
    raise anchors.LookupFailed("index refused")


CITATION = "He et al. Deep Residual Learning for Image Recognition. CVPR 2016."


def test_a_link_list_is_read_whether_it_is_one_string_or_many():
    assert anchors.links_of({"ee": "https://a"}) == ["https://a"]
    assert anchors.links_of({"ee": ["https://a", "https://b"]}) == ["https://a", "https://b"]
    assert anchors.links_of({}) == []


def test_the_best_scoring_title_wins_the_dblp_lookup():
    ask = dblp_answering({"title": "Something else entirely", "ee": "https://wrong"},
                         {"title": "Deep Residual Learning for Image Recognition", "ee": "https://doi.org/right"})
    score, title, url = anchors.dblp_match(CITATION, ask)
    assert title == "Deep Residual Learning for Image Recognition"
    assert url == "https://doi.org/right"
    assert score > anchors.DBLP_MATCH_AT


def test_an_index_that_never_answers_is_a_failure_not_a_zero_score():
    with pytest.raises(anchors.LookupFailed):
        anchors.dblp_match(CITATION, exploding)


def test_crossref_titles_arrive_as_a_list_and_are_joined():
    ask = crossref_answering({"title": ["Deep Residual Learning for Image Recognition"], "DOI": "10.1/x"})
    score, title, url = anchors.crossref_match(CITATION, ask)
    assert title == "Deep Residual Learning for Image Recognition"
    assert url == anchors.doi_url("10.1/x")
    assert score > 0


def test_dblp_is_preferred_when_it_clears_its_own_threshold():
    index, score, title, url = anchors.resolved(
        CITATION,
        dblp_answering({"title": "Deep Residual Learning for Image Recognition", "ee": "https://doi.org/dblp"}),
        crossref_answering({"title": ["Deep Residual Learning for Image Recognition"], "DOI": "10.1/crossref"}))
    assert index == "dblp"
    assert url == "https://doi.org/dblp"


def test_crossref_answers_when_dblp_has_nothing_that_clears_the_bar():
    index, score, title, url = anchors.resolved(
        CITATION,
        dblp_answering({"title": "A totally unrelated paper", "ee": "https://weak"}),
        crossref_answering({"title": ["Deep Residual Learning for Image Recognition"], "DOI": "10.1/crossref"}))
    assert index == "crossref"
    assert url == anchors.doi_url("10.1/crossref")


def test_one_index_failing_is_survivable_and_both_failing_is_not():
    index, score, title, url = anchors.resolved(
        CITATION, exploding,
        crossref_answering({"title": ["Deep Residual Learning for Image Recognition"], "DOI": "10.1/x"}))
    assert index == "crossref"

    with pytest.raises(anchors.LookupFailed):
        anchors.resolved(CITATION, exploding, exploding)


def test_a_weak_match_from_both_still_returns_the_better_of_the_two():
    index, score, title, url = anchors.resolved(
        CITATION,
        dblp_answering({"title": "Unrelated", "ee": "https://d"}),
        crossref_answering({"title": ["Deep Residual Learning for Quantum Cheese Recognition"],
                            "DOI": "10.1/c"}))
    assert index == "crossref"
    assert score < anchors.CROSSREF_MATCH_AT


def test_no_citation_report_on_disk_means_no_citations_rather_than_a_crash(tmp_path):
    assert anchors.confirmed_citations(tmp_path / "absent.jsonl", {}, {"method:x"}) == {}
