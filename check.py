import sys
from pathlib import Path

import yaml

from modelpedia.console import entry
from modelpedia.build import database
from modelpedia import graph as graph_json
from modelpedia.ingest import link
from modelpedia import record_keys as keys
from modelpedia import schema
from modelpedia.build import validate

RESOLVED = "resolved"
PROPOSED = "proposed"
UNDECIDED = "undecided"


def query_of(ref):
    prefix, separator, slug = str(ref).partition(":")
    if separator and prefix in graph_json.REGISTRY_TYPES:
        return slug.replace("-", " ")
    return str(ref)


def refs_of(finding):
    for field, spec in schema.LINK_FIELDS.items():
        for item in finding.get(field) or []:
            ref = item.get(keys.REF) if isinstance(item, dict) else item
            if ref:
                yield field, spec.registry, str(ref)


def resolve_links(finding, db):
    indexes = {}
    rows = []
    for field, registry, ref in refs_of(finding):
        if registry not in indexes:
            indexes[registry] = link.index_of(db.entities, registry)
        if ref in db.entities:
            actual = db.entities[ref]["type"]
            if registry is None or actual == registry:
                rows.append((field, ref, RESOLVED, ref, "already in the registry"))
            else:
                rows.append((field, ref, UNDECIDED, None,
                             "exists as %s, but %s requires %s" % (actual, field, registry)))
            continue
        resolution = link.resolve(query_of(ref), indexes[registry])
        if resolution.kind == link.HIT:
            rows.append((field, ref, RESOLVED, resolution.slug,
                         "matched by %s" % resolution.how))
        elif resolution.kind == link.CANDIDATES:
            rows.append((field, ref, UNDECIDED, None,
                         "choose one: %s" % ", ".join(resolution.candidates)))
        else:
            rows.append((field, ref, PROPOSED, None,
                         "no match in %s, propose a new entity" % (registry or "any registry")))
    return rows


def schema_errors(candidate, db, baseline=None):
    raw_id = candidate.get("id")
    if raw_id is not None and not isinstance(raw_id, str):
        return "CANDIDATE", ["CANDIDATE: id is not a string"]
    fid = raw_id or "CANDIDATE"
    if fid in db.findings:
        return fid, ["%s: id already exists in data/findings" % fid]
    merged = dict(db.findings)
    merged[fid] = candidate
    probe = database.Database(vocabularies=db.vocabularies,
                              entities=db.entities, findings=merged)
    if baseline is None:
        baseline = set(validate.errors(db))
    return fid, [error for error in validate.errors(probe) if error not in baseline]


def render(fid, errors, rows):
    lines = ["candidate %s" % fid, "", "schema"]
    if errors:
        lines += ["  ERROR %s" % error for error in errors]
    else:
        lines.append(entry("ok", "validates against the live registries"))

    lines += ["", "links"]
    if not rows:
        lines.append(entry("none", "the candidate references no entities"))
    for field, ref, state, slug, note in rows:
        label = slug if state == RESOLVED else state.upper()
        lines.append(entry("%s %s" % (field, ref), "%s -- %s" % (label, note)))

    undecided = [row for row in rows if row[2] == UNDECIDED]
    proposed = [row for row in rows if row[2] == PROPOSED]
    lines += ["", "verdict"]
    lines.append(entry("schema errors", len(errors)))
    lines.append(entry("links needing a human", "%d undecided, %d proposed"
                       % (len(undecided), len(proposed))))
    if not errors and not undecided and not proposed:
        lines.append(entry("ready", "nothing blocks promotion to a draft record"))
    return "\n".join(lines)


def main():
    if len(sys.argv) != 2:
        print("usage: python3 check.py path/to/candidate.yaml")
        return 2
    path = Path(sys.argv[1])
    if not path.is_file():
        print("no such file: %s" % path)
        return 2

    try:
        candidate = database.read_yaml(path)
        db = database.load()
    except (OSError, ValueError, yaml.YAMLError) as error:
        print("ERROR %s" % error)
        return 1

    fid, errors = schema_errors(candidate, db)
    rows = resolve_links(candidate, db)
    print(render(fid, errors, rows))
    blocked = errors or [row for row in rows if row[2] != RESOLVED]
    return 1 if blocked else 0


if __name__ == "__main__":
    raise SystemExit(main())
