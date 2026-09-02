import re

from modelpedia import graph as graph_json
from modelpedia import record_keys as keys
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
