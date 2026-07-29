import json
from typing import NamedTuple

FORMAT_VERSION = 2

FINDING = "finding"
VARIANT = "variant"

EDGE_ABOUT_VARIANT = "about_variant"
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
