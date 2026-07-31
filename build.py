import re
from pathlib import Path
from typing import NamedTuple

import yaml

from modelpedia import graph as graph_json
from modelpedia import record_keys as keys
from modelpedia import report as audit

ROOT = Path(__file__).parent
DATA = ROOT / "data"
REGISTRIES = DATA / "registries"
FINDINGS = DATA / "findings"
VOCABULARIES = DATA / "vocabularies.yaml"
OUT = ROOT / "out"

REGISTRY_FILES = {
    graph_json.MODEL: "models.yaml",
    graph_json.SOURCE: "sources.yaml",
    graph_json.DATASET: "datasets.yaml",
    graph_json.METHOD: "methods.yaml",
    graph_json.RELATED_WORK: "related_work.yaml",
    graph_json.CONCEPT: "concepts.yaml",
    graph_json.PERSON: "people.yaml",
}

ANY_REGISTRY = None


class LinkField(NamedTuple):
    registry: str | None
    edge_type: str


LINK_FIELDS = {
    "models": LinkField(graph_json.MODEL, graph_json.EDGE_ABOUT_MODEL),
    "concepts": LinkField(graph_json.CONCEPT, graph_json.EDGE_TAGGED_CONCEPT),
    "sources": LinkField(graph_json.SOURCE, graph_json.EDGE_REPORTED_IN),
    "datasets": LinkField(graph_json.DATASET, graph_json.EDGE_USES_DATASET),
    "methods": LinkField(graph_json.METHOD, graph_json.EDGE_USES_METHOD),
    "related_work": LinkField(ANY_REGISTRY, graph_json.EDGE_CITES),
}

REQUIRED_FIELDS = ("id", "title", "description", "concepts", "models", "sources",
                   "review_status", "extracted_by")

OPTIONAL_FIELDS = ("key_metric", "caveat", "related_findings")

CLOSED_FIELDS = ("evidence_type", "review_status", "extracted_by")

MODEL_FACETS = ("modality", "domain", "task")

ROLE_FIELDS = ("datasets", "methods", "related_work")

KNOWN_FIELDS = set(REQUIRED_FIELDS) | set(CLOSED_FIELDS) | set(OPTIONAL_FIELDS) | set(LINK_FIELDS)

ROLE_SCOPE = "role"

VOCABULARY_SCOPES = {
    graph_json.FINDING: CLOSED_FIELDS,
    graph_json.MODEL: MODEL_FACETS,
    ROLE_SCOPE: ROLE_FIELDS,
}

AUTHORS = "authors"
VARIANTS = "variants"

SLUG = re.compile(r"[a-z][a-z0-9-]*")
ISO_DATE = re.compile(r"[0-9]{4}(-[0-9]{2}(-[0-9]{2})?)?")


class Database(NamedTuple):
    vocabularies: dict
    entities: dict
    findings: dict


def read_yaml(path):
    return yaml.safe_load(path.read_text(encoding="utf-8")) or {}


def add_entity(entities, key, entity):
    if key in entities:
        raise ValueError("registry: duplicated identifier %s" % key)
    entities[key] = entity


def load_registries():
    entities = {}
    for node_type, filename in REGISTRY_FILES.items():
        for key, entity in read_yaml(REGISTRIES / filename).items():
            entity = entity or {}
            add_entity(entities, key, dict(entity, type=node_type))
            for variant_key, variant in (entity.get(VARIANTS) or {}).items():
                add_entity(entities, variant_key,
                           dict(variant, type=graph_json.VARIANT, parent=key))
    return entities


def load_findings():
    findings = {}
    for path in sorted(FINDINGS.glob("*.yaml")):
        finding = read_yaml(path)
        fid = finding.get("id")
        if not isinstance(fid, str):
            raise ValueError("finding file %s has no string id" % path.name)
        if path.stem != fid:
            raise ValueError("finding file %s must contain id %s" % (path.name, path.stem))
        if fid in findings:
            raise ValueError("finding id %s is duplicated" % fid)
        findings[fid] = finding
    return findings


def load():
    return Database(
        vocabularies=read_yaml(VOCABULARIES),
        entities=load_registries(),
        findings=load_findings(),
    )


def check_vocabularies(database):
    for scope, names in VOCABULARY_SCOPES.items():
        terms_by_name = database.vocabularies.get(scope)
        if not isinstance(terms_by_name, dict):
            yield "vocabularies: %s is missing or is not a mapping" % scope
            continue
        for name in names:
            terms = terms_by_name.get(name)
            if not isinstance(terms, list) or not terms:
                yield "vocabularies: %s.%s is missing or is not a non-empty list" % (scope, name)
                continue
            for term in terms:
                if not isinstance(term, str) or not SLUG.fullmatch(term):
                    yield "vocabularies: %s.%s has a term that is not kebab-case: %s" % (
                        scope, name, term)


def check_entity_keys(database):
    for key, entity in database.entities.items():
        prefix, _, slug = key.partition(":")
        if not slug or not SLUG.fullmatch(slug):
            yield "registry: %s is not a kebab-case identifier" % key
        if prefix != entity["type"]:
            yield "registry: %s is a %s, so its identifier must start with %s:" % (
                key, entity["type"], entity["type"])


def check_entity_dates(database):
    for key, entity in database.entities.items():
        date = entity.get("date")
        if date is not None and not (isinstance(date, str) and ISO_DATE.fullmatch(date)):
            yield "registry: %s has a date that is not a quoted ISO string" % key


