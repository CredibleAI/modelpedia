import json
from typing import NamedTuple

FORMAT_VERSION = 2

FINDING = "finding"
MODEL = "model"
VARIANT = "variant"
CONCEPT = "concept"
METHOD = "method"
DATASET = "dataset"
SOURCE = "source"
RELATED_WORK = "rw"
PERSON = "person"

NODE_TYPES = (FINDING, MODEL, VARIANT, CONCEPT, METHOD, DATASET, SOURCE, RELATED_WORK, PERSON)

REGISTRY_TYPES = (MODEL, SOURCE, DATASET, METHOD, RELATED_WORK, CONCEPT, PERSON)

TYPES_WITHOUT_ANCHORS = (CONCEPT, PERSON)

URL_SEGMENTS = {
    FINDING: "findings",
    MODEL: "models",
    CONCEPT: "concepts",
    METHOD: "methods",
    DATASET: "datasets",
    SOURCE: "sources",
    RELATED_WORK: "related-work",
    PERSON: "people",
}

EDGE_ABOUT_MODEL = "about_model"
EDGE_ABOUT_VARIANT = "about_variant"
EDGE_TAGGED_CONCEPT = "tagged_concept"
EDGE_REPORTED_IN = "reported_in"
EDGE_USES_DATASET = "uses_dataset"
EDGE_USES_METHOD = "uses_method"
EDGE_CITES = "cites"
EDGE_VARIANT_OF = "variant_of"
EDGE_RELATES_TO_FINDING = "relates_to_finding"
EDGE_AUTHORED_BY = "authored_by"

VARIANT_NOT_SPECIFIED = "not-specified-in-source"


class Usage(NamedTuple):
    finding: str
    role: str | None


def load(path):
    return json.loads(path.read_text(encoding="utf-8"))


def dump(graph, path):
    path.write_text(json.dumps(graph, indent=2, ensure_ascii=False), encoding="utf-8")


def nodes_by_id(graph):
    return {node["id"]: node for node in graph["nodes"]}


def nodes_of_type(nodes, node_type):
    return [node for node in nodes.values() if node["type"] == node_type]


def usage_by_entity(graph):
    nodes = nodes_by_id(graph)
    usage = {}
    for edge in graph["edges"]:
        if nodes[edge["source"]]["type"] == FINDING:
            usage.setdefault(edge["target"], []).append(Usage(edge["source"], edge["role"]))
    return usage


def finding_count(usages):
    return len({usage.finding for usage in usages})


def authors_by_source(graph):
    authors = {}
    for edge in graph["edges"]:
        if edge["type"] == EDGE_AUTHORED_BY:
            authors.setdefault(edge["source"], []).append(edge["target"])
    return authors


def findings_reaching(graph):
    reached = {key: {usage.finding for usage in usages}
               for key, usages in usage_by_entity(graph).items()}
    for source_id, people in authors_by_source(graph).items():
        findings = reached.get(source_id)
        if not findings:
            continue
        for person in people:
            reached.setdefault(person, set()).update(findings)
    return reached


def shared_entities(reached):
    return [key for key, findings in reached.items() if len(findings) > 1]


def targets_by_finding(graph, edge_type):
    targets = {}
    for edge in graph["edges"]:
        if edge["type"] == edge_type:
            targets.setdefault(edge["source"], set()).add(edge["target"])
    return targets


def models_by_concept(graph):
    models = targets_by_finding(graph, EDGE_ABOUT_MODEL)
    reached = {}
    for finding, concepts in targets_by_finding(graph, EDGE_TAGGED_CONCEPT).items():
        for concept in concepts:
            reached.setdefault(concept, set()).update(models.get(finding, ()))
    return reached


def concept_bridges(graph, model_id):
    return sorted((concept, sorted(models - {model_id}))
                  for concept, models in models_by_concept(graph).items()
                  if model_id in models and len(models) > 1)
