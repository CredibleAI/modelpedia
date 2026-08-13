import os
from importlib.metadata import PackageNotFoundError, version

PACKAGE = "openreview-py"
BASEURL = "https://api2.openreview.net"
PDF_URL = "https://openreview.net/pdf?id=%s"

USERNAME_ENV = "OPENREVIEW_USERNAME"
PASSWORD_ENV = "OPENREVIEW_PASSWORD"

CLIENT_METHODS = ("get_all_notes", "get_notes", "get_group", "get_invitation",
                  "get_attachment")


class Unavailable(Exception):
    pass


def value_of(field):
    return field.get("value") if isinstance(field, dict) else field


def flat_content(content):
    return {key: value_of(field) for key, field in (content or {}).items()}


def pdf_url(paper_id):
    return PDF_URL % paper_id


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


def connect():
    openreview = module()
    username, password = credentials()
    try:
        return openreview.api.OpenReviewClient(baseurl=BASEURL, username=username,
                                               password=password)
    except openreview.MfaRequiredException:
        raise Unavailable("this account requires multi-factor authentication; "
                          "%s cannot complete it non-interactively" % PACKAGE)
    except openreview.OpenReviewException as error:
        raise Unavailable("OpenReview rejected the login: %s" % error)


def submission_invitation(connection, venue_id):
    group = connection.get_group(venue_id)
    name = value_of((group.content or {}).get("submission_name")) or "Submission"
    return "%s/-/%s" % (venue_id, name)


def submissions(connection, venue_id, accepted_only=True):
    if accepted_only:
        return connection.get_all_notes(content={"venueid": venue_id})
    return connection.get_all_notes(invitation=submission_invitation(connection, venue_id))


def error_status(error):
    payload = error.args[0] if error.args else None
    return payload.get("status") if isinstance(payload, dict) else None


def retryable(error):
    status = error_status(error)
    return isinstance(error, OSError) or status == 429 or \
        isinstance(status, int) and status >= 500
