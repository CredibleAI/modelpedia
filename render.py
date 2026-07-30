import shutil
from pathlib import Path
from typing import NamedTuple

import graph as graph_json
from graph_io import load_graph
from html_bits import anchor, definition_list, entry, entry_list, escape, heading, paragraph
import record_keys as keys
import site_paths

ROOT = Path(__file__).parent
SITE = ROOT / "site"
GRAPH = ROOT / "out" / "graph.json"
ASSETS = ROOT / "assets"

INDEX = "index.html"
HOME = INDEX
STYLESHEET = "style.css"

SECTIONS = (
    (graph_json.FINDING, "Findings"),
    (graph_json.MODEL, "Models"),
    (graph_json.CONCEPT, "Concepts"),
    (graph_json.METHOD, "Methods"),
    (graph_json.DATASET, "Datasets"),
    (graph_json.SOURCE, "Sources"),
    (graph_json.RELATED_WORK, "Related work"),
    (graph_json.PERSON, "People"),
)

TEXT_ROWS = (
    ("evidence_type", "Evidence"),
    ("key_metric", "Key metric"),
    ("caveat", "Caveat"),
)

LINK_ROWS = (
    ("models", "Model"),
    ("concepts", "Concepts"),
    ("sources", "Source"),
    ("datasets", "Datasets"),
    ("methods", "Methods"),
    ("related_work", "Related work"),
)

META_FIELDS = ("developer", "date", "modality", "domain", "task", "venue")

AUTHORS = "authors"

DRAFT = "draft"
DRAFT_LABEL = "not verified"

PAGE_TITLE = "Modelpedia"
TAGLINE = "Findings about machine learning models"

LEDE = ("A finding is a claim about how one model behaves, made by someone other than its "
        "authors, after the fact. Findings about different models meet on the mechanisms they "
        "describe.")

FOOTER = ("%(findings)d findings, %(verified)d verified. "
          "%(nodes)d nodes, %(shared)d shared by more than one finding.")

FONTS = ("https://fonts.googleapis.com/css2?"
         "family=Lexend+Deca:wght@300;400;500&family=Roboto+Mono:wght@400;500&display=swap")


class View(NamedTuple):
    graph: dict
    nodes: dict
    usage: dict
    reached: dict
    here: str


def stylesheet_text():
    return (ASSETS / STYLESHEET).read_text(encoding="utf-8")


def join_values(value):
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value)


def role_marker(role):
    return ' <span class="role">[%s]</span>' % escape(role) if role else ""


def link(view, node_id, label=None, role=None):
    text = escape(label or view.nodes[node_id]["label"])
    return anchor(site_paths.href(view.here, site_paths.target_of(node_id, view.nodes)),
                  text) + role_marker(role)


def is_draft(data):
    return data.get("review_status") == DRAFT


def draft_mark(data):
    return '<span class="draft">%s</span>' % DRAFT_LABEL if is_draft(data) else ""


def finding_authors(view, data):
    authors = []
    for source in data.get("sources") or []:
        for author in view.nodes[source[keys.REF]]["data"].get(AUTHORS) or []:
            if author[keys.REF] not in authors:
                authors.append(author[keys.REF])
    return authors


def link_items(view, data, field):
    items = []
    for item in data.get(field) or []:
        text = link(view, item[keys.REF], role=item.get(keys.ROLE))
        variant = item.get(keys.VARIANT)
        if variant and variant != graph_json.VARIANT_NOT_SPECIFIED:
            text += " " + link(view, variant)
        items.append(text)
    return items


def variant_label(view, model_ref, variant_ref):
    name = view.nodes[variant_ref]["label"]
    prefix = view.nodes[model_ref]["label"] + " "
    return name[len(prefix):] if name.startswith(prefix) else name


def model_row(view, data):
    variants = {}
    for item in data.get("models") or []:
        listed = variants.setdefault(item[keys.REF], [])
        variant = item.get(keys.VARIANT)
        if variant and variant != graph_json.VARIANT_NOT_SPECIFIED:
            listed.append(variant)
    parts = []
    for ref, listed in variants.items():
        text = link(view, ref)
        if listed:
            text += ' <span class="role">%s</span>' % ", ".join(
                link(view, variant, label=variant_label(view, ref, variant))
                for variant in listed)
        parts.append(text)
    return ", ".join(parts)


def nodes_of(view, node_type):
    return graph_json.nodes_of_type(view.nodes, node_type)


