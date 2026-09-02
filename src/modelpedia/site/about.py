from modelpedia import graph as graph_json
from modelpedia.site import charts
from modelpedia.site.html_bits import escape, heading, paragraph

PATH = "about/index.html"
SEGMENT = "about"
LABEL = "About"
TITLE = "About"

SITE_URL = "https://credibleai.github.io/modelpedia"
SOURCE_URL = "https://github.com/CredibleAI/modelpedia"

LEDE = ("Modelpedia is a catalog of findings about machine learning models: third-party claims "
        "about how a specific model behaves, made after the fact by someone other than the "
        "model's authors.")

WHAT = (
    "A paper proposing a new method usually also reports something about the models it ran on. "
    "That observation is a finding. It is not the paper's contribution, it carries no name, and "
    "it is indexed nowhere, so it is lost the moment the field moves on. Modelpedia gives each "
    "one an identifier, a source, and links to the entities it concerns.",
    "Every finding names a model and its source. Where the source states them it also names the "
    "datasets its evidence rests on, the methods behind it, and a concept, which is the "
    "mechanism the finding is about. Those five are shared entities with pages of their own, and "
    "they are what makes two findings meet. Related work is kept inline as a title and a link: "
    "an outside paper cited once joins nothing and would not earn a page.",
)

FIELDS = (
    ("Title", "The claim in one sentence."),
    ("Description", "What the finding is and how it was produced."),
    ("Evidence type", "How strongly the evidence supports a causal claim. Observational "
                      "describes a pattern measured against nothing; correlational compares a "
                      "quantity across models, layers or conditions; interventional changes "
                      "something in the model and records the effect."),
    ("Key metric", "The measurement that carried the claim, quoted from the source."),
    ("Caveat", "What the finding does not support, where the source qualifies its own result."),
    ("Extraction", "Whether the record was written by hand or by a language model."),
)

STAGES = (
    ("Stage 1", "Paper ranking",
     "Accepted papers are scored from their abstracts and reviews by a fixed keyword vocabulary, "
     "with no model involved. The score sets the reading order and discards nothing."),
    ("Stage 2", "Extraction",
     "Each paper goes to a language model under a fixed response structure. The model must quote "
     "the source for every entity it names, and it never invents an identifier: it picks from a "
     "closed list or says there is none. That rule turns hallucination into a bounded choice."),
    ("Stage 3", "Linking",
     "Names no registry holds yet go back to the model one at a time, which decides whether each "
     "earns a permanent entry. Citations are checked against the source and anchors come from the "
     "source text, not from the model."),
)

NOT = (
    "Modelpedia reports what the authors of a paper claim. It does not check whether the method "
    "was applied correctly or whether the result replicates. It makes claims findable and "
    "traceable to their source, which is a smaller thing than verifying them.",
    "Almost every record here was extracted automatically and has not been read against its "
    "paper by a person. Each finding says which. There is deliberately no review status, because "
    "a label saying somebody checked is not the same as a check that was any good. Steps that "
    "resolve to a named thing, such as models, datasets and printed figures, come out well; "
    "steps that need a judgement call, such as concepts and methods, come out worse. Read a "
    "record's presence here as a pointer to its source, never as evidence that anyone verified "
    "it.",
)

DIAGRAM_WIDTH = 860
DIAGRAM_HEIGHT = 470

STAGE_BOXES = (
    (145, "Stage 1", "Paper ranking", "Is this paper worth reading?"),
    (394, "Stage 2", "Extraction", "What is the finding about?"),
    (643, "Stage 3", "Linking", "How does it relate to what is here?"),
)

BOX_WIDTH = 205
BOX_HEIGHT = 90
BOX_TOP = 40

REGISTRY_PILLS = ("Findings", "Models", "Datasets", "Methods", "Concepts", "Related work")

PILL_WIDTH = 121
PILL_STEP = 130
PILL_LEFT = 52
PILL_TOP = 212
PILL_HEIGHT = 42


