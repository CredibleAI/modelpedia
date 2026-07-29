import re
from collections import Counter
from pathlib import Path
from typing import NamedTuple

import yaml

import graph as graph_json

ROOT = Path(__file__).parent
DATA = ROOT / "data"
OUT = ROOT / "out"

CONCEPT = "concept"
PERSON = "person"

REGISTRY_FILES = {
    "model": "models.yaml",
    "source": "sources.yaml",
    "dataset": "datasets.yaml",
    "method": "methods.yaml",
    "rw": "related_work.yaml",
    CONCEPT: "concepts.yaml",
    PERSON: "people.yaml",
}

REGISTRIES_WITHOUT_ANCHORS = (CONCEPT, PERSON)

EXTERNAL_REGISTRIES = tuple(prefix for prefix in REGISTRY_FILES
                            if prefix not in REGISTRIES_WITHOUT_ANCHORS)

AUTHORS = "authors"

EVIDENCE_TYPE = "evidence_type"

ANY_REGISTRY = None


class LinkField(NamedTuple):
    registry: str | None
    edge_type: str


LINK_FIELDS = {
    "models": LinkField("model", "about_model"),
    "concepts": LinkField(CONCEPT, "tagged_concept"),
    "sources": LinkField("source", "reported_in"),
    "datasets": LinkField("dataset", "uses_dataset"),
    "methods": LinkField("method", "uses_method"),
    "related_work": LinkField(ANY_REGISTRY, "cites"),
}

REQUIRED_FIELDS = ["id", "title", "description", "concepts", "models", "sources"]

SLUG = re.compile(r"[a-z][a-z0-9-]*")
ISO_DATE = re.compile(r"[0-9]{4}(-[0-9]{2}(-[0-9]{2})?)?")

REPORT_COLUMN = 40


class Database(NamedTuple):
    vocabularies: dict
    entities: dict
    findings: dict


def read_yaml(path):
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def load_registries():
    entities = {}
    for prefix, filename in REGISTRY_FILES.items():
        for key, entity in read_yaml(DATA / filename).items():
            entity = entity or {}
            entities[key] = dict(entity, type=prefix)
            for variant_key, variant in (entity.get("variants") or {}).items():
                entities[variant_key] = dict(variant, type=graph_json.VARIANT, parent=key)
    return entities


def load_findings():
    findings = {}
    for path in sorted((DATA / "findings").glob("*.yaml")):
        finding = read_yaml(path)
        findings[finding["id"]] = finding
    return findings


def load():
    return Database(
        vocabularies=read_yaml(DATA / "vocabularies.yaml"),
        entities=load_registries(),
        findings=load_findings(),
    )


def check_entity_slugs(entities):
    for key in entities:
        _, _, slug = key.partition(":")
        if not slug or not SLUG.fullmatch(slug):
            yield "registry: %s is not a kebab-case identifier" % key


def check_entity_dates(entities):
    for key, entity in entities.items():
        date = entity.get("date")
        if date is not None and not (isinstance(date, str) and ISO_DATE.fullmatch(date)):
            yield "registry: %s has a date that is not a quoted ISO string" % key


def check_authors(entities):
    for key, entity in entities.items():
        for author in entity.get(AUTHORS) or []:
            if not isinstance(author, dict) or "ref" not in author:
                yield "registry: %s has an author that is not a reference" % key
                continue
            ref = author["ref"]
            if not ref.startswith(PERSON + ":"):
                yield "registry: %s lists %s as an author, which is not a person" % (key, ref)
            elif ref not in entities:
                yield "registry: %s lists unknown author %s" % (key, ref)


def check_role_slugs(vocabularies):
    for field, roles in vocabularies["role"].items():
        for role in roles:
            if not SLUG.fullmatch(role):
                yield "vocabularies: role %s on %s is not kebab-case" % (role, field)


def reference_error(field, spec, ref, entities):
    registry = ref.partition(":")[0]
    if spec.registry is not ANY_REGISTRY and registry != spec.registry:
        return "%s does not belong in %s" % (ref, field)
    if registry not in REGISTRY_FILES:
        return "%s has no registry" % ref
    if ref not in entities:
        return "%s is not in any registry" % ref
    return None


def check_finding(fid, finding, database):
    for field in REQUIRED_FIELDS:
        if not finding.get(field):
            yield "%s: missing %s" % (fid, field)

    evidence = finding.get(EVIDENCE_TYPE)
    if evidence and evidence not in database.vocabularies[EVIDENCE_TYPE]:
        yield "%s: unknown evidence_type %s" % (fid, evidence)

    for field, spec in LINK_FIELDS.items():
        for link in finding.get(field) or []:
            if not isinstance(link, dict) or "ref" not in link:
                yield "%s: %s has an entry that is not a reference" % (fid, field)
                continue
            ref = link["ref"]
            error = reference_error(field, spec, ref, database.entities)
            if error:
                yield "%s: %s" % (fid, error)

            role = link.get("role")
            if role and role not in database.vocabularies["role"].get(field, []):
                yield "%s: unknown role %s on %s" % (fid, role, ref)

            variant = link.get("variant")
            if (variant and variant != graph_json.VARIANT_NOT_SPECIFIED
                    and variant not in database.entities):
                yield "%s: %s is not a known variant" % (fid, variant)

    for related in finding.get("related_findings") or []:
        if related not in database.findings:
            yield "%s: %s is not a known finding" % (fid, related)


