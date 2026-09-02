import yaml

from modelpedia import graph as graph_json
from modelpedia import record_keys as keys
from modelpedia.ingest import prompt as promptlib

TASK = """You tag one finding from Modelpedia with concepts from a closed list.

A concept names the MECHANISM a finding is about. It is the axis on which findings about
different models meet, so a wrong tag does not just misdescribe one record: it invents a
relationship between records. That is the most damaging error you can make here.

You are given a finding and the closed list of concepts with their definitions. You see nothing
else, and you do not need anything else: everything that decides the answer is in the finding's
own title and description.

HOW TO DECIDE. For each concept you are considering, find the phrase of its DEFINITION that the
finding satisfies, and quote it. If you cannot quote such a phrase, the concept does not apply,
however close the two names sound. Deciding by the concept's name instead of its definition is
the single most common way this task goes wrong.

Being broad is not the same as being elastic. A concept is meant to be wider than any one
finding -- that is what lets findings meet on it, and the specific mechanism survives in the
finding's own title and description. So "the concept is too broad" is never a reason to reject
it, and neither is "related, but not a perfect fit". Take the concept when the finding is a
SPECIAL CASE of it. Reject it only when the finding fits under NOTHING.

Two ways this goes wrong that are worth checking before you answer:

- `concept:method-artefact` is about the APPARATUS used to study the model, not about the model.
  A property of the model itself is never one, however surprising the property is.
- `concept:shortcut` is reliance on a feature that correlates with the target without causing it.
  A response pattern, a null result, a performance gap or an imbalance in the training data is
  not a shortcut, even when the paper calls the behaviour a bias.

`concepts: []` is a correct and common answer. Never invent a concept id.
"""

RULES = """Return YAML only. No prose before or after, no code fences.

concepts:
- id: "concept:depth-dependent-structure"
  because: "the definition says a property is organised progressively across the layers, and this
    finding measures the same property at several depths and reports it sharpening deeper in"

If nothing fits, return `concepts: []` and then say why in one sentence:

concepts: []
why_none: "the finding is a benchmark score with no mechanism named, and no definition covers it"

  id       an identifier copied exactly from the closed list above
  because  the phrase of THAT concept's definition the finding satisfies, and how it satisfies it
"""


def build(finding, concepts):
    return "\n".join([
        TASK,
        "Closed list of concepts. Choose only from these, or return [].\n",
        promptlib.concept_block(concepts),
        "\n" + RULES,
        "\nThe finding:\n",
        "title: %s" % promptlib.squeezed(finding.get("title")),
        "description: %s" % promptlib.squeezed(finding.get("description")),
        "\nReturn the YAML now, and nothing else.",
    ])


def concepts_in(database):
    return {key: entry for key, entry in database.entities.items()
            if entry.get("type") == graph_json.CONCEPT}


def tagged(finding):
    return [link[keys.REF] for link in finding.get("concepts") or [] if keys.REF in link]


def wanted(database, only_untagged=True):
    return {fid: finding for fid, finding in sorted(database.findings.items())
            if not (only_untagged and tagged(finding))}


def agreement(before, after):
    kept = sum(1 for fid, tags in after.items() if set(tags) == set(before.get(fid, [])))
    added = sum(len(set(tags) - set(before.get(fid, []))) for fid, tags in after.items())
    gone = sum(len(set(before.get(fid, [])) - set(tags)) for fid, tags in after.items())
    return {"findings": len(after), "unchanged": kept, "added": added, "removed": gone}


class Unreadable(Exception):
    pass


def read(raw):
    document = yaml.safe_load(raw)
    if not isinstance(document, dict) or "concepts" not in document:
        raise Unreadable("no top-level 'concepts' key")
    written = document.get("concepts")
    if written is None:
        written = []
    if not isinstance(written, list):
        raise Unreadable("'concepts' is not a list")
    return document


def chosen(document, known):
    taken, invented = [], []
    for item in document.get("concepts") or []:
        key = item if isinstance(item, str) else (item or {}).get("id")
        key = str(key or "").strip()
        if not key:
            continue
        if key not in known:
            invented.append(key)
        elif key not in taken:
            taken.append(key)
    return taken, invented