def text(x, y, body, css_class, anchor=None, extra=""):
    middle = ' text-anchor="%s"' % anchor if anchor else ""
    return '<text x="%s" y="%s" class="%s"%s%s>%s</text>' % (x, y, css_class, middle, extra,
                                                             escape(body))


def box(x, y, width, height, css_class, radius=2):
    return '<rect x="%s" y="%s" width="%s" height="%s" rx="%s" class="%s"/>' % (
        x, y, width, height, radius, css_class)


def arrow_h(x1, x2, y):
    head = 7 if x2 > x1 else -7
    return ('<line x1="%s" y1="%s" x2="%s" y2="%s" class="dg-line"/>'
            '<path d="M%s %s L%s %s L%s %s Z" class="dg-head"/>'
            % (x1, y, x2 - head, y, x2, y, x2 - head, y - 4.5, x2 - head, y + 4.5))


def arrow_v(x, y1, y2):
    head = 7 if y2 > y1 else -7
    return ('<line x1="%s" y1="%s" x2="%s" y2="%s" class="dg-line"/>'
            '<path d="M%s %s L%s %s L%s %s Z" class="dg-head"/>'
            % (x, y1, x, y2 - head, x, y2, x - 4.5, y2 - head, x + 4.5, y2 - head))


def stage_box(x, tag, name, question):
    return "".join([
        box(x, BOX_TOP, BOX_WIDTH, BOX_HEIGHT, "dg-box"),
        box(x, BOX_TOP, BOX_WIDTH, 5, "dg-cap"),
        text(x + 14, BOX_TOP + 22, tag, "dg-tag"),
        text(x + 14, BOX_TOP + 42, name, "dg-title"),
        text(x + 14, BOX_TOP + 63, question, "dg-note"),
    ])


def registry_row():
    parts = [box(34, 178, 812, 92, "dg-panel", radius=8),
             text(52, 200, "Modelpedia", "dg-panel-title")]
    for index, name in enumerate(REGISTRY_PILLS):
        left = PILL_LEFT + index * PILL_STEP
        parts.append(box(left, PILL_TOP, PILL_WIDTH, PILL_HEIGHT, "dg-pill", radius=4))
        parts.append(text(left + PILL_WIDTH / 2, PILL_TOP + 26, name, "dg-pill-text",
                          anchor="middle"))
    return "".join(parts)


def finding_card():
    parts = [box(64, 312, 300, 132, "dg-stack", radius=4),
             box(58, 318, 300, 132, "dg-stack", radius=4),
             box(52, 324, 300, 132, "dg-box", radius=4),
             text(70, 348, "FINDING", "dg-tag"),
             text(70, 372, "Few embedding dimensions drive", "dg-card-title"),
             text(70, 388, "the modality gap in CLIP", "dg-card-title"),
             text(70, 412, "The paper analyses the embedding", "dg-note"),
             text(70, 427, "space of off-the-shelf CLIP models", "dg-note")]
    return "".join(parts)


def mini_chart(counts):
    parts = [box(444, 324, 402, 132, "dg-box", radius=4),
             text(645, 344, "Evidence type", "dg-mini-title", anchor="middle")]
    largest = max((count for _, count in counts), default=0) or 1
    base = 432
    for index, (name, count) in enumerate(counts):
        centre = 528 + index * 116
        height = max(2, round(64.0 * count / largest, 1))
        parts.append('<rect x="%s" y="%s" width="44" height="%s" class="dg-mini ev-%s"/>'
                     % (centre - 22, base - height, height, name))
        parts.append(text(centre, base + 14, name, "dg-mini-tick", anchor="middle"))
    return "".join(parts)


