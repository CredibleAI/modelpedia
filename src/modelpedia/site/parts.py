from typing import NamedTuple

from modelpedia import graph as graph_json
from modelpedia import paths
from modelpedia import record_keys as keys
from modelpedia.site import about
from modelpedia.site import site_paths
from modelpedia.site.html_bits import anchor, entry, escape, paragraph


HOME = site_paths.INDEX

STYLESHEET = "style.css"

EXTERNAL = ' target="_blank" rel="external noopener"'

THEME_INIT = ("<script>(function(){try{var t=localStorage.getItem('theme');"
              "if(t==='light'||t==='dark')"
              "document.documentElement.setAttribute('data-theme',t);}catch(e){}})();</script>")

THEME_SCRIPT = ("<script>(function(){var b=document.querySelector('.theme-toggle');if(!b)return;"
                "var m=window.matchMedia('(prefers-color-scheme: dark)');"
                "function cur(){var t=document.documentElement.getAttribute('data-theme');"
                "return t||(m.matches?'dark':'light');}"
                "function sync(){b.setAttribute('aria-pressed',String(cur()==='dark'));}"
                "sync();b.addEventListener('click',function(){"
                "var n=cur()==='dark'?'light':'dark';"
                "document.documentElement.setAttribute('data-theme',n);"
                "try{localStorage.setItem('theme',n);}catch(e){}sync();});"
                "m.addEventListener('change',sync);})();</script>")

THEME_TOGGLE = ('<button type="button" class="theme-toggle"'
                ' aria-label="Switch between light and dark theme"'
                ' title="Switch light / dark theme">'
                '<span class="on-light">Light</span>'
                '<span class="on-dark">Dark</span></button>')

META_FIELDS = ("developer", "date", "modality", "domain", "task", "venue")

PAGE_TITLE = "Modelpedia"

STATUS_LABEL = "Work in progress"

STATUS_TITLE = "Prototype: the data, the schema and the site are all still changing"

STATUS_BADGE = '<span class="wip" title="%s">%s</span>' % (escape(STATUS_TITLE),
                                                           escape(STATUS_LABEL))

class View(NamedTuple):
    graph: dict
    nodes: dict
    usage: dict
    reached: dict
    here: str

def join_values(value):
    if isinstance(value, list):
        return ", ".join(str(item) for item in value)
    return str(value)

def role_marker(role):
    return ' <span class="role">[%s]</span>' % escape(role) if role else ""

def badge(value):
    return '<span class="badge %s">%s</span>' % (escape(value), escape(value))

def has_page(node_type):
    return bool(graph_json.NODE_TYPE_BY_NAME[node_type].url_segment) \
        or node_type == graph_json.VARIANT

def link(view, node_id, label=None, role=None):
    node = view.nodes[node_id]
    text = escape(label or node["label"])
    if has_page(node["type"]):
        body = anchor(site_paths.href(view.here, site_paths.target_of(node_id, view.nodes)), text)
    else:
        target = (node.get("data") or {}).get(keys.ANCHOR)
        body = anchor(target, text, EXTERNAL) if target else text
    return body + role_marker(role)

def finding_authors(view, data):
    authors = []
    for source in data.get("sources") or []:
        for author in view.nodes[source[keys.REF]]["data"].get(keys.AUTHORS) or []:
            if author not in authors:
                authors.append(author)
    return authors

def outside_link(item):
    text = escape(str(item.get(keys.NAME) or ""))
    target = item.get(keys.ANCHOR)
    return anchor(target, text, EXTERNAL) if target else text

def external_link(url, text):
    return anchor(url, escape(text), EXTERNAL)

def byline(view, data):
    out = []
    authors = ", ".join(escape(name) for name in finding_authors(view, data))
    if authors:
        out.append(paragraph(authors, "byline"))
    sources = ", ".join(link_items(view, data, "sources"))
    if sources:
        out.append(paragraph('<span class="source-label">Source</span>%s' % sources, "source"))
    return "".join(out)

def link_items(view, data, field):
    items = []
    for item in data.get(field) or []:
        if keys.REF not in item:
            items.append(outside_link(item) + role_marker(item.get(keys.ROLE)))
            continue
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
    body = link(view, node["id"], label=data["title"])
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

def entity_meta(view, node):
    data = node["data"]
    parts = []
    for field in META_FIELDS:
        if data.get(field):
            value = escape(join_values(data[field]))
            parts.append('<span class="nowrap">%s</span>' % value if field == "date" else value)
    parts += [anchor(data[field], field, EXTERNAL)
              for field in (keys.ANCHOR, keys.ARTIFACT) if data.get(field)]
    return paragraph(" · ".join(parts), "meta") if parts else ""

def variant_names(node):
    return ", ".join('<span id="%s">%s</span>' % (site_paths.html_id(key), escape(variant["name"]))
                     for key, variant in (node["data"].get(keys.VARIANTS) or {}).items())

def bridge_entries(view, node):
    entries = []
    for concept, others in graph_json.concept_bridges(view.graph, node["id"]):
        models = ", ".join(link(view, other) for other in others)
        entries.append(entry("", link(view, concept)
                             + '<span class="entry-note">also in %s</span>' % models))
    return entries

def navigation(view):
    items = ["<li>%s%s</li>" % (anchor(site_paths.href(view.here, HOME), escape(PAGE_TITLE)),
                               STATUS_BADGE)]
    here = ' aria-current="page"' if view.here.startswith(about.SEGMENT + "/") else ""
    items.append("<li>%s</li>" % anchor(site_paths.href(view.here, about.PATH),
                                        escape(about.LABEL), here))
    for node_type in graph_json.PAGE_TYPES:
        current = ' aria-current="page"' if view.here.startswith(
            node_type.url_segment + "/") else ""
        items.append("<li>%s</li>" % anchor(
            site_paths.href(view.here, site_paths.registry_page(node_type.name)),
            escape(node_type.label), current))
    return "<nav><ul>%s</ul>%s</nav>" % ("".join(items), THEME_TOGGLE)

def page(view, title, body, body_class=""):
    return "".join([
        '<!doctype html><html lang="en"><head><meta charset="utf-8">',
        '<meta name="viewport" content="width=device-width, initial-scale=1">',
        THEME_INIT,
        "<title>%s</title>" % escape(title),
        '<link rel="stylesheet" href="%s">' % escape(site_paths.href(view.here, STYLESHEET)),
        "</head>",
        "<body%s>" % (' class="%s"' % body_class if body_class else ""),
        navigation(view),
        body,
        THEME_SCRIPT,
        "</body></html>",
    ])

def stylesheet_text():
    return (paths.ASSETS / STYLESHEET).read_text(encoding="utf-8")