def by_reach(view):
    return lambda node: (-len(view.reached.get(node["id"], ())), node["label"])


def finding_count(view, node_id):
    total = len(view.reached.get(node_id, ()))
    return "%d finding%s" % (total, "" if total == 1 else "s")


def distinct_refs(data, field):
    refs = []
    for item in data.get(field) or []:
        if item[keys.REF] not in refs:
            refs.append(item[keys.REF])
    return refs


def finding_entry(view, node):
    data = node["data"]
    body = link(view, node["id"], label=data["title"]) + draft_mark(data)
    models = ", ".join(link(view, ref) for ref in distinct_refs(data, "models"))
    if models:
        body += '<span class="entry-note">%s</span>' % models
    return entry(escape(node["id"]), body)


def entity_entry(view, node):
    return entry("", link(view, node["id"]), finding_count(view, node["id"]))


def findings_using(view, node_id):
    usages = sorted(set(view.usage.get(node_id, [])), key=lambda u: (u.finding, u.role or ""))
    return [entry(escape(usage.finding),
                  link(view, usage.finding, label=view.nodes[usage.finding]["data"]["title"])
                  + role_marker(usage.role))
            for usage in usages]


def findings_through_sources(view, node_id):
    direct = {usage.finding for usage in view.usage.get(node_id, [])}
    return [entry(escape(fid), link(view, fid, label=view.nodes[fid]["data"]["title"]))
            for fid in sorted(view.reached.get(node_id, set()) - direct)]


def entity_meta(view, node):
    data = node["data"]
    parts = []
    for field in META_FIELDS:
        if data.get(field):
            value = escape(join_values(data[field]))
            parts.append('<span class="nowrap">%s</span>' % value if field == "date" else value)
    parts += [anchor(data[field], field)
              for field in (keys.ANCHOR, keys.ARTIFACT) if data.get(field)]
    return paragraph(" · ".join(parts), "meta") if parts else ""


def variant_names(node):
    return ", ".join('<span id="%s">%s</span>' % (site_paths.html_id(key), escape(variant["name"]))
                     for key, variant in (node["data"].get("variants") or {}).items())


def bridge_entries(view, node):
    entries = []
    for concept, others in graph_json.concept_bridges(view.graph, node["id"]):
        models = ", ".join(link(view, other) for other in others)
        entries.append(entry("", link(view, concept)
                             + '<span class="entry-note">also in %s</span>' % models))
    return entries


def navigation(view):
    items = ["<li>%s</li>" % anchor(site_paths.href(view.here, HOME), escape(PAGE_TITLE))]
    for node_type, title in SECTIONS:
        current = ' aria-current="page"' if view.here.startswith(
            graph_json.URL_SEGMENTS[node_type] + "/") else ""
        items.append("<li>%s</li>" % anchor(
            site_paths.href(view.here, site_paths.registry_page(node_type)),
            escape(title), current))
    return "<nav><ul>%s</ul></nav>" % "".join(items)


def footer(view):
    findings = nodes_of(view, graph_json.FINDING)
    return FOOTER % {
        "findings": len(findings),
        "verified": sum(1 for node in findings if not is_draft(node["data"])),
        "nodes": len(view.nodes),
        "shared": len(graph_json.shared_entities(view.reached)),
    }


def page(view, title, body, body_class=""):
    return "".join([
        '<!doctype html><html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        "<title>%s</title>" % escape(title),
        '<link rel="preconnect" href="https://fonts.googleapis.com">',
        '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>',
        '<link rel="stylesheet" href="%s">' % FONTS,
        '<link rel="stylesheet" href="%s">' % escape(site_paths.href(view.here, STYLESHEET)),
        "</head>",
        "<body%s>" % (' class="%s"' % body_class if body_class else ""),
        navigation(view),
        body,
        "<footer>%s</footer></body></html>" % footer(view),
    ])