def diagram(view):
    parts = ['<svg class="diagram" viewBox="0 0 %d %d" role="img" '
             'aria-label="How a finding reaches the catalog: papers are ranked, findings are '
             'extracted, entities are linked into the registries, and the result is served as '
             'finding pages and as material for meta-analysis." '
             'xmlns="http://www.w3.org/2000/svg">' % (DIAGRAM_WIDTH, DIAGRAM_HEIGHT)]

    parts.append(text(8, 46, "A", "dg-key"))
    parts.append(text(8, 82, "New accepted", "dg-tag"))
    parts.append(text(8, 95, "proceedings", "dg-tag"))
    parts.append(arrow_h(88, 141, 86))
    for x, tag, name, question in STAGE_BOXES:
        parts.append(stage_box(x, tag, name, question))
    parts.append(arrow_h(354, 390, 86))
    parts.append(arrow_h(603, 639, 86))

    parts.append(text(8, 196, "B", "dg-key"))
    parts.append(registry_row())
    parts.append(arrow_v(486, 174, 134))
    parts.append(arrow_v(506, 134, 174))
    parts.append(text(520, 158, "match or create", "dg-tag"))
    parts.append(arrow_v(745, 134, 174))
    parts.append(text(735, 158, "write findings", "dg-tag", anchor="end"))

    parts.append(arrow_v(202, 274, 308))
    parts.append(arrow_v(645, 274, 308))
    parts.append(text(8, 330, "C", "dg-key"))
    parts.append(finding_card())
    parts.append(text(400, 330, "D", "dg-key"))
    parts.append(mini_chart(charts.evidence_counts(view)))

    parts.append("</svg>")
    return '<div class="diagram-frame">%s</div>' % "".join(parts)


def stage_rows():
    return "".join('<li><span class="stage-tag">%s</span>'
                   '<span class="stage-body"><strong>%s</strong> %s</span></li>'
                   % (escape(tag), escape(name), escape(body))
                   for tag, name, body in STAGES)


def field_rows():
    return "".join("<dt>%s</dt><dd>%s</dd>" % (escape(term), escape(body))
                   for term, body in FIELDS)


def plural(count, one, many):
    return "%d %s" % (count, one if count == 1 else many)


def counts(view):
    findings = len(graph_json.nodes_of_type(view.nodes, graph_json.FINDING))
    sources = len(graph_json.nodes_of_type(view.nodes, graph_json.SOURCE))
    shared = len(graph_json.shared_entities(view.reached))
    return ("%s drawn from %s, almost all of them papers accepted to ICLR 2024 and ICLR 2025. "
            "%s reached by more than one finding, and those joins are the point. Every number "
            "here is counted from the data when the site is built."
            % (plural(findings, "finding", "findings"),
               plural(sources, "source", "sources"),
               plural(shared, "entity is", "entities are")))


def body(view, link, external):
    out = [
        "<header><h1>%s</h1></header>" % escape(TITLE),
        paragraph(escape(LEDE), "lede"),
    ]
    out += [paragraph(escape(part)) for part in WHAT]

    out.append(heading("What a finding records"))
    out.append(paragraph(escape(
        "Six fields a finding carries in its own right. Everything else is a link.")))
    out.append("<dl>%s</dl>" % field_rows())

    out.append(heading("How a finding gets here"))
    out.append(paragraph(escape(
        "The stages that need a model are separated by deterministic steps that check its "
        "output.")))
    out.append(diagram(view))
    out.append('<ol class="stages">%s</ol>' % stage_rows())

    out.append(heading("What the catalog holds"))
    out.append(paragraph(escape(counts(view))))
    out.append(charts.all_charts(view, link))

    out.append(heading("What this is not"))
    out += [paragraph(escape(part)) for part in NOT]

    out.append(heading("Source"))
    out.append(paragraph(
        "The data lives as YAML in %s and everything else, this site included, is rebuilt from "
        "it. Findings are addressed by identifier, so %s is a stable place to point at."
        % (external(SOURCE_URL, "the repository"),
           external(SITE_URL + "/findings/FX-003/index.html", "a finding page"))))
    return "".join(out)
