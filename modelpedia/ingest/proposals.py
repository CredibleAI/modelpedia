import collections
import re
from typing import NamedTuple

from modelpedia import graph as graph_json
from modelpedia.ingest import answers
from modelpedia.ingest import citations
from modelpedia.ingest import link
from modelpedia.ingest import text as textutil

KIND_FIELDS = (("models", graph_json.MODEL), ("methods", graph_json.METHOD),
               ("datasets", graph_json.DATASET), ("related_work", None))

FAMILY_OVERLAP = 0.6
MIN_FAMILY = 2
ACRONYM = re.compile(r"\s*\(([^)]{2,12})\)\s*$")
WORD = re.compile(r"[a-z][a-z0-9-]+")
FAMILY_STOP = frozenset({"with", "without", "and", "the", "for", "of", "from", "using"})


class Proposal(NamedTuple):
    name: str
    field: str
    papers: tuple
    kind: str
    citation: str
    state: str
    identifier: str
    candidates: tuple

    def reach(self):
        return len(self.papers)


class Family(NamedTuple):
    stem: str
    members: tuple

    def reach(self):
        return len({paper for member in self.members for paper in member.papers})


def bare(name):
    return ACRONYM.sub("", str(name or "")).strip()


def family_words(name):
    return frozenset(word for word in WORD.findall(textutil.normalise(bare(name)))
                     if word not in FAMILY_STOP and len(word) > 2)


def related(left, right):
    if not left or not right:
        return False
    shared = len(left & right)
    return shared / min(len(left), len(right)) >= FAMILY_OVERLAP


def names_in(document, field):
    for finding in answers.entries_of(document, answers.FINDINGS):
        for item in finding.get(field) or []:
            name = item if isinstance(item, str) else (item or {}).get("name")
            if str(name or "").strip():
                yield str(name).strip()


def entity_notes(document):
    notes = {}
    for entity in answers.entries_of(document, answers.ENTITIES):
        key = textutil.flatten(str(entity.get("name") or ""))
        if key:
            notes[key] = entity
    return notes


def gather(documents, entities, verdicts=None):
    indexes = {field: link.index_of(entities, node_type) for field, node_type in KIND_FIELDS}
    seen = {}
    for paper, document in sorted(documents.items()):
        notes = entity_notes(document)
        for field, _ in KIND_FIELDS:
            for name in names_in(document, field):
                if link.resolve(name, indexes[field]).kind == link.HIT:
                    continue
                key = (field, textutil.flatten(name))
                note = notes.get(textutil.flatten(name)) or {}
                verdict = (verdicts or {}).get((paper, textutil.flatten(name)))
                record = seen.setdefault(key, {"name": name, "field": field, "papers": [],
                                               "kind": note.get("kind") or field,
                                               "citation": "", "state": citations.ABSENT,
                                               "identifier": "", "candidates": ()})
                if paper not in record["papers"]:
                    record["papers"].append(paper)
                if not record["citation"] and note.get("citation"):
                    record["citation"] = " ".join(str(note["citation"]).split())
                    record["state"] = verdict or citations.ABSENT
                    record["identifier"] = citations.identifier_in(note["citation"])
                if not record["candidates"]:
                    found = link.resolve(name, indexes[field])
                    record["candidates"] = tuple(found.candidates or ())
    return sorted((Proposal(record["name"], record["field"], tuple(record["papers"]),
                            record["kind"], record["citation"], record["state"],
                            record["identifier"], record["candidates"])
                   for record in seen.values()),
                  key=lambda item: (-item.reach(), item.field, item.name.lower()))


def families(proposals):
    by_field = collections.defaultdict(list)
    for proposal in proposals:
        by_field[proposal.field].append(proposal)
    found = []
    for field, members in sorted(by_field.items()):
        pending = list(members)
        while pending:
            head = pending.pop(0)
            words = family_words(head.name)
            kin = [other for other in pending if related(words, family_words(other.name))]
            if len(kin) + 1 < MIN_FAMILY:
                continue
            for member in kin:
                pending.remove(member)
            shared = words.intersection(*[family_words(member.name) for member in kin])
            found.append(Family(" ".join(sorted(shared)) or head.name, tuple([head] + kin)))
    return sorted(found, key=lambda item: -item.reach())


class Refusal(NamedTuple):
    paper: str
    finding: str
    closest: str
    why: str


class Silence(NamedTuple):
    paper: str
    finding: str


class ConceptAnswers(NamedTuple):
    proposals: tuple
    refusals: tuple
    silent: tuple
    without_concept: int
    stray: int

    def answered(self):
        return self.without_concept - len(self.silent)


class Chosen(NamedTuple):
    paper: str
    finding: str
    value: str
    written: str


def chosen_concepts(finding):
    for item in finding.get("concepts") or []:
        written = item if isinstance(item, str) else None
        if written is None and isinstance(item, dict):
            written = item.get("concept") or item.get("id") or item.get("name")
        value = str(written or "").strip()
        if not value:
            continue
        yield value if value.startswith("concept:") else "concept:%s" % value, item


def untagged(document):
    return [" ".join(str(finding.get("title") or "").split())
            for finding in answers.entries_of(document, answers.FINDINGS)
            if not finding.get("concepts")]


def off_list(documents, known):
    unknown, misshapen = [], []
    for paper, document in sorted(documents.items()):
        for finding in answers.entries_of(document, answers.FINDINGS):
            title = " ".join(str(finding.get("title") or "").split())
            for value, written in chosen_concepts(finding):
                if value not in known:
                    unknown.append(Chosen(paper, title, value, repr(written)))
                elif not isinstance(written, str) or not written.startswith("concept:"):
                    misshapen.append(Chosen(paper, title, value, repr(written)))
    return tuple(unknown), tuple(misshapen)


def about(entry, title):
    said, asked = textutil.flatten(str(entry.get("finding") or "")), textutil.flatten(title)
    return bool(said) and bool(asked) and (said in asked or asked in said)


def concept_answers(documents):
    proposed, refusals, silent, without_concept, stray = {}, [], [], 0, 0
    for paper, document in sorted(documents.items()):
        entries = answers.entries_of(document, answers.CONCEPTS_CONSIDERED)
        answering = set()
        for title in untagged(document):
            without_concept += 1
            found = [entry for entry in entries if about(entry, title)]
            if not found:
                silent.append(Silence(paper, title))
            answering.update(id(entry) for entry in found)
        for entry in entries:
            name = str(entry.get("name") or "").strip()
            closest = str(entry.get("closest") or "").strip()
            if not name:
                if id(entry) not in answering:
                    stray += 1
                    continue
                refusals.append(Refusal(paper, " ".join(str(entry.get("finding") or "").split()),
                                        closest, " ".join(str(entry.get("why") or "").split())))
                continue
            record = proposed.setdefault(textutil.flatten(name),
                                         {"name": name, "papers": [], "definitions": [],
                                          "instead_of": []})
            record["papers"].append(paper)
            for field, bucket in (("definition", "definitions"), ("instead_of", "instead_of")):
                if entry.get(field):
                    record[bucket].append(" ".join(str(entry[field]).split()))
    return ConceptAnswers(tuple(sorted(proposed.values(),
                                       key=lambda record: -len(record["papers"]))),
                          tuple(refusals), tuple(silent), without_concept, stray)
