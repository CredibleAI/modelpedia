import difflib
import re
from typing import NamedTuple

from modelpedia import graph as graph_json
from modelpedia.ingest.text import fold

VARIANT = graph_json.VARIANT

HIT = "hit"
CANDIDATES = "candidates"
MISS = "miss"

BY_KEY = "key"
BY_NAME = "name"
BY_SLUG = "slug"
BY_COMPACT = "compact"

THRESHOLD = 0.6
MIN_BLOCK = 4
MAX_CANDIDATES = 5

PUNCTUATION = re.compile(r"[^a-z0-9]+")
ALIAS = re.compile(r"\s+/\s+")
QUALIFIER = re.compile(r"\s*\([^)]*\)\s*$")


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
    by_compact: dict
    by_gram: dict
    short: frozenset


def normalise(value):
    return PUNCTUATION.sub(" ", fold(value)).strip()


def slugify(value):
    return PUNCTUATION.sub("-", fold(value)).strip("-")


def valid_slug(text):
    slug = slugify(text)
    if not slug or not slug[0].isdigit():
        return slug
    parts = slug.split("-")
    if len(parts) > 1:
        return "-".join(parts[1:] + parts[:1])
    digits = slug[:len(slug) - len(slug.lstrip("0123456789"))]
    return "%s-%s" % (slug[len(digits):], digits) if slug[len(digits):] else slug


def compact(value):
    return PUNCTUATION.sub("", fold(value))


def display_name(key, entity):
    return entity.get("name") or entity.get("title") or key.partition(":")[2]


def aliases(name):
    whole = str(name).strip()
    parts = [part.strip() for part in ALIAS.split(whole)]
    return [part for part in dict.fromkeys([whole] + parts) if part]


def add(table, name, key):
    keys = table.setdefault(name, [])
    if key not in keys:
        keys.append(key)


def index_of(entities, node_type=None):
    wanted = ({node_type} if isinstance(node_type, str)
              else frozenset(node_type) if node_type is not None else None)
    identifiers, by_name, by_slug, by_compact = [], {}, {}, {}
    for key, entity in sorted(entities.items()):
        if wanted is not None and entity.get("type") not in wanted:
            continue
        identifiers.append(key)
        slug = key.partition(":")[2]
        for alias in aliases(display_name(key, entity)):
            for form in dict.fromkeys([alias, QUALIFIER.sub("", alias).strip()]):
                if not form:
                    continue
                add(by_name, normalise(form), key)
                add(by_compact, compact(form), key)
        add(by_slug, slug, key)
        add(by_compact, compact(slug), key)

    by_gram, short = {}, set()
    for name in by_name:
        if len(name) < MIN_BLOCK:
            short.add(name)
            continue
        for gram in grams(name):
            by_gram.setdefault(gram, set()).add(name)
    return Index(node_type=node_type, identifiers=frozenset(identifiers),
                 by_name=by_name, by_slug=by_slug,
                 by_compact={form: list(dict.fromkeys(keys))
                             for form, keys in by_compact.items()},
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


def parents_of(entities):
    return {key: entity.get("parent") for key, entity in entities.items()
            if entity.get("type") == VARIANT and entity.get("parent")}


def through_variant(found, parents):
    if found.kind == HIT:
        return hit(found.query, parents.get(found.slug, ""), "%s via variant" % found.how), \
            found.slug
    seen = [parents[key] for key in found.candidates if key in parents]
    return miss(found.query, tuple(dict.fromkeys(seen))), ""


def resolve_model(query, models, variants, parents, threshold=THRESHOLD):
    direct = resolve(query, models, threshold)
    if direct.kind == HIT:
        return direct, ""
    named, variant = through_variant(resolve(query, variants, threshold), parents)
    if named.kind == HIT and named.slug:
        return named, variant
    if direct.kind == CANDIDATES or named.candidates:
        pool = dict.fromkeys(tuple(direct.candidates) + tuple(named.candidates))
        return ambiguous(str(query).strip(), pool, direct.how), ""
    return direct, ""


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

    by_compact = index.by_compact.get(compact(text))
    if by_compact:
        return settle(text, by_compact, BY_COMPACT)

    trimmed = QUALIFIER.sub("", text).strip()
    if trimmed and trimmed != text:
        without = resolve(trimmed, index, threshold)
        if without.kind == HIT:
            return hit(text, without.slug, "%s without qualifier" % without.how)

    close = nearby(text, index, threshold)
    if close:
        return ambiguous(text, close, None)
    return miss(text)
