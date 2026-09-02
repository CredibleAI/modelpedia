import json
import re

from modelpedia import graph as graph_json
from modelpedia import record_keys as keys
from modelpedia.ingest import citations
from modelpedia.ingest import link
from modelpedia.ingest import manifest
from modelpedia.ingest import verification

DBLP_MATCH_AT = 0.80
CROSSREF_MATCH_AT = 0.85
MIN_TITLE_WORDS = 4

VENUE = re.compile(r"^(in|arxiv|proceedings|advances|url|http|accessed|pp\.|vol)", re.I)
NOISE = re.compile(r"\b(et al|arxiv preprint|url|accessed)\b", re.I)
PUNCTUATION = re.compile(r"[^a-z0-9 \-']")
SPRINGER = re.compile(r"^[^:]{0,180}?\.?:\s+(.+)$")
SENTENCE = re.compile(r"(?<=\.)\s+")

QUERY_WORDS = 3
QUERY_CHARS = 170
QUERIES = 2
CITATION_CHARS = 320

PREFERRED_HOSTS = ("doi.org", "arxiv.org")


def segments(citation):
    body = " ".join(str(citation or "").split())
    parts = [part.strip(" ,.") for part in SENTENCE.split(body)]
    springer = SPRINGER.match(body)
    if springer:
        parts.append(springer.group(1).split(". ")[0].strip(" ,."))
    return [part for part in parts
            if len(part.split()) >= QUERY_WORDS and not VENUE.match(part)]


def queries(citation):
    found, parts = [], segments(citation)
    for part in parts[1:1 + QUERIES]:
        query = PUNCTUATION.sub(" ", NOISE.sub(" ", part).lower())
        query = " ".join(word for word in query.split() if len(word) > 1)[:QUERY_CHARS]
        if query and query not in found:
            found.append(query)
    return found[:QUERIES]


def match_score(title, citation):
    wanted = verification.content_words(title)
    if len(wanted) < MIN_TITLE_WORDS:
        return 0.0
    return len(wanted & verification.content_words(citation)) / len(wanted)


def bibliographic(citation):
    body = NOISE.sub(" ", " ".join(str(citation or "").split()))
    return " ".join(body.split())[:CITATION_CHARS]


def doi_url(doi):
    value = str(doi or "").strip()
    return "https://doi.org/%s" % value if value else ""


def url_from(links):
    for host in PREFERRED_HOSTS:
        for link in links:
            if host in link:
                return link.replace("http://", "https://")
    return links[0] if links else ""


def missing_anchor(entities):
    return sorted(key for key, entity in entities.items()
                  if entity["type"] in graph_json.ANCHORED_TYPES
                  and not entity.get(keys.ANCHOR))


class LookupFailed(Exception):
    pass


def links_of(record):
    found = record.get("ee")
    if isinstance(found, list):
        return [str(item) for item in found]
    return [str(found)] if found else []


def dblp_match(citation, ask):
    best, answered = (0.0, "", ""), False
    for query in queries(citation):
        try:
            records = ask(query)
            answered = True
        except LookupFailed:
            continue
        for record in records:
            title = str(record.get("title") or "")
            score = match_score(title, citation)
            if score > best[0]:
                best = (score, title, url_from(links_of(record)))
    if not answered:
        raise LookupFailed("every query for this citation failed")
    return best


def crossref_match(citation, ask):
    best = (0.0, "", "")
    for item in ask(citation):
        title = " ".join(item.get("title") or [])
        score = match_score(title, citation)
        if score > best[0]:
            best = (score, title, doi_url(item.get("DOI")))
    return best


def resolved(citation, ask_dblp, ask_crossref):
    """The two indexes disagree often enough that the order matters: DBLP first because its
    threshold was calibrated lower and it answers for conference papers, Crossref second. A single
    index failing is not a failure -- only both are."""
    failures = []
    try:
        score, title, url = dblp_match(citation, ask_dblp)
        if url and score >= DBLP_MATCH_AT:
            return "dblp", score, title, url
    except LookupFailed as error:
        failures.append(str(error))
        score, title, url = 0.0, "", ""
    try:
        other, other_title, other_url = crossref_match(citation, ask_crossref)
        if other_url and other >= CROSSREF_MATCH_AT:
            return "crossref", other, other_title, other_url
    except LookupFailed as error:
        failures.append(str(error))
        other, other_title, other_url = 0.0, "", ""
    if len(failures) == 2:
        raise LookupFailed("; ".join(failures))
    if other > score:
        return "crossref", other, other_title, other_url
    return "dblp", score, title, url


def confirmed_citations(path, entities, wanted):
    found = {}
    if not path.exists():
        return found
    index = link.index_of(entities)
    for _, line in manifest.json_lines(path):
        row = json.loads(line)
        if row.get("state") != citations.CONFIRMED or not (row.get("citation") or "").strip():
            continue
        hit = link.resolve(str(row.get("name") or ""), index)
        if hit.kind != link.HIT or hit.slug not in wanted:
            continue
        if len(row["citation"]) > len(found.get(hit.slug, "")):
            found[hit.slug] = row["citation"]
    return found
