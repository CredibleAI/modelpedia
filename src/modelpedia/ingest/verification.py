import re
from decimal import Decimal, InvalidOperation
from typing import NamedTuple

from modelpedia import graph as graph_json
from modelpedia.ingest import link
from modelpedia import record_keys as keys
from modelpedia import schema
from modelpedia.ingest import text as textutil

FOUND = "found"
MISSING = "missing"
LOCATED = "located"
REVIEW = "review"
SKIPPED = "skipped"

NUMBER = re.compile(
    r"(?<![A-Za-z])[-+−]?(?:(?:\d{1,3}(?:[ ,]\d{3})+|\d+)(?:[.,]\d+)?|[.,]\d+)"
    r"(?:[eE][-+−]?\d+)?%?"
)
ARXIV = re.compile(r"arxiv\.org/(?:abs|pdf)/(\d{4}\.\d{4,5})(?:v\d+)?", re.I)
DOI = re.compile(r"doi\.org/(10\.\d{4,9}/[^\s?#]+)", re.I)
OPENREVIEW = re.compile(r"openreview\.net/(?:forum|pdf)\?id=([A-Za-z0-9_-]+)", re.I)
BARE_ARXIV = re.compile(r"arxiv[:\s]\s*(\d{4}\.\d{4,5})(?:v\d+)?", re.I)
BARE_DOI = re.compile(r"(?<![\w/])(10\.\d{4,9}/[^\s,;]+)")
IDENTIFIERS = ((ARXIV, "arXiv"), (DOI, "DOI"), (OPENREVIEW, "OpenReview"),
               (BARE_ARXIV, "arXiv"), (BARE_DOI, "DOI"))
ANCHOR_URL = {"arXiv": "https://arxiv.org/abs/%s",
              "DOI": "https://doi.org/%s",
              "OpenReview": "https://openreview.net/forum?id=%s"}
WORD = re.compile(r"[a-z][a-z0-9-]+")
STOPWORDS = frozenset({
    "and", "the", "of", "to", "in", "by", "for", "as", "with", "at", "from", "than",
    "on", "all", "a", "an", "is", "are", "was", "were",
})
MIN_WORD = 3

CAVEAT_LOCATED = 0.50
CAVEAT_MISSING = 0.25


class Check(NamedTuple):
    subject: str
    state: str
    pages: tuple
    detail: str


class Mention(NamedTuple):
    token: str
    canonical: tuple
    context: frozenset


def content_words(value):
    return frozenset(word for word in WORD.findall(textutil.fold(str(value or "")))
                     if word not in STOPWORDS and len(word) >= MIN_WORD)


def canonical_number(token):
    value = token.strip().replace("−", "-")
    percent = value.endswith("%")
    value = value.rstrip("%").replace(" ", "")
    if "," in value and "." not in value:
        left, right = value.rsplit(",", 1)
        value = left + right if len(right) == 3 and left not in ("0", "+0", "-0") \
            else left + "." + right
    else:
        value = value.replace(",", "")
    try:
        return Decimal(value), percent
    except InvalidOperation:
        return None


def numbers_in(value):
    seen = set()
    found = []
    for token in NUMBER.findall(str(value or "")):
        canonical = canonical_number(token)
        if canonical is not None and canonical not in seen:
            seen.add(canonical)
            found.append((token, canonical))
    return found


def number_mentions(value):
    raw = str(value or "")
    folded = textutil.fold(raw)
    mentions = []
    for match in NUMBER.finditer(folded):
        canonical = canonical_number(match.group())
        if canonical is None:
            continue
        around = folded[max(0, match.start() - 48):match.start()] + " " + \
            folded[match.end():match.end() + 48]
        mentions.append(Mention(match.group(), canonical, content_words(around)))
    return mentions


def page_number_mentions(doc):
    by_number = {}
    for number, page in enumerate(doc.pages, start=1):
        for mention in number_mentions(page):
            by_number.setdefault(mention.canonical, []).append((number, mention.context))
    return by_number


def other_notation(canonical):
    value, percent = canonical
    return (value, not percent)


def number_checks(finding, doc):
    source = page_number_mentions(doc)
    checks = []
    claims = number_mentions(finding.get("key_metric"))
    if not claims:
        return [Check("key_metric", SKIPPED, (), "no numeric key metric")]
    seen = set()
    for claim in claims:
        if claim.canonical in seen:
            continue
        seen.add(claim.canonical)
        matches = tuple(source.get(claim.canonical, ()))
        unmarked = tuple(source.get(other_notation(claim.canonical), ()))
        pages = tuple(dict.fromkeys(page for page, _ in matches + unmarked))
        contextual = [context for _, context in matches if context & claim.context]
        if not pages:
            state = MISSING
            detail = "numeric value not found in extracted text"
        elif not matches:
            state = REVIEW
            detail = "numeric value occurs, but only without the claimed percent sign"
        elif claim.context and not contextual:
            state = REVIEW
            detail = "numeric value occurs, but nearby terminology differs"
        else:
            state = FOUND
            detail = "numeric value and nearby terminology occur in the source"
        checks.append(Check(claim.token, state, pages, detail))
    return checks


