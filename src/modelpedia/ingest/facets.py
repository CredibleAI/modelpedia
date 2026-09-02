import yaml

from modelpedia import graph as graph_json
from modelpedia import schema

FIELDS = ("modality", "task", "domain")

TASK = """You describe one model along three closed axes, so that a reader can tell at a glance
what kind of model it is and so that models of one kind can be listed together.

You are given the model's name, the entry the registry holds for it, and the sentence a paper
cites for it. Answer only from what those tell you, plus what the name itself makes certain.

  modality  what the model takes in or puts out. One or more.
  task      what it is for. One or more.
  domain    the field it was built for, and ONLY when the model is specific to that field.
            A general-purpose language model has no domain. Leave the list empty; an empty
            answer is the common one and is correct.

Two rules that matter more than coverage.

CHOOSE ONLY FROM THE LISTS BELOW. An axis is closed on purpose: a value nobody else uses groups
nothing, and a value invented here will fail validation and be dropped anyway.

WHEN YOU DO NOT KNOW, LEAVE THE LIST EMPTY. A wrong modality is worse than a missing one, because
a reader filtering on it gets a model that does not belong and never learns why. If the name and
the citation do not settle it, answer with an empty list and nothing else.
"""

RULES = """Return YAML only. No prose before or after, no code fences.

modality:
- text
- image
task:
- discriminative
domain: []

Every value copied exactly from the closed lists. Empty lists are allowed on every axis.
"""


class Unreadable(Exception):
    pass


def vocabulary(database):
    terms = database.vocabularies.get(graph_json.MODEL) or {}
    return {field: [str(value) for value in (terms.get(field) or [])] for field in FIELDS}


def block(allowed):
    lines = []
    for field in FIELDS:
        lines.append("  %-9s %s" % (field, ", ".join(allowed[field]) or "(none)"))
    return "\n".join(lines)


def wanted(entities):
    found = {}
    for key, entity in sorted(entities.items()):
        if entity.get("type") != graph_json.MODEL:
            continue
        if any(entity.get(field) for field in FIELDS):
            continue
        found[key] = entity
    return found


def squeezed(value, limit=400):
    return " ".join(str(value or "").split())[:limit]


def build(key, entity, allowed, citation=""):
    variants = list((entity.get("variants") or {}).values())
    parts = [
        TASK,
        "\n" + RULES,
        "\nClosed lists. Every value must be one of these.\n",
        block(allowed),
        "\n\nThe model: %s" % (entity.get("name") or key),
        "identifier: %s" % key,
    ]
    if entity.get("anchor"):
        parts.append("anchor: %s" % entity["anchor"])
    if variants:
        parts.append("checkpoints the registry holds under it: %s"
                     % ", ".join(str(v.get("name") or "") for v in variants[:8]))
    if citation:
        parts.append("\nThe sentence a paper cites for it:\n  %s" % squeezed(citation))
    parts.append("\nReturn the YAML now, and nothing else.")
    return "\n".join(parts)


def read(raw):
    document = yaml.safe_load(raw)
    if not isinstance(document, dict):
        raise Unreadable("answer is not a mapping")
    if not any(field in document for field in FIELDS):
        raise Unreadable("no axis among %s" % ", ".join(FIELDS))
    return document


def chosen(document, allowed):
    taken, refused = {}, []
    for field in FIELDS:
        written = document.get(field)
        if isinstance(written, str):
            written = [written]
        values = []
        for value in written or []:
            text = str(value).strip()
            if text in allowed[field]:
                if text not in values:
                    values.append(text)
            elif text:
                refused.append((field, text))
        if values:
            taken[field] = values
    return taken, refused


def valid(taken, database):
    terms = database.vocabularies.get(graph_json.MODEL) or {}
    for field, values in taken.items():
        for value in values:
            if value not in (terms.get(field) or []):
                return False
    return bool(schema.MODEL_FACETS)
