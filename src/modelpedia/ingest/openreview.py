import os
import re
import time
from importlib.metadata import PackageNotFoundError, version

PACKAGE = "openreview-py"
BASEURL = "https://api2.openreview.net"
V1_BASEURL = "https://api.openreview.net"
PDF_URL = "https://openreview.net/pdf?id=%s"
FORUM_URL = "https://openreview.net/forum?id=%s"

API1 = "v1"
API2 = "v2"

USERNAME_ENV = "OPENREVIEW_USERNAME"
PASSWORD_ENV = "OPENREVIEW_PASSWORD"

CLIENT_METHODS = ("get_all_notes", "get_notes", "get_group", "get_invitation",
                  "get_attachment")

BLIND_SUBMISSION = "%s/-/Blind_Submission"
REVIEW_INVITATION = "Official_Review"

REFUSED = ("submitted to", "withdrawn", "desk reject", "rejected")

NOT_PROSE = frozenset((
    "title", "rating", "confidence", "soundness", "presentation", "contribution",
    "correctness", "recommendation", "technical_novelty_and_significance",
    "empirical_novelty_and_significance", "flag_for_ethics_review",
    "code_of_conduct", "reproducibility", "student_author", "venue", "venueid",
    "id", "forum", "replyto", "number", "signatures", "paper_title", "review_id",
))

RETRY_AFTER_CAP = 60
RETRY_TOTAL = 3

LOGIN_RETRIES = 4
LOGIN_WAIT = 60
LOGIN_WAIT_CAP = 300
ASKED_TO_WAIT = re.compile(r"try again in (?:(\d+) minutes? and )?(\d+) seconds?", re.I)

RATING_FIELDS = ("rating", "recommendation")
LEADING_NUMBER = re.compile(r"-?\d+(?:\.\d+)?")


class Unavailable(Exception):
    pass


def value_of(field):
    return field.get("value") if isinstance(field, dict) else field


def flat_content(content):
    return {key: value_of(field) for key, field in (content or {}).items()}


def pdf_url(paper_id):
    return PDF_URL % paper_id


def forum_url(paper_id):
    return FORUM_URL % paper_id


def package_version():
    try:
        return version(PACKAGE)
    except PackageNotFoundError:
        return "unknown"


def module():
    try:
        import openreview
        return openreview
    except ImportError:
        raise Unavailable("%s is not importable by this interpreter" % PACKAGE)


def missing_methods(openreview=None):
    client_type = (openreview or module()).api.OpenReviewClient
    return tuple(name for name in CLIENT_METHODS
                 if not callable(getattr(client_type, name, None)))


def credentials_present():
    return all(os.environ.get(name) for name in (USERNAME_ENV, PASSWORD_ENV))


def credentials():
    missing = [name for name in (USERNAME_ENV, PASSWORD_ENV) if not os.environ.get(name)]
    if missing:
        raise Unavailable("set %s in the environment; never put them in the repo"
                          % " and ".join(missing))
    return os.environ[USERNAME_ENV], os.environ[PASSWORD_ENV]


def capped_retry(retry, cap, total):
    bounded = retry.new(total=total)
    bounded.respect_retry_after_header = False
    bounded.backoff_max = min(getattr(bounded, "backoff_max", cap) or cap, cap)
    return bounded


def bound_waiting(connection, cap=RETRY_AFTER_CAP, total=RETRY_TOTAL):
    """`%s` mounts a urllib3 retry that honours `Retry-After` literally and without a ceiling, so
    a 429 carrying `Retry-After: 3600` parks the whole process inside a single request for an
    hour -- invisibly, having written nothing and logged nothing, and up to `total` times over.
    Measured 2026-08-23 on ICLR 2024: fifty minutes asleep in one call, before the first paper was
    ever asked for, while the caller's own batch pause never got to run.

    So a request may wait a little and then fail, and a spent quota is waited out by the caller,
    where the wait is counted, printed, interruptible and resumable. Returns how many adapters
    were bounded, because a policy nobody can confirm was applied is not a policy.""" % PACKAGE
    adapters = getattr(getattr(connection, "session", None), "adapters", {})
    bounded = 0
    for adapter in adapters.values():
        retry = getattr(adapter, "max_retries", None)
        if not hasattr(retry, "respect_retry_after_header"):
            continue
        adapter.max_retries = capped_retry(retry, cap, total)
        bounded += 1
    return bounded


