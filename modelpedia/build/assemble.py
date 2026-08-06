from modelpedia import graph as graph_json
from modelpedia import record_keys as keys
from modelpedia import schema


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
        for field, spec in schema.LINK_FIELDS.items():
            for link in finding.get(field) or []:
                edges.append(edge(fid, link[keys.REF], spec.edge_type, link.get(keys.ROLE)))
                variant = link.get(keys.VARIANT)
                if variant and variant != graph_json.VARIANT_NOT_SPECIFIED:
                    edges.append(edge(fid, variant, graph_json.EDGE_ABOUT_VARIANT))
        for related in finding.get(schema.RELATED_FINDINGS_FIELD) or []:
            edges.append(edge(fid, related, graph_json.EDGE_RELATES_TO_FINDING))
    return edges


def variant_edges(entities):
    return [edge(key, entity["parent"], graph_json.EDGE_VARIANT_OF)
            for key, entity in entities.items() if entity["type"] == graph_json.VARIANT]


def author_edges(entities):
    return [edge(key, author[keys.REF], graph_json.EDGE_AUTHORED_BY)
            for key, entity in entities.items()
            for author in entity.get(keys.AUTHORS) or []]


def graph_from(database):
    return {
        "format_version": graph_json.FORMAT_VERSION,
        "nodes": finding_nodes(database.findings) + entity_nodes(database.entities),
        "edges": (finding_edges(database.findings)
                  + variant_edges(database.entities)
                  + author_edges(database.entities)),
    }
