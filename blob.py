import math
import random
import sys
from pathlib import Path

from modelpedia import graph as graph_json
from modelpedia.graph_io import load_graph
from modelpedia.html_bits import escape

ROOT = Path(__file__).resolve().parent
GRAPH = ROOT / "out" / "graph.json"
OUT = ROOT / "out" / "blob.html"

WIDTH = 1400
HEIGHT = 1000
MARGIN = 40
ITERATIONS = 400
SEED = 7

COLOURS = {
    graph_json.FINDING: "#001f33",
    graph_json.MODEL: "#b3402a",
    graph_json.VARIANT: "#d98b7a",
    graph_json.CONCEPT: "#1f7a5a",
    graph_json.METHOD: "#3d6ea8",
    graph_json.DATASET: "#8a6a1f",
    graph_json.SOURCE: "#6b4a8a",
    graph_json.RELATED_WORK: "#9a8fa8",
    graph_json.PERSON: "#a3a3a3",
}

RADII = {graph_json.FINDING: 7, graph_json.MODEL: 8, graph_json.CONCEPT: 6}
DEFAULT_RADIUS = 4


def adjacency(edges):
    linked = {}
    for edge in edges:
        linked.setdefault(edge["source"], set()).add(edge["target"])
        linked.setdefault(edge["target"], set()).add(edge["source"])
    return linked


def within_hops(linked, start, hops):
    seen = {start}
    frontier = {start}
    for _ in range(hops):
        frontier = {other for node in frontier for other in linked.get(node, ())} - seen
        seen |= frontier
    return seen


def subgraph(graph, keep):
    nodes = [node for node in graph["nodes"] if node["id"] in keep]
    edges = [edge for edge in graph["edges"]
             if edge["source"] in keep and edge["target"] in keep]
    return nodes, edges


def layout(nodes, edges):
    rng = random.Random(SEED)
    ids = [node["id"] for node in nodes]
    area = (WIDTH - 2 * MARGIN) * (HEIGHT - 2 * MARGIN)
    k = math.sqrt(area / max(len(ids), 1))
    pos = {node_id: [rng.uniform(0, WIDTH), rng.uniform(0, HEIGHT)] for node_id in ids}
    pairs = [(edge["source"], edge["target"]) for edge in edges]
    temperature = WIDTH / 8

    for step in range(ITERATIONS):
        push = {node_id: [0.0, 0.0] for node_id in ids}
        for i, a in enumerate(ids):
            for b in ids[i + 1:]:
                dx = pos[a][0] - pos[b][0]
                dy = pos[a][1] - pos[b][1]
                distance = math.hypot(dx, dy) or 0.01
                force = k * k / distance
                push[a][0] += dx / distance * force
                push[a][1] += dy / distance * force
                push[b][0] -= dx / distance * force
                push[b][1] -= dy / distance * force
        for a, b in pairs:
            dx = pos[a][0] - pos[b][0]
            dy = pos[a][1] - pos[b][1]
            distance = math.hypot(dx, dy) or 0.01
            force = distance * distance / k
            push[a][0] -= dx / distance * force
            push[a][1] -= dy / distance * force
            push[b][0] += dx / distance * force
            push[b][1] += dy / distance * force
        for node_id in ids:
            dx, dy = push[node_id]
            distance = math.hypot(dx, dy) or 0.01
            limit = min(distance, temperature)
            pos[node_id][0] += dx / distance * limit
            pos[node_id][1] += dy / distance * limit
        temperature *= 1 - (step + 1) / ITERATIONS

    return rescale(pos)


def rescale(pos):
    xs = [point[0] for point in pos.values()]
    ys = [point[1] for point in pos.values()]
    span_x = (max(xs) - min(xs)) or 1
    span_y = (max(ys) - min(ys)) or 1
    scale = min((WIDTH - 2 * MARGIN) / span_x, (HEIGHT - 2 * MARGIN) / span_y)
    return {node_id: (MARGIN + (point[0] - min(xs)) * scale,
                      MARGIN + (point[1] - min(ys)) * scale)
            for node_id, point in pos.items()}


def svg(nodes, edges, pos):
    parts = ['<svg viewBox="0 0 %d %d" xmlns="http://www.w3.org/2000/svg">' % (WIDTH, HEIGHT)]
    for edge in edges:
        start = pos[edge["source"]]
        end = pos[edge["target"]]
        parts.append('<line x1="%.1f" y1="%.1f" x2="%.1f" y2="%.1f" '
                     'stroke="#c8ced2" stroke-width="0.6"/>' % (start[0], start[1],
                                                                end[0], end[1]))
    for node in nodes:
        x, y = pos[node["id"]]
        parts.append('<circle cx="%.1f" cy="%.1f" r="%d" fill="%s"><title>%s</title></circle>'
                     % (x, y, RADII.get(node["type"], DEFAULT_RADIUS),
                        COLOURS.get(node["type"], "#000"),
                        escape("%s  %s" % (node["id"], node["data"].get("name")
                                           or node["data"].get("title") or ""))))
    parts.append("</svg>")
    return "".join(parts)


def legend():
    items = ['<span><i style="background:%s"></i>%s</span>' % (COLOURS[node_type], node_type)
             for node_type in graph_json.NODE_TYPES]
    return '<p class="legend">%s</p>' % "".join(items)


def page(nodes, edges, pos, focus):
    title = "blob map"
    if focus:
        title += " around %s, 2 hops" % focus
    return ("<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\">"
            "<title>%s</title><style>"
            "body{margin:0;padding:1.5rem;font:14px/1.5 ui-sans-serif,system-ui,sans-serif;"
            "background:#fff;color:#001f33}"
            "h1{font-size:1rem;font-weight:500;margin:0 0 .3rem}"
            "p{margin:0 0 .8rem;color:#5c7180;font-size:.8rem}"
            ".legend i{display:inline-block;width:9px;height:9px;border-radius:50%%;"
            "margin-right:.3rem;vertical-align:middle}"
            ".legend span{margin-right:.9rem;white-space:nowrap}"
            "svg{width:100%%;height:auto;border:1px solid #e4e8ea}"
            "</style></head><body>"
            "<h1>%s</h1><p>%d nodes, %d edges. Hover a dot for its identifier. "
            "Throwaway sketch, not part of the pipeline.</p>%s%s</body></html>"
            % (escape(title), escape(title), len(nodes), len(edges), legend(),
               svg(nodes, edges, pos)))


def main():
    graph = load_graph(GRAPH)
    focus = sys.argv[1] if len(sys.argv) > 1 else None
    if focus:
        known = {node["id"] for node in graph["nodes"]}
        if focus not in known:
            print("no such node: %s" % focus)
            return 1
        keep = within_hops(adjacency(graph["edges"]), focus, 2)
    else:
        keep = {node["id"] for node in graph["nodes"]}
    nodes, edges = subgraph(graph, keep)
    pos = layout(nodes, edges)
    OUT.write_text(page(nodes, edges, pos, focus), encoding="utf-8")
    print("wrote %s, %d nodes, %d edges" % (OUT.relative_to(ROOT), len(nodes), len(edges)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
