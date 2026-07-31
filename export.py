import csv
from pathlib import Path

from modelpedia import graph as graph_json
from modelpedia.graph_io import load_graph
from modelpedia import record_keys as keys

ROOT = Path(__file__).parent
GRAPH = ROOT / "out" / "graph.json"
EXPORT = ROOT / "out" / "csv"

TABLE_FILES = {
    graph_json.FINDING: "findings.csv",
    graph_json.MODEL: "models.csv",
    graph_json.VARIANT: "variants.csv",
    graph_json.CONCEPT: "concepts.csv",
    graph_json.METHOD: "methods.csv",
    graph_json.DATASET: "datasets.csv",
    graph_json.SOURCE: "sources.csv",
    graph_json.RELATED_WORK: "related_work.csv",
    graph_json.PERSON: "people.csv",
}

EDGE_FILE = "edges.csv"

EDGE_COLUMNS = ["source", "target", "type", "role"]

REQUIRED_NONEMPTY_TYPES = (
    graph_json.FINDING,
    graph_json.MODEL,
    graph_json.CONCEPT,
    graph_json.METHOD,
    graph_json.DATASET,
    graph_json.SOURCE,
    graph_json.RELATED_WORK,
    graph_json.PERSON,
)

COLUMN_ORDER = ["id", "label", "name", "title", "review_status", "extracted_by",
                "evidence_type", "key_metric", "caveat", "developer", "authors", "venue",
                "date", "modality", "domain", "task", "parent", "variants",
                "models", "concepts", "sources", "datasets", "methods", "related_work",
                "related_findings", "anchor", "artifact", "description", "note"]

DERIVED_COLUMNS = ("type",)


def link_text(item):
    if not isinstance(item, dict):
        return " ".join(str(item).split())
    text = item.get(keys.REF, "")
    if item.get(keys.ROLE):
        text += "[%s]" % item[keys.ROLE]
    variant = item.get(keys.VARIANT)
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
    unplaced = sorted(present - set(COLUMN_ORDER))
    if unplaced:
        raise KeyError("columns missing from COLUMN_ORDER in export.py: %s" % ", ".join(unplaced))
    return [column for column in COLUMN_ORDER if column in present]


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


def missing_required_types(grouped):
    return sorted(node_type for node_type in REQUIRED_NONEMPTY_TYPES if not grouped.get(node_type))


def main():
    graph = load_graph(GRAPH)
    grouped = nodes_by_type(graph)
    EXPORT.mkdir(parents=True, exist_ok=True)

    unknown = sorted(set(grouped) - set(TABLE_FILES))
    if unknown:
        for node_type in unknown:
            print("ERROR node type %s has no table in TABLE_FILES" % node_type)
        return 1

    missing = missing_required_types(grouped)
    if missing:
        for node_type in missing:
            print("ERROR node type %s has no rows in graph.json" % node_type)
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
