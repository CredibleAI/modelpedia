import difflib
import re
from typing import NamedTuple

from modelpedia.ingest.text import fold

HIT = "hit"
CANDIDATES = "candidates"
MISS = "miss"

BY_KEY = "key"
BY_NAME = "name"
BY_SLUG = "slug"

THRESHOLD = 0.6
MIN_BLOCK = 4
MAX_CANDIDATES = 5

PUNCTUATION = re.compile(r"[^a-z0-9]+")
ALIAS = re.compile(r"\s+/\s+")


class Resolution(NamedTuple):
    query: str
    kind: str
    slug: str | None
    how: str | None
    candidates: tuple


class Index(NamedTuple):
    node_type: str | None
    identifiers: frozenset
    by_name: dict
    by_slug: dict
    by_gram: dict
    short: frozenset


def normalise(value):
    return PUNCTUATION.sub(" ", fold(value)).strip()


def slugify(value):
    return PUNCTUATION.sub("-", fold(value)).strip("-")


def display_name(key, entity):
    return entity.get("name") or entity.get("title") or key.partition(":")[2]


def aliases(name):
    whole = str(name).strip()
    parts = [part.strip() for part in ALIAS.split(whole)]
    return [part for part in dict.fromkeys([whole] + parts) if part]


def index_of(entities, node_type=None):
    identifiers, by_name, by_slug = [], {}, {}
    for key, entity in sorted(entities.items()):
        if node_type is not None and entity.get("type") != node_type:
            continue
        identifiers.append(key)
        for alias in aliases(display_name(key, entity)):
            by_name.setdefault(normalise(alias), []).append(key)
        by_slug.setdefault(key.partition(":")[2], []).append(key)

    by_gram, short = {}, set()
    for name in by_name:
        if len(name) < MIN_BLOCK:
            short.add(name)
            continue
        for gram in grams(name):
            by_gram.setdefault(gram, set()).add(name)
    return Index(node_type=node_type, identifiers=frozenset(identifiers),
                 by_name=by_name, by_slug=by_slug,
                 by_gram={gram: frozenset(names) for gram, names in by_gram.items()},
                 short=frozenset(short))


def hit(query, slug, how):
    return Resolution(query=query, kind=HIT, slug=slug, how=how, candidates=())


def ambiguous(query, keys, how):
    return Resolution(query=query, kind=CANDIDATES, slug=None, how=how,
                      candidates=tuple(sorted(keys)))


def miss(query, candidates=()):
    return Resolution(query=query, kind=MISS, slug=None, how=None, candidates=tuple(candidates))


def settle(query, keys, how):
    if len(keys) == 1:
        return hit(query, keys[0], how)
    return ambiguous(query, keys, how)


def containment(target, name):
    if not target or not name:
        return 0.0
    short, long = sorted((target, name), key=len)
    return len(short) / len(long) if short in long else 0.0


def similarity(target, name):
    matcher = difflib.SequenceMatcher(None, target, name)
    longest = matcher.find_longest_match(0, len(target), 0, len(name)).size
    if longest < min(MIN_BLOCK, len(target), len(name)):
        return 0.0
    return max(matcher.ratio(), containment(target, name))


def grams(value):
    return {value[at:at + MIN_BLOCK] for at in range(len(value) - MIN_BLOCK + 1)}


def comparable(target, index):
    if len(target) < MIN_BLOCK:
        return list(index.by_name)
    names = set(index.short)
    for gram in grams(target):
        names |= index.by_gram.get(gram, frozenset())
    return names


def nearby(query, index, threshold):
    best = {}
    target = normalise(query)
    for name in comparable(target, index):
        score = similarity(target, name)
        if score < threshold:
            continue
        for key in index.by_name[name]:
            best[key] = max(best.get(key, 0.0), score)
    ranked = sorted(best.items(), key=lambda pair: (-pair[1], pair[0]))
    return [key for key, _ in ranked[:MAX_CANDIDATES]]


def resolve(query, index, threshold=THRESHOLD):
    if not query or not str(query).strip():
        return miss(query)

    text = str(query).strip()
    if text in index.identifiers:
        return hit(text, text, BY_KEY)

    by_name = index.by_name.get(normalise(text))
    if by_name:
        return settle(text, by_name, BY_NAME)

    by_slug = index.by_slug.get(slugify(text))
    if by_slug:
        return settle(text, by_slug, BY_SLUG)

    close = nearby(text, index, threshold)
    if close:
        return ambiguous(text, close, None)
    return miss(text)
