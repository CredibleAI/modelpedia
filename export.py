import csv
from pathlib import Path

import graph as graph_json

ROOT = Path(__file__).parent
GRAPH = ROOT / "out" / "graph.json"
EXPORT = ROOT / "out" / "csv"

TABLE_FILES = {
    "finding": "findings.csv",
    "model": "models.csv",
    "variant": "variants.csv",
    "concept": "concepts.csv",
    "method": "methods.csv",
    "dataset": "datasets.csv",
    "source": "sources.csv",
    "rw": "related_work.csv",
    "person": "people.csv",
}

EDGE_FILE = "edges.csv"

EDGE_COLUMNS = ["source", "target", "type", "role"]

COLUMN_ORDER = ["id", "label", "name", "title", "evidence_type", "key_metric", "caveat",
                "developer", "authors", "venue", "date", "domain", "parent", "variants",
                "models", "concepts", "sources", "datasets", "methods", "related_work",
                "related_findings", "anchor", "artifact", "description", "note"]

DERIVED_COLUMNS = ("type",)


def link_text(item):
    if not isinstance(item, dict):
        return " ".join(str(item).split())
    text = item.get("ref", "")
    if item.get("role"):
        text += "[%s]" % item["role"]
    variant = item.get("variant")
    if variant and variant != graph_json.VARIANT_NOT_SPECIFIED:
        text += "(%s)" % variant
    return text


def cell(value):
    if value is None:
        return ""
    if isinstance(value, dict):
        return "; ".join(value)
    if isinstance(value, list):
        return "; ".join(link_text(item) for item in value)
    return " ".join(str(value).split())


def columns_for(nodes):
    present = {key for node in nodes for key in node["data"]} - set(DERIVED_COLUMNS)
    present.add("id")
    ordered = [column for column in COLUMN_ORDER if column in present]
    return ordered + sorted(present - set(ordered))


def row_for(node, columns):
    data = dict(node["data"], id=node["id"])
    return {column: cell(data.get(column)) for column in columns}


def write_table(nodes, path):
    columns = columns_for(nodes)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=columns)
        writer.writeheader()
        for node in nodes:
            writer.writerow(row_for(node, columns))
    return len(nodes), len(columns)


def write_edges(graph, path):
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=EDGE_COLUMNS)
        writer.writeheader()
        for edge in graph["edges"]:
            writer.writerow({column: cell(edge.get(column)) for column in EDGE_COLUMNS})
    return len(graph["edges"]), len(EDGE_COLUMNS)


def nodes_by_type(graph):
    grouped = {}
    for node in graph["nodes"]:
        grouped.setdefault(node["type"], []).append(node)
    return grouped


def main():
    graph = graph_json.load(GRAPH)
    grouped = nodes_by_type(graph)
    EXPORT.mkdir(parents=True, exist_ok=True)

    unknown = sorted(set(grouped) - set(TABLE_FILES))
    if unknown:
        for node_type in unknown:
            print("ERROR node type %s has no table in TABLE_FILES" % node_type)
        return 1

    for node_type, filename in TABLE_FILES.items():
        nodes = grouped.get(node_type) or []
        if not nodes:
            continue
        rows, columns = write_table(nodes, EXPORT / filename)
        print("%-20s %3d rows, %2d columns" % (filename, rows, columns))

    rows, columns = write_edges(graph, EXPORT / EDGE_FILE)
    print("%-20s %3d rows, %2d columns" % (EDGE_FILE, rows, columns))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
