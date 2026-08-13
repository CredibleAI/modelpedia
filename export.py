import csv
import shutil

from modelpedia import graph as graph_json
from modelpedia.graph_io import load_graph
from modelpedia import paths
from modelpedia import record_keys as keys

EDGE_FILE = "edges.csv"

EDGE_COLUMNS = ["source", "target", "type", "role"]

COLUMN_ORDER = ["id", "label", "name", "title", "extracted_by",
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
    return sorted(node_type.name for node_type in graph_json.NODE_TYPES
                  if node_type.required and not grouped.get(node_type.name))


def main():
    graph = load_graph(paths.GRAPH)
    grouped = nodes_by_type(graph)

    unknown = sorted(set(grouped) - set(graph_json.NODE_TYPE_BY_NAME))
    if unknown:
        for node_type in unknown:
            print("ERROR node type %s is not declared in graph.py NODE_TYPES" % node_type)
        return 1

    missing = missing_required_types(grouped)
    if missing:
        for node_type in missing:
            print("ERROR node type %s has no rows in graph.json" % node_type)
        return 1

    staging = paths.CSV.with_name(paths.CSV.name + paths.PARTIAL)
    shutil.rmtree(staging, ignore_errors=True)
    staging.mkdir(parents=True)

    written = []
    for node_type in graph_json.NODE_TYPES:
        nodes = grouped.get(node_type.name) or []
        if not nodes:
            continue
        written.append((node_type.table_file,) + write_table(nodes, staging / node_type.table_file))

    written.append((EDGE_FILE,) + write_edges(graph, staging / EDGE_FILE))

    shutil.rmtree(paths.CSV, ignore_errors=True)
    staging.replace(paths.CSV)

    for filename, rows, columns in written:
        print("%-20s %3d rows, %2d columns" % (filename, rows, columns))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