def home_body(view):
    models = sorted(nodes_of(view, graph_json.MODEL), key=by_reach(view))
    findings = sorted(nodes_of(view, graph_json.FINDING), key=lambda node: node["id"])
    registries = [(node_type, title) for node_type, title in SECTIONS
                  if node_type != graph_json.FINDING]
    return "".join([
        "<header><h1>%s</h1>%s</header>" % (escape(PAGE_TITLE), paragraph(TAGLINE, "tagline")),
        paragraph(escape(LEDE), "lede"),
        heading("Models"),
        entry_list([entry("", link(view, node["id"]) + '<span class="entry-note">%s</span>'
                          % escape(join_values(node["data"].get("modality") or [])),
                          finding_count(view, node["id"])) for node in models]),
        heading("Findings"),
        entry_list([finding_entry(view, node) for node in findings]),
        heading("Registries"),
        entry_list([entry("", anchor(site_paths.href(view.here, site_paths.registry_page(node_type)),
                                     escape(title)),
                          "%d" % len(nodes_of(view, node_type)))
                    for node_type, title in registries]),
    ])


def registry_body(view, node_type, title):
    if node_type == graph_json.FINDING:
        nodes = sorted(nodes_of(view, node_type), key=lambda node: node["id"])
        items = [finding_entry(view, node) for node in nodes]
    else:
        items = [entity_entry(view, node)
                 for node in sorted(nodes_of(view, node_type), key=by_reach(view))]
    return "<header><h1>%s</h1></header>%s" % (escape(title), entry_list(items))


def finding_body(view, node):
    data = node["data"]
    rows = [(term, escape(data[field].strip()) if data.get(field) else "")
            for field, term in TEXT_ROWS]
    rows += [(term, model_row(view, data) if field == "models"
              else ", ".join(link_items(view, data, field)))
             for field, term in LINK_ROWS]
    rows.append(("Related findings", ", ".join(link(view, fid, label=fid)
                                               for fid in data.get("related_findings") or [])))
    rows.append(("Record", "%s, %s" % (escape(data["review_status"]),
                                       escape(data["extracted_by"]))))
    authors = finding_authors(view, data)
    return "".join([
        '<header><h1><span class="ident">%s%s</span>%s</h1></header>'
        % (escape(node["id"]), draft_mark(data), escape(data["title"])),
        paragraph(", ".join(link(view, ref) for ref in authors), "byline") if authors else "",
        paragraph(escape(data["description"].strip())),
        definition_list(rows),
    ])


def entity_body(view, node):
    data = node["data"]
    out = ["<header><h1>%s</h1></header>" % escape(node["label"]), entity_meta(view, node)]
    if data.get("description"):
        out.append(paragraph(escape(data["description"].strip())))

    rows = []
    if data.get("note") and data.get(keys.ANCHOR):
        rows.append(("Note", escape(data["note"].strip())))
    if variant_names(node):
        rows.append(("Variants", variant_names(node)))
    out.append(definition_list(rows))

    used_by = findings_using(view, node["id"])
    if used_by:
        out += [heading("Findings"), entry_list(used_by)]

    through_sources = findings_through_sources(view, node["id"])
    if through_sources:
        out += [heading("Findings through their sources"), entry_list(through_sources)]

    if node["type"] == graph_json.MODEL:
        bridges = bridge_entries(view, node)
        if bridges:
            out += [heading("Shared mechanisms"), entry_list(bridges)]
    return "".join(out)


def iter_pages(graph, stylesheet=None):
    stylesheet = stylesheet if stylesheet is not None else stylesheet_text()
    base = View(graph=graph,
                nodes=graph_json.nodes_by_id(graph),
                usage=graph_json.usage_by_entity(graph),
                reached=graph_json.findings_reaching(graph),
                here=HOME)
    yield STYLESHEET, stylesheet
    yield HOME, page(base, PAGE_TITLE, home_body(base), body_class="home")

    for node_type, title in SECTIONS:
        view = base._replace(here=site_paths.registry_page(node_type))
        yield view.here, page(view, "%s — %s" % (title, PAGE_TITLE),
                              registry_body(view, node_type, title))

    for node in base.nodes.values():
        if node["type"] == graph_json.VARIANT:
            continue
        view = base._replace(here=site_paths.page_of(node))
        body = (finding_body(view, node) if node["type"] == graph_json.FINDING
                else entity_body(view, node))
        yield view.here, page(view, "%s — %s" % (node["label"], PAGE_TITLE), body)


def render_site(graph, stylesheet=None):
    return dict(iter_pages(graph, stylesheet=stylesheet))


def main():
    shutil.rmtree(SITE, ignore_errors=True)
    pages = 0
    for path, document in iter_pages(load_graph(GRAPH)):
        target = SITE / path
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(document, encoding="utf-8")
        pages += 1
    print("wrote %d pages under site/, open site/%s" % (pages, HOME))


if __name__ == "__main__":
    main()
