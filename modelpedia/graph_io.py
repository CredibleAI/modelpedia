import json
from pathlib import Path

from modelpedia import graph as graph_json
from modelpedia import paths


def load_graph(path: Path):
    graph = json.loads(path.read_text(encoding="utf-8"))
    version = graph.get("format_version")
    if version != graph_json.FORMAT_VERSION:
        raise ValueError(
            "graph format_version is %r, expected %r" % (version, graph_json.FORMAT_VERSION)
        )
    return graph


def dump_graph(graph, path: Path):
    path.parent.mkdir(parents=True, exist_ok=True)
    document = json.dumps(graph, indent=2, ensure_ascii=False)
    partial = path.with_suffix(path.suffix + paths.PARTIAL)
    partial.write_text(document, encoding="utf-8")
    partial.replace(path)
