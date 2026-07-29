import html
from pathlib import Path

import graph as graph_json

ROOT = Path(__file__).parent
SITE = ROOT / "site"
GRAPH = ROOT / "out" / "graph.json"

SECTIONS = [
    ("model", "Models"),
    ("concept", "Concepts"),
    ("method", "Methods"),
    ("dataset", "Datasets"),
    ("source", "Sources"),
    ("rw", "Related work"),
]

LINK_ROWS = [
    ("models", "Model"),
    ("concepts", "Concepts"),
    ("sources", "Source"),
    ("datasets", "Datasets"),
    ("methods", "Methods"),
    ("related_work", "Related work"),
]

META_FIELDS = ("developer", "date", "domain", "venue")

AUTHORS = "authors"

PAGE_TITLE = "Modelpedia"
TAGLINE = "Findings about machine learning models"

FOOTER = "%d nodes, %d shared by more than one finding."

FONTS = ("https://fonts.googleapis.com/css2?"
         "family=Lexend+Deca:wght@300;400;500&family=Roboto+Mono:wght@400;500&display=swap")

STYLE = """
:root {
  --ink: #001f33;
  --quiet: #5c7180;
  --rule: #a3a3a3;
  --faint: #e4e8ea;
  --bg: #fff;
  --sans: "Lexend Deca", ui-sans-serif, system-ui, "Helvetica Neue", Arial, sans-serif;
  --mono: "Roboto Mono", ui-monospace, SFMono-Regular, Menlo, Consolas, monospace;
}
* { box-sizing: border-box; }
body { margin: 0 auto; padding: 4rem 1.5rem 5rem; max-width: 50rem; background: var(--bg);
  color: var(--ink); font: 300 1rem/1.7 var(--sans); -webkit-font-smoothing: antialiased; }
a { color: inherit; text-decoration: none; border-bottom: 1px solid var(--rule); }
a:hover { border-bottom-color: var(--ink); }
a:focus-visible { outline: 2px solid var(--ink); outline-offset: 3px; border-bottom-color: transparent; }
header { padding-bottom: 1.4rem; margin-bottom: 1.6rem; border-bottom: 1px solid var(--ink); }
h1 { margin: 0; font-size: 2.6rem; font-weight: 400; letter-spacing: -.035em; line-height: 1.05; }
.tagline { margin: .7rem 0 0; font-family: var(--mono); font-size: .7rem; font-weight: 400;
  text-transform: uppercase; letter-spacing: .16em; color: var(--quiet); }
nav { font-family: var(--mono); font-size: .7rem; font-weight: 400; text-transform: uppercase;
  letter-spacing: .14em; }
nav ul { list-style: none; margin: 0; padding: 0; display: flex; flex-wrap: wrap; gap: 1.4rem; }
nav a { border-bottom: 1px solid transparent; padding-bottom: 2px; }
nav a:hover { border-bottom-color: var(--ink); }
h2 { margin: 4.5rem 0 0; padding-bottom: .5rem; border-bottom: 1px solid var(--ink);
  font-family: var(--mono); font-size: .7rem; font-weight: 500; text-transform: uppercase;
  letter-spacing: .18em; }
article { padding: 1.8rem 0; border-bottom: 1px solid var(--faint); }
article:last-of-type { border-bottom: none; }
h3 { margin: 0 0 .5rem; font-size: 1.3rem; font-weight: 400; letter-spacing: -.02em;
  line-height: 1.25; }
article.entity h3 { font-size: 1.05rem; font-weight: 500; letter-spacing: -.01em; }
p { margin: 0 0 .6rem; }
p:last-child { margin-bottom: 0; }
.ident { display: block; margin-bottom: .35rem; font-family: var(--mono); font-size: .68rem;
  font-weight: 500; letter-spacing: .16em; color: var(--quiet); }
.meta { font-family: var(--mono); font-size: .74rem; font-weight: 400; color: var(--quiet);
  margin-bottom: .6rem; }
.meta a { border-bottom-color: var(--faint); }
.nowrap { white-space: nowrap; }
.byline { font-family: var(--mono); font-size: .72rem; font-weight: 400; color: var(--quiet);
  margin-bottom: .75rem; }
.role { font-family: var(--mono); font-size: .72em; font-weight: 400; color: var(--quiet); }
.mono { font-family: var(--mono); font-size: .82em; font-weight: 400; }
dl { display: grid; grid-template-columns: 8.5rem 1fr; gap: .5rem 1.5rem; margin: 1.1rem 0 0; }
dt { font-family: var(--mono); font-size: .64rem; font-weight: 500; text-transform: uppercase;
  letter-spacing: .14em; color: var(--quiet); padding-top: .34rem; }
dd { margin: 0; }
dt, dd, h3 { min-width: 0; overflow-wrap: anywhere; }
footer { margin-top: 4.5rem; padding-top: 1.2rem; border-top: 1px solid var(--ink);
  font-family: var(--mono); font-size: .7rem; font-weight: 400; letter-spacing: .06em;
  color: var(--quiet); }
@media (max-width: 36rem) {
  body { padding: 2.5rem 1.2rem 4rem; }
  h1 { font-size: 2rem; }
  dl { grid-template-columns: 1fr; gap: .15rem 0; }
  dt { padding-top: .7rem; }
}
"""


