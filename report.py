from collections import Counter
from typing import NamedTuple

import graph as graph_json

KEY_COLUMN = 40

TYPES_WITH_ANCHORS = tuple(node_type for node_type in graph_json.REGISTRY_TYPES
                           if node_type not in graph_json.TYPES_WITHOUT_ANCHORS)


class Audit(NamedTuple):
    findings: dict
    entities: dict
    graph: dict
    reached: dict


def entry(key, note):
    return "  %-*s %s" % (KEY_COLUMN, key, note)


def header(audit):
    return ["format %d, %d findings, %d nodes, %d edges" % (
        graph_json.FORMAT_VERSION, len(audit.findings),
        len(audit.graph["nodes"]), len(audit.graph["edges"]))]


def record_status(audit):
    counts = Counter((finding.get("review_status"), finding.get("extracted_by"))
                     for finding in audit.findings.values())
    return ["record status"] + [entry("%s / %s" % pair, count)
                                for pair, count in sorted(counts.items())]


def concept_use(audit):
    counts = Counter(link["ref"]
                     for finding in audit.findings.values()
                     for link in finding["concepts"])
    unused = sorted(key for key, entity in audit.entities.items()
                    if entity["type"] == graph_json.CONCEPT and key not in counts)
    return (["concepts"]
            + [entry(key, count) for key, count in counts.most_common()]
            + [entry(key, "0  (unused)") for key in unused])


def shared_nodes(audit):
    counts = Counter({key: len(findings) for key, findings in audit.reached.items()})
    shared = [(key, count) for key, count in counts.most_common() if count > 1]
    return (["shared nodes, reached from more than one finding"]
            + [entry(key, "x%d" % count) for key, count in shared]
            + ["  %d of %d entities are shared" % (len(shared), len(counts))])


def findings_without_datasets(audit):
    empty = sorted(fid for fid, finding in audit.findings.items() if not finding.get("datasets"))
    lines = ["findings with no dataset link"]
    if not empty:
        return lines + ["  none"]
    return lines + [entry(fid, "source does not state one") for fid in empty]


def entities_without_anchors(audit):
    missing = sorted(key for key, entity in audit.entities.items()
                     if entity["type"] in TYPES_WITH_ANCHORS and not entity.get("anchor"))
    return ["gaps"] + [
        entry(key, "no anchor, %s" % ("artifact only" if audit.entities[key].get("artifact")
                                      else "nothing to link to"))
        for key in missing]


def entities_without_findings(audit):
    unused = sorted(key for key in audit.entities if key not in audit.reached)
    lines = ["registry entries not reached by any finding"]
    if not unused:
        return lines + ["  none"]
    return lines + [entry(key, "(%s)" % audit.entities[key]["type"]) for key in unused]


SECTIONS = (record_status, concept_use, shared_nodes, findings_without_datasets,
            entities_without_anchors, entities_without_findings)


def render(findings, entities, graph):
    audit = Audit(findings=findings, entities=entities, graph=graph,
                  reached=graph_json.findings_reaching(graph))
    lines = header(audit)
    for section in SECTIONS:
        lines += [""] + section(audit)
    return "\n".join(lines)
