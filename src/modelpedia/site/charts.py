from modelpedia import graph as graph_json
from modelpedia import record_keys as keys
from modelpedia.site.html_bits import escape

TOP = 15

EVIDENCE_ORDER = ("observational", "correlational", "interventional")

DATASET_BUCKETS = 7

TAB_SLOTS = 6

TAB_GROUP = "catalog"


def short_name(label):
    return label.split(" / ")[0].strip() or label


def findings_of(view):
    return graph_json.nodes_of_type(view.nodes, graph_json.FINDING)


def reach_of(view, node_id):
    return len(view.reached.get(node_id, ()))


def ranked(view, node_type, limit=TOP):
    nodes = graph_json.nodes_of_type(view.nodes, node_type)
    counted = [(reach_of(view, node["id"]), node["id"]) for node in nodes]
    counted = [pair for pair in counted if pair[0]]
    counted.sort(key=lambda pair: (-pair[0], view.nodes[pair[1]]["label"]))
    return counted[:limit], len(nodes), len(counted)


def evidence_counts(view):
    counts = {}
    for node in findings_of(view):
        value = node["data"].get("evidence_type")
        if value:
            counts[value] = counts.get(value, 0) + 1
    return [(value, counts.get(value, 0)) for value in EVIDENCE_ORDER]


def datasets_used(node):
    return len({item[keys.REF] for item in node["data"].get("datasets") or []})


def datasets_per_finding(view):
    counts = {}
    longest = 0
    for node in findings_of(view):
        used = datasets_used(node)
        longest = max(longest, used)
        counts[min(used, DATASET_BUCKETS)] = counts.get(min(used, DATASET_BUCKETS), 0) + 1
    rows = [(str(used), counts.get(used, 0)) for used in range(DATASET_BUCKETS)]
    rows.append(("%d or more" % DATASET_BUCKETS, counts.get(DATASET_BUCKETS, 0)))
    return rows, longest


def share_of(value, largest):
    return 0.0 if not largest else round(100.0 * value / largest, 2)


def bar(label, value, largest, fill_class=""):
    return ('<li><span class="bar-label">%s</span>'
            '<span class="bar-track"><span class="bar-fill%s" style="--share:%s%%"></span></span>'
            '<span class="bar-value">%d</span></li>'
            % (label, fill_class, share_of(value, largest), value))


def chart(title, note, rows):
    largest = max((value for _, value, _ in rows), default=0)
    bars = "".join(bar(label, value, largest, fill_class) for label, value, fill_class in rows)
    return ('<figure class="chart panel"><figcaption>%s<span class="chart-note">%s</span>'
            '</figcaption><ol class="bars">%s</ol></figure>'
            % (escape(title), escape(note), bars))


def tabbed(items, group):
    if not items or len(items) > TAB_SLOTS:
        return "".join(figure for _, figure in items)
    inputs = "".join('<input class="tab-input" type="radio" name="%s" id="%s-%d"%s>'
                     % (escape(group), escape(group), index, " checked" if not index else "")
                     for index, _ in enumerate(items))
    strip = "".join('<label class="tab" for="%s-%d">%s</label>'
                    % (escape(group), index, escape(label))
                    for index, (label, _) in enumerate(items))
    panels = "".join(figure for _, figure in items)
    return ('<div class="tabs">%s<div class="tab-strip">%s</div>'
            '<div class="panels">%s</div></div>' % (inputs, strip, panels))


def evidence_chart(view):
    rows = [(escape(value), count, " ev-" + value) for value, count in evidence_counts(view)]
    return chart("Evidence type",
                 "One value per finding, ordered by the strength of the causal claim it "
                 "supports.", rows)


def ranked_chart(view, link, node_type, title, note):
    counted, total, reaching = ranked(view, node_type)
    rows = [(link(view, node_id, label=short_name(view.nodes[node_id]["label"])), count, "")
            for count, node_id in counted]
    if len(rows) < reaching:
        scope = ("Showing the %d most used of the %d reached by at least one finding, out of %d "
                 "in the registry." % (len(rows), reaching, total))
    elif reaching < total:
        scope = ("All %d reached by at least one finding, out of %d in the registry."
                 % (reaching, total))
    else:
        scope = "All %d in the registry." % total
    return chart(title, "%s %s" % (note, scope), rows)


def datasets_chart(view):
    rows, longest = datasets_per_finding(view)
    return chart("Datasets per finding",
                 "How many distinct datasets a finding names. The longest tail reaches %d; "
                 "none means the source does not state one." % longest,
                 [(escape(label), count, "") for label, count in rows])


def all_charts(view, link):
    return tabbed([
        ("Evidence", evidence_chart(view)),
        ("Concepts", ranked_chart(view, link, graph_json.CONCEPT, "Concepts",
                                  "The mechanism a finding is about, and the axis on which "
                                  "findings about different models meet.")),
        ("Models", ranked_chart(view, link, graph_json.MODEL, "Models",
                                "Findings naming each model.")),
        ("Datasets", ranked_chart(view, link, graph_json.DATASET, "Datasets",
                                  "Findings whose evidence rests on each dataset.")),
        ("Per finding", datasets_chart(view)),
    ], TAB_GROUP)
