from typing import NamedTuple

import yaml

from modelpedia import graph as graph_json
from modelpedia.ingest import citations
from modelpedia.ingest import verification
from modelpedia.ingest import link
from modelpedia.ingest import text as textutil

KINDS = {"models": graph_json.MODEL, "datasets": graph_json.DATASET,
         "methods": graph_json.METHOD}
FAMILY_FIELD = "models"
MAX_FAMILIES = 400

DECISION = "decision"
ADOPT = "adopt"
REFUSE = "refuse"
DECISIONS = (ADOPT, REFUSE)
TRUSTED_CITATION = ("confirmed", "partial")

TASK = """You decide whether one name earns a permanent entry in a shared registry.

The registry is the join surface of a database of findings about machine learning models. An
entry is how two papers come to talk about the same thing, so a wrong entry does not merely add
noise: it either splits one thing into two records or merges two things into one.

You are given the name as the extractor wrote it, the papers it appeared in, and the sentence
those papers cite for it. You are also given the entries the registry already holds that came
closest to this name, and, for a model, the closed list of families already present.

Answer these questions and nothing else.

1. DOES IT EARN AN ENTRY? Two tests, both must pass.
   citable  it has a paper, a repository or a release behind it, and a reader could look it up
            without the papers listed below.
   reusable a different paper, about a different model, could refer to the same thing by the
            same name.

   These fail and must be refused:
   - an optimiser or a training setting: Adam, early stopping, a batch size, a learning rate
   - an implementation detail or a step someone named for their own pipeline: "our training
     recipe", "automatic evaluation", "attention heatmap analysis", "GPT-4o as judge"
   - one parameterisation of an underlying method, where the underlying method is the entry
   - a model, a dataset or a method that is already in the closest-entries list under another
     spelling. Say so and give that entry's identifier: the answer is an alias, not a new entry.
   - for a method only: something that describes what a model is rather than how the evidence
     about it was obtained. A method characterises the evidence.

2. IS IT A FAMILY OR A CHECKPOINT? Models only.
   A checkpoint belongs under its family, never beside it: `Llama-3.1-405B` is a checkpoint of
   the `Llama 3.1` family, and the registry holds it under that family, not as its own model.
   Choose the family from the closed list, or answer `new` when no family in the list is the same
   model line. Never write an identifier that is not in the list.

3. WHAT IS ITS ANCHOR? A URL a reader can follow: arXiv, a DOI, OpenReview, a project page, a
   Wikipedia article.

   THE ANCHOR MUST COME FROM THE CITED SENTENCE BELOW, NOT FROM YOUR MEMORY. This is the rule
   that this step exists to enforce. An earlier run wrote anchors from general knowledge and two
   of seven pointed at the wrong work while looking perfectly well-formed. If the sentence carries
   an arXiv number, a DOI, an OpenReview id or a full URL, use it. If it does not, answer with an
   empty string. An empty anchor is a correct answer and costs nothing; a plausible wrong one
   costs a reader their trust in every other entry.
"""

RULES = """Return YAML only. No prose before or after, no code fences.

decision: adopt        or refuse
why: "one sentence"    always, for both decisions
title: "Canonical Name"    adopt only; the spelling a reader should see, not the one the
                           extractor happened to write
family: model:slug     adopt only, models only; an identifier from the closed list, or `new`
anchor: "https://..."  adopt only; copied from the cited sentence, or "" when it carries none
alias_of: entity:slug  refuse only, and only when the reason is that the registry already holds
                       this thing under another spelling

Worked answers.

decision: adopt
why: "Named benchmark with its own paper, used by four unrelated groups here."
title: "HellaSwag"
anchor: "https://arxiv.org/abs/1905.07830"

decision: adopt
why: "Released checkpoint of a family the registry already carries."
title: "Llama-3.1-405B"
family: model:llama-3-1
anchor: "https://arxiv.org/abs/2407.21783"

decision: refuse
why: "The registry already holds this under a different spelling."
alias_of: dataset:big-bench-hard

decision: refuse
why: "A model used as a scoring tool is still a model, and this is the methods registry."
"""


def families_of(entities):
    found = []
    for key, entity in sorted(entities.items()):
        if entity.get("type") == graph_json.MODEL:
            found.append((key, link.display_name(key, entity)))
    return found[:MAX_FAMILIES]


