import posixpath

from modelpedia import graph as graph_json

INDEX = "index.html"


def html_id(node_id):
    return node_id.replace(":", "-")


def slug_of(node_id):
    return node_id.partition(":")[2] or node_id


def page_of(node):
    return "%s/%s/%s" % (graph_json.URL_SEGMENTS[node["type"]], slug_of(node["id"]), INDEX)


def registry_page(node_type):
    return "%s/%s" % (graph_json.URL_SEGMENTS[node_type], INDEX)


def target_of(node_id, nodes):
    node = nodes[node_id]
    if node["type"] == graph_json.VARIANT:
        parent = nodes[node["data"]["parent"]]
        anchor = html_id(node_id)
        return "%s#%s" % (page_of(parent), anchor)
    return page_of(node)


def href(here, target):
    path, _, fragment = target.partition("#")
    relative = posixpath.relpath(path, posixpath.dirname(here) or ".")
    if fragment:
        return relative + "#" + fragment
    return relative