def check_authors(database):
    for key, entity in database.entities.items():
        for author in entity.get(AUTHORS) or []:
            if not isinstance(author, dict) or keys.REF not in author:
                yield "registry: %s has an author that is not a reference" % key
                continue
            ref = author[keys.REF]
            if not ref.startswith(graph_json.PERSON + ":"):
                yield "registry: %s lists %s as an author, which is not a person" % (key, ref)
            elif ref not in database.entities:
                yield "registry: %s lists unknown author %s" % (key, ref)


def check_model_facets(database):
    facet_terms = database.vocabularies[graph_json.MODEL]
    for key, entity in database.entities.items():
        if entity["type"] != graph_json.MODEL:
            continue
        for facet in MODEL_FACETS:
            values = entity.get(facet)
            if values is None:
                continue
            if not isinstance(values, list):
                yield "registry: %s has %s that is not a list" % (key, facet)
                continue
            for value in values:
                if value not in facet_terms[facet]:
                    yield "registry: %s has unknown %s %s" % (key, facet, value)


def reference_error(field, spec, ref, entities):
    registry = ref.partition(":")[0]
    if spec.registry is not ANY_REGISTRY and registry != spec.registry:
        return "%s does not belong in %s" % (ref, field)
    if registry not in graph_json.REGISTRY_TYPES:
        return "%s has no registry" % ref
    if ref not in entities:
        return "%s is not in any registry" % ref
    return None


def check_finding_fields(fid, finding, database):
    for field in REQUIRED_FIELDS:
        if not finding.get(field):
            yield "%s: missing %s" % (fid, field)

    for field in sorted(set(finding) - KNOWN_FIELDS):
        yield "%s: %s is not a field in the schema" % (fid, field)

    for field in CLOSED_FIELDS:
        value = finding.get(field)
        if value and value not in database.vocabularies[graph_json.FINDING][field]:
            yield "%s: unknown %s %s" % (fid, field, value)


def check_finding_links(fid, finding, database):
    for field, spec in LINK_FIELDS.items():
        for link in finding.get(field) or []:
            if not isinstance(link, dict) or keys.REF not in link:
                yield "%s: %s has an entry that is not a reference" % (fid, field)
                continue
            allowed = {keys.REF}
            if field in ROLE_FIELDS:
                allowed.add(keys.ROLE)
            if field == "models":
                allowed.add(keys.VARIANT)
            unknown_keys = sorted(set(link) - allowed)
            if unknown_keys:
                yield "%s: %s has unknown keys: %s" % (fid, field, ", ".join(unknown_keys))

            ref = link[keys.REF]
            error = reference_error(field, spec, ref, database.entities)
            if error:
                yield "%s: %s" % (fid, error)

            role = link.get(keys.ROLE)
            if role and role not in database.vocabularies[ROLE_SCOPE].get(field, []):
                yield "%s: unknown role %s on %s" % (fid, role, ref)

            variant = link.get(keys.VARIANT)
            if variant and variant != graph_json.VARIANT_NOT_SPECIFIED:
                if variant not in database.entities:
                    yield "%s: %s is not a known variant" % (fid, variant)
                elif database.entities[variant]["type"] != graph_json.VARIANT:
                    yield "%s: %s is not a variant" % (fid, variant)
                elif field == "models" and database.entities[variant]["parent"] != ref:
                    yield "%s: %s does not belong to %s" % (fid, variant, ref)

    for related in finding.get("related_findings") or []:
        if related not in database.findings:
            yield "%s: %s is not a known finding" % (fid, related)


REGISTRY_CHECKS = (check_entity_keys, check_entity_dates, check_authors, check_model_facets)

FINDING_CHECKS = (check_finding_fields, check_finding_links)


def validate(database):
    vocabulary_errors = list(check_vocabularies(database))
    if vocabulary_errors:
        return vocabulary_errors

    errors = [error for check in REGISTRY_CHECKS for error in check(database)]
    for fid, finding in database.findings.items():
        errors += [error for check in FINDING_CHECKS for error in check(fid, finding, database)]
    return errors


def finding_nodes(findings):
    return [{"id": fid, "type": graph_json.FINDING, "label": finding["title"], "data": finding}
            for fid, finding in findings.items()]


def entity_nodes(entities):
    return [{"id": key,
             "type": entity["type"],
             "label": entity.get("name") or entity.get("title") or key,
             "data": entity}
            for key, entity in entities.items()]


def edge(source, target, edge_type, role=None):
    return {"source": source, "target": target, "type": edge_type, "role": role}


def finding_edges(findings):
    edges = []
    for fid, finding in findings.items():
        for field, spec in LINK_FIELDS.items():
            for link in finding.get(field) or []:
                edges.append(edge(fid, link[keys.REF], spec.edge_type, link.get(keys.ROLE)))
                variant = link.get(keys.VARIANT)
                if variant and variant != graph_json.VARIANT_NOT_SPECIFIED:
                    edges.append(edge(fid, variant, graph_json.EDGE_ABOUT_VARIANT))
        for related in finding.get("related_findings") or []:
            edges.append(edge(fid, related, graph_json.EDGE_RELATES_TO_FINDING))
    return edges


def variant_edges(entities):
    return [edge(key, entity["parent"], graph_json.EDGE_VARIANT_OF)
            for key, entity in entities.items() if entity["type"] == graph_json.VARIANT]


def author_edges(entities):
    return [edge(key, author[keys.REF], graph_json.EDGE_AUTHORED_BY)
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


def main():
    try:
        database = load()
    except (ValueError, yaml.YAMLError) as error:
        print("ERROR %s" % error)
        return 1
    errors = validate(database)
    if errors:
        for error in errors:
            print("ERROR %s" % error)
        return 1
    graph = build(database)
    OUT.mkdir(exist_ok=True)
    graph_json.dump(graph, OUT / "graph.json")
    print(audit.render(database.findings, database.entities, graph))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
