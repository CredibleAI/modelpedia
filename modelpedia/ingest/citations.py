from typing import NamedTuple

from modelpedia.ingest import verification

CONFIRMED = "confirmed"
PARTIAL = "partial"
REJECTED = "rejected"
ABSENT = "absent"

CONFIRMED_AT = 0.90
PARTIAL_AT = 0.60
MIN_WORDS = 6


class Judgement(NamedTuple):
    state: str
    overlap: float
    page: int

    def usable(self):
        return self.state != REJECTED


def overlap_with(citation, pages):
    wanted = verification.content_words(citation)
    if not wanted:
        return 0.0, 0
    best, where = 0.0, 0
    for number, page in enumerate(pages, start=1):
        share = len(wanted & verification.content_words(page)) / len(wanted)
        if share > best:
            best, where = share, number
    return best, where


def judge(citation, pages):
    if not str(citation or "").strip():
        return Judgement(ABSENT, 0.0, 0)
    share, page = overlap_with(citation, pages)
    if len(verification.content_words(citation)) < MIN_WORDS:
        return Judgement(PARTIAL if share else REJECTED, round(share, 3), page)
    if share >= CONFIRMED_AT:
        return Judgement(CONFIRMED, round(share, 3), page)
    if share >= PARTIAL_AT:
        return Judgement(PARTIAL, round(share, 3), page)
    return Judgement(REJECTED, round(share, 3), page)


def identifier_in(citation):
    found = verification.anchor_identifier(citation)
    return "%s:%s" % found if found else ""


def anchor_from(citation):
    return verification.anchor_url(verification.anchor_identifier(citation))
