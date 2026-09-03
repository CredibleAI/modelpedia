import os

from modelpedia.ingest import citations
from modelpedia.ingest import openreview
from tests.helpers import Reply


def test_openreview_unwraps_the_value_envelope_of_api_v2():
    assert openreview.value_of({"value": "a title"}) == "a title"
    assert openreview.value_of("a title") == "a title"
    assert openreview.flat_content({"title": {"value": "t"}, "plain": "p"}) == \
        {"title": "t", "plain": "p"}
    assert openreview.flat_content(None) == {}

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

def test_accepted_only_harvest_never_needs_the_submission_invitation():
    class Connection:
        def get_all_notes(self, **query):
            return sorted(query)

        def get_group(self, venue_id):
            raise AssertionError("accepted-only reached the invitation lookup")

    assert openreview.submissions(Connection(), "V/2026") == ["content"]

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

def test_only_transient_openreview_failures_are_retried():
    transient = Exception({"status": 429})
    permanent = Exception({"status": 403})
    assert openreview.retryable(transient)
    assert not openreview.retryable(permanent)

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

def test_a_recognised_identifier_becomes_a_canonical_anchor_url():
    assert citations.anchor_from("arXiv preprint arXiv:1405.0312, 2014.") == \
        "https://arxiv.org/abs/1405.0312"
    assert citations.anchor_from("see https://openreview.net/forum?id=AbC123 for details") == \
        "https://openreview.net/forum?id=AbC123"
