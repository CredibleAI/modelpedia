import datetime
import re
from typing import NamedTuple

from modelpedia import graph as graph_json
from modelpedia import record_keys as keys
from modelpedia import schema
from modelpedia.ingest import answers
from modelpedia.ingest import citations
from modelpedia.ingest import link
from modelpedia.ingest import text as textutil

SLUG_WORDS = 6
STOP = frozenset({"a", "an", "the", "of", "for", "and", "or", "in", "on", "to", "with",
                  "is", "are", "can", "do", "does", "how", "what", "why", "towards"})
WORD = re.compile(r"[a-z0-9]+")
BARE_CITATION = re.compile(r"et al\.|&\s|\(\s*\d{4}[a-z]?\s*\)")

DRAFT = "draft"
AUTOMATIC = "automatic-extraction"

FIELDS = (("models", graph_json.MODEL), ("datasets", graph_json.DATASET),
          ("methods", graph_json.METHOD))


class Dropped(NamedTuple):
    paper: str
    finding: str
    field: str
    name: str
    why: str


class Candidate(NamedTuple):
    identifier: str
    paper: str
    record: dict


def slug_from(title):
    words = [word for word in WORD.findall(textutil.fold(title)) if word not in STOP]
    return "-".join(words[:SLUG_WORDS]) or "untitled"


def date_from(stamp):
    if not isinstance(stamp, (int, float)) or stamp <= 0:
        return None
    moment = datetime.datetime.fromtimestamp(stamp / 1000, datetime.timezone.utc)
    return moment.date().isoformat()


def value_of(content, key):
    found = (content or {}).get(key)
    return found.get("value") if isinstance(found, dict) else found


def source_entry(meta):
    content = meta.get("content") or {}
    title = str(value_of(content, "title") or "").strip()
    return slug_from(title), {
        keys.AUTHORS: list(value_of(content, "authors") or []),
        "title": title,
        "venue": str(value_of(content, "venue") or "").strip() or None,
        "date": date_from(meta.get("pdate")),
        keys.ANCHOR: "https://openreview.net/forum?id=%s" % meta.get("id"),
        keys.ARTIFACT: None,
        keys.NOTE: None,
    }


def anchors_in(document):
    found = {}
    for entity in answers.entries_of(document, answers.ENTITIES):
        name = textutil.flatten(str(entity.get("name") or ""))
        if name:
            found[name] = citations.anchor_from(str(entity.get("citation") or ""))
    return found


def concept_refs(finding, known):
    kept = []
    for value, _ in _chosen(finding):
        entry = {keys.REF: value}
        if value in known and entry not in kept:
            kept.append(entry)
    return kept


def _chosen(finding):
    for item in finding.get("concepts") or []:
        written = item if isinstance(item, str) else (item or {}).get("concept")
        value = str(written or "").strip()
        if value:
            yield (value if value.startswith("concept:") else "concept:%s" % value), item


def named(item):
    if isinstance(item, str):
        return item.strip(), None
    item = item or {}
    return str(item.get("name") or "").strip(), item.get(keys.ROLE)


def resolve_field(field, finding, indexes, variants, parents, roles, paper, title, dropped):
    kept = []
    for item in finding.get(field) or []:
        name, role = named(item)
        if not name:
            continue
        if field == "models":
            found, variant = link.resolve_model(name, indexes[field], variants, parents)
        else:
            found, variant = link.resolve(name, indexes[field]), ""
        if found.kind != link.HIT:
            dropped.append(Dropped(paper, title, field, name,
                                   "candidates: %s" % ", ".join(found.candidates)
                                   if found.candidates else "no registry entry"))
            continue
        entry = {keys.REF: found.slug}
        if variant:
            entry[keys.VARIANT] = variant
        if role and role in roles.get(field, ()):
            entry[keys.ROLE] = role
        if entry not in kept:
            kept.append(entry)
    return without_bare_duplicates(kept)


def without_bare_duplicates(links):
    named = {link[keys.REF] for link in links if link.get(keys.VARIANT)}
    return [link for link in links
            if link.get(keys.VARIANT) or link[keys.REF] not in named]


def related_work_of(finding, anchors, allowed, everything):
    kept = []
    for item in finding.get("related_work") or []:
        name, role = named(item)
        if not name:
            continue
        known = link.resolve(name, everything)
        if known.kind == link.HIT:
            entry = {keys.REF: known.slug}
            if role and role in allowed:
                entry[keys.ROLE] = role
            if entry not in kept:
                kept.append(entry)
            continue
        anchor = anchors.get(textutil.flatten(name))
        if not anchor and BARE_CITATION.search(name):
            continue
        entry = {keys.NAME: name}
        if anchor:
            entry[keys.ANCHOR] = anchor
        if role and role in allowed:
            entry[keys.ROLE] = role
        kept.append(entry)
    return kept


def named_in_title(title, dropped, paper):
    packed = textutil.flatten(title)
    for item in dropped:
        if item.paper != paper or item.field != "models" or item.finding != title:
            continue
        name = textutil.flatten(item.name)
        if name and name in packed:
            return item.name
    return ""


def record_for(finding, source_ref, links, concepts, related):
    record = {
        "title": " ".join(str(finding.get("title") or "").split()),
        "description": " ".join(str(finding.get("description") or "").split()),
        "models": links["models"],
        "concepts": concepts,
        "sources": [{keys.REF: source_ref}],
        "datasets": links["datasets"],
        "methods": links["methods"],
        "related_work": related,
        "evidence_type": str(finding.get("evidence_type") or "").strip() or None,
        "key_metric": " ".join(str(finding.get("key_metric") or "").split()),
        "caveat": " ".join(str(finding.get("caveat") or "").split()),
        "review_status": DRAFT,
        "extracted_by": AUTOMATIC,
    }
    return {key: value for key, value in record.items() if value not in ("", None) or key == "concepts"}


def split(documents, entities, sources, prefix, known_concepts, roles=None):
    roles = roles or {}
    indexes = {field: link.index_of(entities, node_type) for field, node_type in FIELDS}
    everything = link.index_of(entities)
    variants = link.index_of(entities, graph_json.VARIANT)
    parents = link.parents_of(entities)

    kept, dropped, refused = [], [], []
    number = 0
    for paper, document in sorted(documents.items()):
        anchors = anchors_in(document)
        source_ref = sources.get(paper)
        for finding in answers.entries_of(document, answers.FINDINGS):
            title = " ".join(str(finding.get("title") or "").split())
            links = {field: resolve_field(field, finding, indexes, variants, parents,
                                          roles, paper, title, dropped)
                     for field, _ in FIELDS}
            if not links["models"]:
                refused.append(Dropped(paper, title, "models", "",
                                       "no model resolved to a registry entry"))
                continue
            claimed = named_in_title(title, dropped, paper)
            if claimed:
                refused.append(Dropped(paper, title, "models", claimed,
                                       "the title names a model that resolved to nothing"))
                continue
            if not source_ref:
                refused.append(Dropped(paper, title, "sources", "", "no source entry for the paper"))
                continue
            number += 1
            kept.append(Candidate("%s-%03d" % (prefix, number), paper,
                                  record_for(finding, source_ref, links,
                                             concept_refs(finding, known_concepts),
                                             related_work_of(finding, anchors,
                                                             roles.get("related_work", ()),
                                                             everything))))
    return kept, dropped, refused