def linked_refs(finding):
    seen = set()
    for field in schema.LINK_FIELDS:
        for item in finding.get(field) or []:
            if isinstance(item, dict) and item.get(keys.REF):
                pair = field, item[keys.REF]
                if pair not in seen:
                    seen.add(pair)
                    yield pair
                variant = item.get(keys.VARIANT)
                if field == schema.MODELS_FIELD and variant \
                        and variant != graph_json.VARIANT_NOT_SPECIFIED:
                    pair = keys.VARIANTS, variant
                    if pair not in seen:
                        seen.add(pair)
                        yield pair


def entity_checks(finding, entities, doc):
    checks = []
    ignored = {"concepts", "sources"}
    for field, ref in linked_refs(finding):
        if field in ignored:
            continue
        entity = entities.get(ref)
        if entity is None:
            checks.append(Check(ref, MISSING, (), "unknown registry entity"))
            continue
        aliases = link.aliases(link.display_name(ref, entity))
        pages = tuple(sorted({page for alias in aliases
                              for page in textutil.pages_with(doc, alias)}))
        checks.append(Check(ref, FOUND if pages else REVIEW, pages,
                            "%s mention" % field if pages else "%s name not found" % field))
    return checks or [Check("entities", SKIPPED, (), "no linked entities to look for")]


def anchor_identifier(anchor):
    for pattern, kind in IDENTIFIERS:
        match = pattern.search(str(anchor or ""))
        if match:
            return kind, match.group(1).rstrip(".,;)")
    return None


def anchor_url(found):
    if not found:
        return ""
    kind, value = found
    return ANCHOR_URL[kind] % value


def anchor_checks(finding, entities, doc):
    checks = []
    for field, ref in linked_refs(finding):
        if field == "sources":
            continue
        entity = entities.get(ref) or {}
        identifier = anchor_identifier(entity.get("anchor"))
        if identifier is None:
            continue
        kind, value = identifier
        pages = textutil.pages_with(doc, value)
        checks.append(Check(ref, FOUND if pages else REVIEW, pages,
                            "%s identifier %s" % (kind, value)))
    return checks or [Check("anchors", SKIPPED, (), "no stable external identifiers to check")]


def best_page_for(wanted, doc):
    best_page, best_share = None, 0.0
    for number, page in enumerate(doc.pages, start=1):
        share = len(wanted & content_words(page)) / len(wanted)
        if share > best_share:
            best_page, best_share = number, share
    return best_page, best_share


def caveat_check(finding, doc):
    if not str(finding.get("caveat") or "").strip():
        return Check("caveat", SKIPPED, (), "no caveat recorded")
    wanted = content_words(finding.get("caveat"))
    if not wanted:
        return Check("caveat", REVIEW, (), "the caveat has no searchable terms")
    page, share = best_page_for(wanted, doc)
    detail = "%.0f%% of the caveat's terms occur on the closest page; " \
             "human judgment required" % (share * 100)
    if share >= CAVEAT_LOCATED:
        return Check("caveat", LOCATED, (page,), detail)
    if share >= CAVEAT_MISSING:
        return Check("caveat", REVIEW, (page,) if page else (), detail)
    return Check("caveat", MISSING, (), detail)


def run(finding, entities, doc):
    return {
        "numbers": number_checks(finding, doc),
        "entities": entity_checks(finding, entities, doc),
        "anchors": anchor_checks(finding, entities, doc),
        "caveat": [caveat_check(finding, doc)],
    }


def blocking(report):
    return [check for section in report.values() for check in section
            if check.state == MISSING]


def nothing_verified(report):
    return all(check.state == SKIPPED
               for section in report.values() for check in section)


def render(fid, report):
    lines = ["verification evidence for %s" % fid]
    for section, checks in report.items():
        lines += ["", section]
        for check in checks:
            where = "pages " + ", ".join(map(str, check.pages)) if check.pages else "no page"
            lines.append("  %-28s %-8s %-16s %s" %
                         (check.subject, check.state.upper(), where, check.detail))
    review = [check for section in report.values() for check in section
              if check.state == REVIEW]
    lines += ["", "%d blocking check(s), %d item(s) for review; "
              "this report never promotes a record" % (len(blocking(report)), len(review))]
    if nothing_verified(report):
        lines.append("this record offered nothing to check against the source, "
                     "so 0 blocking is not a pass")
    return "\n".join(lines)
