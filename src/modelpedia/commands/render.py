import shutil

from modelpedia import graph as graph_json
from modelpedia.graph_io import load_graph
from modelpedia.site.html_bits import (anchor, definition_list, entry, entry_list, escape,
                                       heading, paragraph)
from modelpedia import paths
from modelpedia import record_keys as keys
from modelpedia import schema
from modelpedia.site import about
from modelpedia.site import parts
from modelpedia.site import site_paths

TEXT_ROWS = (
    ("key_metric", "Key metric"),
    ("caveat", "Caveat"),
)

LINK_ROWS = (
    ("models", "Model"),
    ("concepts", "Concepts"),
    ("datasets", "Datasets"),
    ("methods", "Methods"),
    ("related_work", "Related work"),
)

TAGLINE = "Findings about machine learning models"

LEDE = ("A finding is a claim about how one model behaves, made by someone other than its "
        "authors, after the fact. Findings about different models meet on the mechanisms they "
        "describe.")


def home_body(view):
    models = sorted(parts.nodes_of(view, graph_json.MODEL), key=parts.by_reach(view))
    findings = sorted(parts.nodes_of(view, graph_json.FINDING), key=lambda node: node["id"])
    registries = [node_type for node_type in graph_json.PAGE_TYPES
                  if node_type.name != graph_json.FINDING]
    return "".join([
        "<header><h1>%s%s</h1>%s</header>" % (escape(parts.PAGE_TITLE), parts.STATUS_BADGE,
                                              paragraph(TAGLINE, "tagline")),
        paragraph(escape(LEDE), "lede"),
        heading("Models"),
        entry_list([entry("", parts.link(view, node["id"]) + '<span class="entry-note">%s</span>'
                          % escape(parts.join_values(node["data"].get("modality") or [])),
                          parts.finding_count(view, node["id"])) for node in models]),
        heading("Findings"),
        entry_list([parts.finding_entry(view, node) for node in findings]),
        heading("Registries"),
        entry_list([entry("", anchor(site_paths.href(view.here,
                                                     site_paths.registry_page(node_type.name)),
                                     escape(node_type.label)),
                          "%d" % len(parts.nodes_of(view, node_type.name)))
                    for node_type in registries]),
    ])


def registry_body(view, node_type, title):
    if node_type == graph_json.FINDING:
        nodes = sorted(parts.nodes_of(view, node_type), key=lambda node: node["id"])
        items = [parts.finding_entry(view, node) for node in nodes]
    else:
        items = [parts.entity_entry(view, node)
                 for node in sorted(parts.nodes_of(view, node_type), key=parts.by_reach(view))]
    return "<header><h1>%s</h1></header>%s" % (escape(title), entry_list(items))


def finding_body(view, node):
    data = node["data"]
    rows = [("Evidence", parts.badge(data["evidence_type"]) if data.get("evidence_type") else "")]
    rows += [(term, escape(data[field].strip()) if data.get(field) else "")
             for field, term in TEXT_ROWS]
    rows += [(term, parts.model_row(view, data) if field == "models"
              else ", ".join(parts.link_items(view, data, field)))
             for field, term in LINK_ROWS]
    rows.append(("Related findings",
                 ", ".join(parts.link(view, fid, label=fid)
                           for fid in data.get(schema.RELATED_FINDINGS_FIELD) or [])))
    rows.append(("Extraction", parts.badge(data["extracted_by"])))
    return "".join([
        '<header><h1><span class="ident">%s</span>%s</h1></header>'
        % (escape(node["id"]), escape(data["title"])),
        parts.byline(view, data),
        paragraph(escape(data["description"].strip())),
        definition_list(rows),
    ])


def entity_body(view, node):
    data = node["data"]
    out = ["<header><h1>%s</h1></header>" % escape(node["label"]), parts.entity_meta(view, node)]
    if data.get("description"):
        out.append(paragraph(escape(data["description"].strip())))

    rows = []
    if data.get("note") and data.get(keys.ANCHOR):
        rows.append(("Note", escape(data["note"].strip())))
    rows.append(("Variants", parts.variant_names(node)))
    out.append(definition_list(rows))

    used_by = parts.findings_using(view, node["id"])
    if used_by:
        out += [heading("Findings"), entry_list(used_by)]

    if node["type"] == graph_json.MODEL:
        bridges = parts.bridge_entries(view, node)
        if bridges:
            out += [heading("Shared mechanisms"), entry_list(bridges)]
    return "".join(out)


def iter_pages(graph, stylesheet=None):
    stylesheet = stylesheet if stylesheet is not None else parts.stylesheet_text()
    base = parts.View(graph=graph,
                nodes=graph_json.nodes_by_id(graph),
                usage=graph_json.usage_by_entity(graph),
                reached=graph_json.findings_reaching(graph),
                here=parts.HOME)
    yield parts.STYLESHEET, stylesheet
    yield parts.HOME, parts.page(base, parts.PAGE_TITLE, home_body(base), body_class="home")

    here = base._replace(here=about.PATH)
    yield about.PATH, parts.page(here, "%s — %s" % (about.TITLE, parts.PAGE_TITLE),
                           about.body(here, parts.link, parts.external_link), body_class="about")

    for node_type in graph_json.PAGE_TYPES:
        view = base._replace(here=site_paths.registry_page(node_type.name))
        yield view.here, parts.page(view, "%s — %s" % (node_type.label, parts.PAGE_TITLE),
                              registry_body(view, node_type.name, node_type.label))

    for node in base.nodes.values():
        if not graph_json.NODE_TYPE_BY_NAME[node["type"]].url_segment:
            continue
        view = base._replace(here=site_paths.page_of(node))
        body = (finding_body(view, node) if node["type"] == graph_json.FINDING
                else entity_body(view, node))
        yield view.here, parts.page(view, "%s — %s" % (node["label"], parts.PAGE_TITLE), body)


def render_site(graph, stylesheet=None):
    return dict(iter_pages(graph, stylesheet=stylesheet))


def main():
    graph = load_graph(paths.GRAPH)
    stylesheet = parts.stylesheet_text()
    staging = paths.SITE.with_name(paths.SITE.name + paths.PARTIAL)
    shutil.rmtree(staging, ignore_errors=True)
    pages = 0
    for path, document in iter_pages(graph, stylesheet=stylesheet):
        target = staging / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(document, encoding="utf-8")
        if path != parts.STYLESHEET:
            pages += 1
    shutil.rmtree(paths.SITE, ignore_errors=True)
    staging.replace(paths.SITE)
    print("wrote %d pages under site/, open site/%s" % (pages, parts.HOME))