def closest(candidates, entities):
    lines = []
    for key in candidates or ():
        entry = entities.get(key) or {}
        lines.append("  %-34s %s" % (key, link.display_name(key, entry)))
    return "\n".join(lines) or "  none"


def family_block(families):
    return "\n".join("  %-30s %s" % (key, name) for key, name in families)


def squeezed(value, limit=600):
    return " ".join(str(value or "").split())[:limit]


def build(proposal, entities, families):
    parts = [
        TASK,
        "\n" + RULES,
        "\nThe name, as the extractor wrote it: %s" % proposal["name"],
        "It was written in the `%s` field of %d paper(s): %s"
        % (proposal["field"], len(proposal["papers"]), ", ".join(proposal["papers"][:12])),
        "\nThe sentence those papers cite for it, copied out of the paper:",
        "  %s" % (squeezed(proposal.get("citation")) or "(no citation was copied)"),
        "  citation check against the paper: %s" % (proposal.get("state") or "absent"),
        "\nClosest entries the registry already holds:",
        closest(proposal.get("candidates"), entities),
    ]
    if proposal["field"] == FAMILY_FIELD:
        parts += ["\nClosed list of model families already in the registry. `family` must be one",
                  "of these identifiers, or the word `new`.\n", family_block(families)]
    parts += ["\nReturn the YAML now, and nothing else."]
    return "\n".join(parts)


class Unreadable(Exception):
    pass


def read(raw):
    try:
        document = yaml.safe_load(raw)
    except yaml.YAMLError as error:
        raise Unreadable(str(error).split("\n")[0])
    if not isinstance(document, dict):
        raise Unreadable("answer is not a mapping")
    decision = str(document.get(DECISION) or "").strip().lower()
    if decision not in DECISIONS:
        raise Unreadable("decision is %r, not one of %s" % (decision, "/".join(DECISIONS)))
    return document


def anchor_is_in(anchor, pages):
    if not str(anchor or "").strip():
        return False
    found = verification.anchor_identifier(anchor)
    needle = found[1] if found else str(anchor).split("://", 1)[-1].rstrip("/")
    packed = textutil.flatten(needle)
    return bool(packed) and any(packed in textutil.flatten(page) for page in pages)


class Verdict(NamedTuple):
    name: str
    field: str
    decision: str
    title: str
    family: str
    anchor: str
    alias_of: str
    why: str
    problem: str

    def adopted(self):
        return self.decision == ADOPT and not self.problem


def slug_for(title, name):
    return link.valid_slug(title) or link.valid_slug(name)


def judge(row, document, pages, families):
    decision = str(document.get(DECISION) or "").strip().lower()
    title = str(document.get("title") or row["name"]).strip()
    family = str(document.get("family") or "").strip()
    anchor = str(document.get("anchor") or "").strip()
    alias_of = str(document.get("alias_of") or "").strip()
    why = squeezed(document.get("why"), 200)
    problem = ""

    if decision == REFUSE:
        if alias_of and alias_of not in dict(families) and not alias_of.startswith(
                ("dataset:", "method:", "model:", "variant:")):
            problem = "alias_of %r is not an identifier" % alias_of
        return Verdict(row["name"], row["field"], REFUSE, "", "", "", alias_of, why, problem)

    if row["field"] == FAMILY_FIELD:
        known = dict(families)
        if family in ("new", "model:new"):
            family = "new"
        if family and family != "new" and family not in known:
            problem = "family %r is not in the closed list" % family
    if anchor and not anchor_is_in(anchor, pages):
        anchor = ""
        problem = problem or "anchor was not in any citing paper, dropped"
    if not anchor and str(row.get("state") or "") in TRUSTED_CITATION:
        anchor = citations.anchor_from(row.get("citation") or "")
    return Verdict(row["name"], row["field"], ADOPT, title, family, anchor, "", why, problem)


def already_held(verdict, indexes, variants, parents):
    index = indexes.get(verdict.field)
    if index is None:
        return ""
    for probe in (verdict.title, verdict.name):
        if not probe:
            continue
        if verdict.field == FAMILY_FIELD:
            found = link.resolve_model(probe, index, variants, parents)[0]
        else:
            found = link.resolve(probe, index)
        if found.kind == link.HIT:
            return found.slug
    return ""


def entry_for(verdict):
    entry = {"name": verdict.title}
    if verdict.anchor:
        entry["anchor"] = verdict.anchor
    return entry
