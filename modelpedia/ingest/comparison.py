from typing import NamedTuple

from modelpedia.ingest import answers
from modelpedia.ingest import citations
from modelpedia.ingest import link
from modelpedia.ingest import text as textutil
from modelpedia.ingest import verification

NAME_FIELDS = ("models", "methods", "datasets")
NUMBER_STATES = (verification.FOUND, verification.REVIEW, verification.MISSING)
CITATION_STATES = (citations.CONFIRMED, citations.PARTIAL, citations.REJECTED, citations.ABSENT)


class Side(NamedTuple):
    findings: int
    models: frozenset
    concepts: frozenset
    evidence: tuple
    numbers: dict
    quotes: dict
    unreadable: str = ""

    def numeric(self):
        return sum(self.numbers.values())

    def cited(self):
        return sum(self.quotes.values())


class Row(NamedTuple):
    paper: str
    left: Side
    right: Side

    def shared(self):
        return self.left.models & self.right.models

    def only_left(self):
        return self.left.models - self.right.models

    def only_right(self):
        return self.right.models - self.left.models


def flat_key(name):
    return textutil.flatten(str(name or ""))


def registry_keys(entities):
    """Two answers name the same model in two spellings far more often than they disagree
    about it, so the comparison counts registry slugs where the linker is certain and falls
    back to the flattened name where it is not. A certain hit only; a candidate list is not
    a decision, here as anywhere else."""
    index = link.index_of(entities)

    def key_for(name):
        found = link.resolve(str(name or ""), index)
        return found.slug if found.kind == link.HIT and found.slug else flat_key(name)
    return key_for


def named(finding, field, key_for=flat_key):
    """A model may be written as a name, as a name plus a variant, or -- against the rules, but
    it happens -- as a bare checkpoint string. All three are counted, because otherwise a side
    that writes `Gemma 2` + variant `Gemma 2 2B` reads as disagreeing with one that writes
    `Gemma 2 2b`, and that is a difference in shape, not in what the paper is about."""
    for item in finding.get(field) or []:
        if isinstance(item, str):
            written = [item]
        else:
            written = [(item or {}).get("name"), (item or {}).get("variant")]
        for name in written:
            key = key_for(name) if str(name or "").strip() else ""
            if key:
                yield key


def unreadable_side(error):
    return Side(0, frozenset(), frozenset(), (), {}, {}, str(error))


def side_of(document, doc, key_for=flat_key):
    """Everything measured about one answer to one paper. `doc` is that paper's own text:
    numbers and citations are checked against it, never against another paper's."""
    findings = answers.entries_of(document, answers.FINDINGS)
    models, concepts, evidence = set(), set(), []
    numbers = {state: 0 for state in NUMBER_STATES}
    for finding in findings:
        models.update(named(finding, "models", key_for))
        concepts.update(key for key, _ in answers.concepts_of(finding))
        evidence.append(str(finding.get("evidence_type") or ""))
        for check in verification.number_checks(finding, doc):
            if check.state in numbers:
                numbers[check.state] += 1

    quotes = {state: 0 for state in CITATION_STATES}
    for entity in answers.entries_of(document, answers.ENTITIES):
        quotes[citations.judge(entity.get("citation"), doc.pages).state] += 1

    return Side(len(findings), frozenset(models), frozenset(concepts),
                tuple(sorted(value for value in evidence if value)), numbers, quotes)


def read_side(raw, doc, key_for):
    try:
        return side_of(answers.read(raw).document, doc, key_for)
    except answers.Unreadable as error:
        return unreadable_side(error)


def rows(left, right, doc_for, key_for=flat_key):
    """Papers answered by both sides only. A paper one side never saw says nothing about
    either, and averaging it in would read as a difference between the models."""
    found = []
    for paper in sorted(set(left) & set(right)):
        doc = doc_for(paper)
        found.append(Row(paper, read_side(left[paper], doc, key_for),
                         read_side(right[paper], doc, key_for)))
    return found


def totals(found, pick):
    sides = [pick(row) for row in found]
    numbers = {state: sum(side.numbers.get(state, 0) for side in sides)
               for state in NUMBER_STATES}
    quotes = {state: sum(side.quotes.get(state, 0) for side in sides)
              for state in CITATION_STATES}
    return Side(sum(side.findings for side in sides),
                frozenset().union(*[side.models for side in sides]) if sides else frozenset(),
                frozenset().union(*[side.concepts for side in sides]) if sides else frozenset(),
                tuple(sorted(value for side in sides for value in side.evidence)),
                numbers, quotes,
                ", ".join(side.unreadable for side in sides if side.unreadable))


def share(part, whole):
    return 0.0 if not whole else round(part / whole, 3)


def agreement(found):
    """One number for the question the comparison exists to answer: of the models one side
    named, how many did the other name too. Models, because a model name is the one thing in
    a finding that a paper either contains or does not."""
    left = sum(len(row.left.models) for row in found)
    both = sum(len(row.shared()) for row in found)
    right = sum(len(row.right.models) for row in found)
    return {"left": left, "right": right, "shared": both,
            "of_left": share(both, left), "of_right": share(both, right),
            "papers_without_overlap": sum(1 for row in found
                                          if not row.shared() and (row.left.models
                                                                   or row.right.models))}