def escape(value):
    return html.escape(str(value), quote=True)


def html_id(node_id):
    return node_id.replace(":", "-")


def join_values(value):
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value)


def role_marker(role):
    return ' <span class="role">[%s]</span>' % escape(role) if role else ""


def node_link(nodes, node_id, label=None, role=None):
    text = escape(label or nodes[node_id]["label"])
    return '<a href="#%s">%s</a>%s' % (html_id(node_id), text, role_marker(role))


def definition_list(pairs):
    body = "".join("<dt>%s</dt><dd>%s</dd>" % (escape(term), value)
                   for term, value in pairs if value)
    return "<dl>%s</dl>" % body if body else ""


def paragraph(text, css_class=None):
    opening = '<p class="%s">' % css_class if css_class else "<p>"
    return "%s%s</p>" % (opening, text)


def author_names(nodes, data):
    return [nodes[author["ref"]]["label"] for author in data.get(AUTHORS) or []]


def finding_authors(nodes, data):
    names = []
    for source in data.get("sources") or []:
        for name in author_names(nodes, nodes[source["ref"]]["data"]):
            if name not in names:
                names.append(name)
    return names


def link_items(nodes, data, field):
    items = []
    for link in data.get(field) or []:
        text = node_link(nodes, link["ref"], role=link.get("role"))
        variant = link.get("variant")
        if variant and variant != graph_json.VARIANT_NOT_SPECIFIED:
            text += " " + node_link(nodes, variant)
        items.append(text)
    return items


def render_finding(nodes, node):
    data = node["data"]
    pairs = [("Evidence", escape(data["evidence_type"]) if data.get("evidence_type") else ""),
             ("Key metric", escape(data["key_metric"].strip()) if data.get("key_metric") else ""),
             ("Caveat", escape(data["caveat"].strip()) if data.get("caveat") else "")]
    pairs += [(term, ", ".join(link_items(nodes, data, field))) for field, term in LINK_ROWS]
    authors = finding_authors(nodes, data)
    return "".join([
        '<article id="%s">' % html_id(node["id"]),
        '<h3><span class="ident">%s</span>%s</h3>'
        % (escape(node["id"]), escape(data["title"])),
        paragraph(escape(", ".join(authors)), "byline") if authors else "",
        paragraph(escape(data["description"].strip())),
        definition_list(pairs),
        "</article>",
    ])


