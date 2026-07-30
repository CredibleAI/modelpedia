from pathlib import Path

import graph as graph_json


def load_graph(path: Path):
    graph = graph_json.load(path)
    version = graph.get("format_version")
    if version != graph_json.FORMAT_VERSION:
        raise ValueError(
            "graph format_version is %r, expected %r" % (version, graph_json.FORMAT_VERSION)
        )
    return graph