def asked_to_wait(error):
    payload = error.args[0] if error.args else None
    if not isinstance(payload, dict) or payload.get("status") != 429:
        return None
    found = ASKED_TO_WAIT.search(str(payload.get("message") or ""))
    if not found:
        return LOGIN_WAIT
    minutes, seconds = found.groups()
    return min(int(minutes or 0) * 60 + int(seconds), LOGIN_WAIT_CAP)


def client_for(generation, retries=LOGIN_RETRIES, sleeper=None):
    openreview = module()
    username, password = credentials()
    build = (openreview.api.OpenReviewClient if generation == API2 else openreview.Client)
    baseurl = BASEURL if generation == API2 else V1_BASEURL
    sleeper = sleeper or time.sleep
    for attempt in range(retries):
        try:
            connection = build(baseurl=baseurl, username=username, password=password)
            break
        except openreview.MfaRequiredException:
            raise Unavailable("this account requires multi-factor authentication; "
                              "%s cannot complete it non-interactively" % PACKAGE)
        except openreview.OpenReviewException as error:
            wait = asked_to_wait(error)
            if wait is None or attempt + 1 == retries:
                raise Unavailable("OpenReview rejected the login: %s" % error)
            print("  login rate-limited, waiting %ds before attempt %d of %d"
                  % (wait, attempt + 2, retries))
            sleeper(wait + 1)
    bound_waiting(connection)
    return connection


def connect():
    return client_for(API2)


def connect_v1():
    return client_for(API1)


def generation_of(connection, venue_id):
    _, count = connection.get_notes(content={"venueid": venue_id}, limit=1, with_count=True)
    return API2 if count else API1


def submission_invitation(connection, venue_id):
    group = connection.get_group(venue_id)
    name = value_of((group.content or {}).get("submission_name")) or "Submission"
    return "%s/-/%s" % (venue_id, name)


def accepted(note):
    venue = str(value_of((note.content or {}).get("venue")) or "").strip().lower()
    return bool(venue) and not any(venue.startswith(mark) or mark in venue for mark in REFUSED)


def submissions(connection, venue_id, accepted_only=True, generation=API2):
    if generation == API1:
        notes = connection.get_all_notes(invitation=BLIND_SUBMISSION % venue_id)
        return [note for note in notes if accepted(note)] if accepted_only else notes
    if accepted_only:
        return connection.get_all_notes(content={"venueid": venue_id})
    return connection.get_all_notes(invitation=submission_invitation(connection, venue_id))


def invitations_of(note):
    named = getattr(note, "invitations", None) or getattr(note, "invitation", None)
    return [str(name) for name in (named if isinstance(named, list) else [named]) if name]


def is_review(note):
    return any(name.rsplit("/", 1)[-1] == REVIEW_INVITATION for name in invitations_of(note))


def reviews_of(connection, forum_id):
    return [note for note in connection.get_all_notes(forum=forum_id) if is_review(note)]


def venue_review_count(connection, venue_id):
    _, count = connection.get_notes(invitation="%s/-/%s" % (venue_id, REVIEW_INVITATION),
                                    parent_invitations=True, limit=1, with_count=True)
    return count


def prose_fields(content):
    return {key: value for key, value in flat_content(content).items()
            if isinstance(value, str) and key not in NOT_PROSE and value.strip()}


def rating_of(content):
    flat = flat_content(content)
    for field in RATING_FIELDS:
        found = LEADING_NUMBER.match(str(flat.get(field) or "").strip())
        if found:
            return float(found.group(0))
    return None


def error_status(error):
    payload = error.args[0] if error.args else None
    return payload.get("status") if isinstance(payload, dict) else None


def retryable(error):
    status = error_status(error)
    return isinstance(error, OSError) or status == 429 or \
        isinstance(status, int) and status >= 500