def entity_meta(node):
    data = node["data"]
    meta = []
    for field in META_FIELDS:
        if data.get(field):
            value = escape(join_values(data[field]))
            meta.append('<span class="nowrap">%s</span>' % value if field == "date" else value)
    meta += ['<a href="%s">%s</a>' % (escape(data[field]), field)
             for field in ("anchor", "artifact") if data.get(field)]
    return meta


def entity_rows(nodes, node, usages):
    data = node["data"]
    pairs = []
    if data.get("note") and data.get("anchor"):
        pairs.append(("Note", escape(data["note"].strip())))
    variants = data.get("variants") or {}
    if variants:
        pairs.append(("Variants", ", ".join(
            '<span id="%s">%s</span>' % (html_id(key), escape(variant["name"]))
            for key, variant in variants.items())))
    if usages:
        pairs.append(("Used by", ", ".join(
            '<span class="mono">%s</span>%s'
            % (node_link(nodes, usage.finding, label=usage.finding), role_marker(usage.role))
            for usage in sorted(set(usages), key=lambda u: (u.finding, u.role or "")))))
    return pairs


def render_entity(nodes, node, usages):
    data = node["data"]
    out = ['<article class="entity" id="%s">' % html_id(node["id"]),
           "<h3>%s</h3>" % escape(node["label"])]
    meta = entity_meta(node)
    if meta:
        out.append(paragraph(" · ".join(meta), "meta"))
    authors = author_names(nodes, data)
    if authors:
        out.append(paragraph(escape(", ".join(authors)), "meta"))
    if data.get("description"):
        out.append(paragraph(escape(data["description"].strip())))
    out.append(definition_list(entity_rows(nodes, node, usages)))
    out.append("</article>")
    return "".join(out)


def navigation():
    items = ['<li><a href="#findings">Findings</a></li>']
    items += ['<li><a href="#%s">%s</a></li>' % (node_type, escape(title))
              for node_type, title in SECTIONS]
    return "<nav><ul>%s</ul></nav>" % "".join(items)


def entities_of_type(nodes, usage, node_type):
    group = [node for node in nodes.values() if node["type"] == node_type]
    return sorted(group, key=lambda node: (-graph_json.finding_count(usage.get(node["id"], [])),
                                           node["label"]))


def render_page(nodes, usage, reached):
    findings = sorted((node for node in nodes.values() if node["type"] == graph_json.FINDING),
                      key=lambda node: node["id"])

    out = ['<!doctype html><html lang="en"><head><meta charset="utf-8">',
           '<meta name="viewport" content="width=device-width, initial-scale=1">',
           '<title>%s</title>' % PAGE_TITLE,
           '<link rel="preconnect" href="https://fonts.googleapis.com">',
           '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>',
           '<link rel="stylesheet" href="%s">' % FONTS,
           "<style>%s</style></head><body>" % STYLE,
           "<header><h1>%s</h1>%s</header>" % (PAGE_TITLE, paragraph(TAGLINE, "tagline")),
           navigation(),
           '<h2 id="findings">Findings</h2>']

    out += [render_finding(nodes, node) for node in findings]

    for node_type, title in SECTIONS:
        out.append('<h2 id="%s">%s</h2>' % (node_type, escape(title)))
        out += [render_entity(nodes, node, usage.get(node["id"], []))
                for node in entities_of_type(nodes, usage, node_type)]

    out.append("<footer>%s</footer></body></html>"
               % (FOOTER % (len(nodes), len(graph_json.shared_entities(reached)))))
    return "".join(out)


def main():
    graph = graph_json.load(GRAPH)
    nodes = graph_json.nodes_by_id(graph)
    usage = graph_json.usage_by_entity(graph)
    reached = graph_json.findings_reaching(graph)
    SITE.mkdir(exist_ok=True)
    (SITE / "modelpedia.html").write_text(
        render_page(nodes, usage, reached), encoding="utf-8")
    print("wrote site/modelpedia.html")


if __name__ == "__main__":
    main()
