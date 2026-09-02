import datetime
import re
from typing import NamedTuple

from modelpedia import graph as graph_json
from modelpedia import models
from modelpedia import record_keys as keys
from modelpedia import schema
from modelpedia.ingest import answers
from modelpedia.ingest import citations
from modelpedia.ingest import link
from modelpedia.ingest import openreview
from modelpedia.ingest import text as textutil

SLUG_WORDS = 6
STOP = frozenset({"a", "an", "the", "of", "for", "and", "or", "in", "on", "to", "with",
                  "is", "are", "can", "do", "does", "how", "what", "why", "towards"})
WORD = re.compile(r"[a-z0-9]+")

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
    return link.valid_slug("-".join(words[:SLUG_WORDS])) or "untitled"


def date_from(stamp):
    if not isinstance(stamp, (int, float)) or stamp <= 0:
        return None
    moment = datetime.datetime.fromtimestamp(stamp / 1000, datetime.timezone.utc)
    return moment.date().isoformat()


def source_entry(meta):
    content = openreview.flat_content(meta.get("content"))
    title = str(content.get("title") or "").strip()
    return slug_from(title), {
        keys.AUTHORS: list(content.get("authors") or []),
        "title": title,
        "venue": str(content.get("venue") or "").strip() or None,
        "date": date_from(meta.get("pdate")),
        keys.ANCHOR: "https://openreview.net/forum?id=%s" % meta.get("id"),
        keys.ARTIFACT: None,
        keys.NOTE: None,
    }


def anchors_in(document, packed=""):
    found = {}
    for entity in answers.entries_of(document, answers.ENTITIES):
        name = textutil.flatten(str(entity.get("name") or ""))
        if name:
            found[name] = citations.anchor_from(str(entity.get("citation") or ""), packed)
    return found


def concept_refs(finding, known):
    kept = []
    for value, _ in answers.concepts_of(finding):
        entry = {keys.REF: value}
        if value in known and entry not in kept:
            kept.append(entry)
    return kept


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


def related_work_of(finding, anchors, allowed, everything, paper, title, dropped):
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
        if not anchor:
            dropped.append(Dropped(paper, title, "related_work", name,
                                   "no node and no anchor, the reader could not follow it"))
            continue
        entry = {keys.NAME: name, keys.ANCHOR: anchor}
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


def squeezed(value):
    return " ".join(str(value or "").split())


def record_for(finding, source_ref, links, concepts, related):
    return models.Finding(
        id=None,
        title=squeezed(finding.get("title")),
        description=squeezed(finding.get("description")),
        models=tuple(models.link_from(item) for item in links["models"]),
        concepts=tuple(models.link_from(item) for item in concepts),
        sources=(models.Ref(ref=source_ref),),
        datasets=tuple(models.link_from(item) for item in links["datasets"]),
        methods=tuple(models.link_from(item) for item in links["methods"]),
        related_work=tuple(models.link_from(item) for item in related),
        evidence_type=str(finding.get("evidence_type") or "").strip() or None,
        key_metric=squeezed(finding.get("key_metric")),
        caveat=squeezed(finding.get("caveat")),
        extracted_by=AUTOMATIC,
    ).to_dict()


def split(documents, entities, sources, prefix, known_concepts, roles=None, texts=None,
          start=0):
    roles = roles or {}
    texts = texts or {}
    indexes = {field: link.index_of(entities, node_type) for field, node_type in FIELDS}
    everything = link.index_of(entities, graph_json.REGISTRY_TYPES)
    variants = link.index_of(entities, graph_json.VARIANT)
    parents = link.parents_of(entities)

    kept, dropped, refused = [], [], []
    number = start
    for paper, document in sorted(documents.items()):
        anchors = anchors_in(document, textutil.squeezed(texts.get(paper, "")))
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
            related = related_work_of(finding, anchors, roles.get("related_work", ()),
                                      everything, paper, title, dropped)
            number += 1
            kept.append(Candidate("%s-%03d" % (prefix, number), paper,
                                  record_for(finding, source_ref, links,
                                             concept_refs(finding, known_concepts),
                                             related)))
    return cross_linked(kept), dropped, refused


def cross_linked(kept):
    by_paper = {}
    for candidate in kept:
        by_paper.setdefault(candidate.paper, []).append(candidate.identifier)
    linked = []
    for candidate in kept:
        siblings = [other for other in by_paper[candidate.paper]
                    if other != candidate.identifier]
        if not siblings:
            linked.append(candidate)
            continue
        record = dict(candidate.record)
        record[schema.RELATED_FINDINGS_FIELD] = siblings
        linked.append(candidate._replace(record=record))
    return linked