def validate(database):
    errors = list(check_entity_slugs(database.entities))
    errors += list(check_role_slugs(database.vocabularies))
    errors += list(check_entity_dates(database.entities))
    errors += list(check_authors(database.entities))
    for fid, finding in database.findings.items():
        errors += list(check_finding(fid, finding, database))
    return errors


def finding_nodes(findings):
    return [{"id": fid, "type": graph_json.FINDING, "label": finding["title"], "data": finding}
            for fid, finding in findings.items()]


def entity_nodes(entities):
    nodes = []
    for key, entity in entities.items():
        label = entity.get("name") or entity.get("title") or key
        nodes.append({"id": key, "type": entity["type"], "label": label, "data": entity})
    return nodes


def edge(source, target, edge_type, role=None):
    return {"source": source, "target": target, "type": edge_type, "role": role}


def finding_edges(findings):
    edges = []
    for fid, finding in findings.items():
        for field, spec in LINK_FIELDS.items():
            for link in finding.get(field) or []:
                edges.append(edge(fid, link["ref"], spec.edge_type, link.get("role")))
                variant = link.get("variant")
                if variant and variant != graph_json.VARIANT_NOT_SPECIFIED:
                    edges.append(edge(fid, variant, graph_json.EDGE_ABOUT_VARIANT))
        for related in finding.get("related_findings") or []:
            edges.append(edge(fid, related, graph_json.EDGE_RELATES_TO_FINDING))
    return edges


def variant_edges(entities):
    return [edge(key, entity["parent"], graph_json.EDGE_VARIANT_OF)
            for key, entity in entities.items() if entity["type"] == graph_json.VARIANT]


def author_edges(entities):
    return [edge(key, author["ref"], graph_json.EDGE_AUTHORED_BY)
            for key, entity in entities.items()
            for author in entity.get(AUTHORS) or []]


def build(database):
    return {
        "format_version": graph_json.FORMAT_VERSION,
        "nodes": finding_nodes(database.findings) + entity_nodes(database.entities),
        "edges": (finding_edges(database.findings)
                  + variant_edges(database.entities)
                  + author_edges(database.entities)),
    }


def entry(key, note):
    return "  %-*s %s" % (REPORT_COLUMN, key, note)


def concept_section(database):
    lines = ["concepts"]
    counts = Counter(link["ref"]
                     for finding in database.findings.values()
                     for link in finding["concepts"])
    for key, count in counts.most_common():
        lines.append(entry(key, count))
    unused = sorted(key for key, entity in database.entities.items()
                    if entity["type"] == CONCEPT and key not in counts)
    return lines + [entry(key, "0  (unused)") for key in unused]


def shared_section(reached):
    lines = ["shared nodes, reached from more than one finding"]
    counts = Counter({key: len(findings) for key, findings in reached.items()})
    shared = [(key, count) for key, count in counts.most_common() if count > 1]
    lines += [entry(key, "x%d" % count) for key, count in shared]
    return lines + ["  %d of %d entities are shared" % (len(shared), len(counts))]


def gaps_section(entities):
    lines = ["gaps"]
    for key in sorted(key for key, entity in entities.items()
                      if entity["type"] in EXTERNAL_REGISTRIES and not entity.get("anchor")):
        state = "artifact only" if entities[key].get("artifact") else "nothing to link to"
        lines.append(entry(key, "no anchor, %s" % state))
    return lines


def unused_section(entities, reached):
    lines = ["registry entries not reached by any finding"]
    unused = sorted(key for key in entities if key not in reached)
    lines += [entry(key, "(%s)" % entities[key]["type"]) for key in unused]
    return lines if unused else lines + ["  none"]


def report(database, graph):
    reached = graph_json.findings_reaching(graph)
    header = "format %d, %d findings, %d nodes, %d edges" % (
        graph_json.FORMAT_VERSION, len(database.findings),
        len(graph["nodes"]), len(graph["edges"]))
    sections = [
        concept_section(database),
        shared_section(reached),
        gaps_section(database.entities),
        unused_section(database.entities, reached),
    ]
    lines = [header]
    for section in sections:
        lines += [""] + section
    return "\n".join(lines)


def main():
    database = load()
    errors = validate(database)
    if errors:
        for error in errors:
            print("ERROR %s" % error)
        return 1
    graph = build(database)
    OUT.mkdir(exist_ok=True)
    graph_json.dump(graph, OUT / "graph.json")
    print(report(database, graph))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
