import csv
import posixpath
import re
import tempfile
from pathlib import Path

import build
import export
from modelpedia import graph_io
from modelpedia import graph as graph_json
import render
from modelpedia import report
from modelpedia import site_paths
from tests.test_build import database


def graph_of(**changes):
    return build.build(database(**changes))


def audit_of(**changes):
    db = database(**changes)
    graph = build.build(db)
    return report.Audit(findings=db.findings, entities=db.entities, graph=graph,
                        reached=graph_json.findings_reaching(graph))


def nodes_of(**changes):
    return graph_json.nodes_by_id(graph_of(**changes))


def line_for(lines, key):
    matches = [line for line in lines if line.strip().startswith(key)]
    assert len(matches) == 1, (key, lines)
    return matches[0]


def test_header_counts_findings_nodes_and_edges():
    audit = audit_of()
    line = report.header(audit)[0]
    assert line == "format %d, 1 findings, %d nodes, %d edges" % (
        graph_json.FORMAT_VERSION, len(audit.graph["nodes"]), len(audit.graph["edges"]))


def test_record_status_counts_each_pair():
    assert "1" in line_for(report.record_status(audit_of()), "verified / manual")


def test_concept_use_counts_the_findings_that_tag_it():
    assert "1 finding," in line_for(report.concept_use(audit_of()), "concept:idea")


def test_concept_use_marks_a_concept_no_finding_tags():
    def orphan(entities):
        entities["concept:lonely"] = {"type": graph_json.CONCEPT, "name": "Lonely"}
    assert "0 findings  (unused)" in line_for(report.concept_use(audit_of(entities=orphan)),
                                              "concept:lonely")


def test_concept_use_counts_the_distinct_models_a_concept_reaches():
    def second_model(entities):
        entities["model:other"] = {"type": graph_json.MODEL, "name": "Other",
                                   "modality": ["image"], "variants": {}}

    def reach_it(findings):
        findings["XX-001"]["models"].append({"ref": "model:other", "variant": None})
    line = line_for(report.concept_use(audit_of(entities=second_model, findings=reach_it)),
                    "concept:idea")
    assert "1 finding, 2 models" in line


def test_concept_use_counts_two_variants_of_one_model_once():
    def add_variant(entities):
        entities["variant:thing-big"] = {"type": graph_json.VARIANT, "name": "Thing big",
                                         "parent": "model:thing"}
        entities["model:thing"]["variants"]["variant:thing-big"] = {"name": "Thing big"}

    def reach_it(findings):
        findings["XX-001"]["models"].append({"ref": "model:thing",
                                             "variant": "variant:thing-big"})
    line = line_for(report.concept_use(audit_of(entities=add_variant, findings=reach_it)),
                    "concept:idea")
    assert "1 finding, 1 model" in line


def test_shared_nodes_reports_none_shared_for_a_single_finding():
    lines = report.shared_nodes(audit_of())
    assert lines[-1].strip().startswith("0 of ")


def test_findings_without_datasets_says_none_when_every_finding_has_one():
    assert report.findings_without_datasets(audit_of())[-1] == "  none"


def test_findings_without_datasets_lists_the_finding_that_has_none():
    def drop(findings):
        findings["XX-001"]["datasets"] = []
    lines = report.findings_without_datasets(audit_of(findings=drop))
    assert "source does not state one" in line_for(lines, "XX-001")


def test_gaps_list_entities_that_should_have_an_anchor_but_do_not():
    lines = report.entities_without_anchors(audit_of())
    assert "nothing to link to" in line_for(lines, "rw:earlier")
    assert "nothing to link to" in line_for(lines, "source:the-paper")


def test_gaps_distinguish_an_entity_that_has_only_an_artifact():
    def artifact_only(entities):
        entities["rw:earlier"]["artifact"] = "https://example.org/code"
    assert "artifact only" in line_for(report.entities_without_anchors(
        audit_of(entities=artifact_only)), "rw:earlier")


def test_gaps_never_report_concepts_or_people_as_missing_an_anchor():
    text = "\n".join(report.entities_without_anchors(audit_of()))
    assert "concept:idea" not in text
    assert "person:ada-lovelace" not in text


def test_unused_registry_entries_say_none_when_everything_is_reached():
    assert report.entities_without_findings(audit_of())[-1] == "  none"


def test_unused_registry_entries_name_the_type_of_each_orphan():
    def orphan(entities):
        entities["method:unused"] = {"type": graph_json.METHOD, "name": "Unused"}
    assert "(method)" in line_for(
        report.entities_without_findings(audit_of(entities=orphan)), "method:unused")


def test_render_separates_every_section_with_a_blank_line():
    db = database()
    graph = build.build(db)
    lines = report.render(db.findings, db.entities, graph).split("\n")
    assert lines[0].startswith("format ")
    assert lines.count("") == len(report.SECTIONS)


def test_link_text_writes_a_bare_reference():
    assert export.link_text({"ref": "method:probe"}) == "method:probe"


def test_link_text_appends_the_role_in_brackets():
    assert export.link_text({"ref": "method:probe", "role": "primary"}) == "method:probe[primary]"


def test_link_text_appends_the_variant_in_parentheses():
    link = {"ref": "model:thing", "variant": "variant:thing-small"}
    assert export.link_text(link) == "model:thing(variant:thing-small)"


def test_link_text_omits_a_variant_recorded_as_not_specified():
    link = {"ref": "model:thing", "variant": graph_json.VARIANT_NOT_SPECIFIED}
    assert export.link_text(link) == "model:thing"


def test_cell_renders_a_missing_value_as_empty():
    assert export.cell(None) == ""


def test_cell_collapses_the_line_breaks_that_block_yaml_scalars_carry():
    assert export.cell("one\n  two\n\n  three") == "one two three"


def test_cell_joins_a_list_with_semicolons():
    assert export.cell([{"ref": "a:one"}, {"ref": "b:two"}]) == "a:one; b:two"


def test_cell_joins_the_keys_of_a_mapping():
    assert export.cell({"variant:a": {}, "variant:b": {}}) == "variant:a; variant:b"


def test_columns_follow_the_declared_order_not_the_yaml_order():
    nodes = [{"data": {"description": "d", "title": "t", "id": "XX-001"}}]
    assert export.columns_for(nodes) == ["id", "title", "description"]


def test_a_column_missing_from_the_declared_order_is_an_error_not_a_silent_append():
    nodes = [{"data": {"id": "XX-001", "invented_field": 1}}]
    try:
        export.columns_for(nodes)
    except KeyError as error:
        assert "invented_field" in str(error)
        return
    raise AssertionError("columns_for accepted an undeclared column")


def test_every_node_type_the_build_produces_has_a_table():
    produced = {node["type"] for node in graph_of()["nodes"]}
    assert produced <= set(export.TABLE_FILES)


def test_missing_required_types_reports_empty_required_tables():
    grouped = {graph_json.VARIANT: [{"type": graph_json.VARIANT}]}
    missing = export.missing_required_types(grouped)
    assert graph_json.FINDING in missing
    assert graph_json.MODEL in missing
    assert graph_json.VARIANT not in missing


def test_written_tables_round_trip_through_csv():
    graph = graph_of()
    findings = [node for node in graph["nodes"] if node["type"] == graph_json.FINDING]
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "findings.csv"
        rows, columns = export.write_table(findings, path)
        written = list(csv.DictReader(path.open(encoding="utf-8")))
    assert (rows, columns) == (1, len(written[0]))
    assert written[0]["id"] == "XX-001"
    assert written[0]["methods"] == "method:probe[primary]"


def test_written_edges_round_trip_through_csv():
    graph = graph_of()
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "edges.csv"
        rows, columns = export.write_edges(graph, path)
        written = list(csv.DictReader(path.open(encoding="utf-8")))
    assert rows == len(graph["edges"]) == len(written)
    assert columns == len(export.EDGE_COLUMNS)


def test_graph_loader_accepts_current_format_version():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "graph.json"
        path.write_text('{"format_version": %d, "nodes": [], "edges": []}'
                        % graph_json.FORMAT_VERSION, encoding="utf-8")
        loaded = graph_io.load_graph(path)
    assert loaded["nodes"] == []


def test_graph_loader_rejects_wrong_format_version():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "graph.json"
        path.write_text('{"format_version": 999, "nodes": [], "edges": []}', encoding="utf-8")
        try:
            graph_io.load_graph(path)
        except ValueError as error:
            assert "format_version" in str(error)
            return
    raise AssertionError("load_graph accepted an incompatible format version")


HREF = re.compile(r'href="([^"]+)"')


def view_of(**changes):
    graph = graph_of(**changes)
    return render.View(graph=graph,
                       nodes=graph_json.nodes_by_id(graph),
                       usage=graph_json.usage_by_entity(graph),
                       reached=graph_json.findings_reaching(graph),
                       here=render.HOME)


def at(view, path):
    return view._replace(here=path)


def internal_links(document):
    return [raw for raw in HREF.findall(document) if not raw.startswith("http")]


def broken_links(pages):
    broken = []
    for path, document in pages.items():
        for raw in internal_links(document):
            target, _, fragment = raw.partition("#")
            resolved = posixpath.normpath(posixpath.join(posixpath.dirname(path), target))
            if resolved not in pages:
                broken.append((path, raw, "no such page"))
            elif fragment and 'id="%s"' % fragment not in pages[resolved]:
                broken.append((path, raw, "no such anchor"))
    return broken


def test_escaping_neutralises_markup_in_the_data():
    assert render.escape("<b>&</b>") == "&lt;b&gt;&amp;&lt;/b&gt;"


def test_a_slug_drops_the_registry_prefix_but_a_finding_keeps_its_identifier():
    assert site_paths.slug_of("method:morans-i") == "morans-i"
    assert site_paths.slug_of("TM-001") == "TM-001"


def test_a_page_path_has_the_shape_the_api_will_serve():
    view = view_of()
    assert site_paths.page_of(view.nodes["XX-001"]) == "findings/XX-001/index.html"
    assert site_paths.page_of(view.nodes["method:probe"]) == "methods/probe/index.html"
    assert site_paths.registry_page(graph_json.MODEL) == "models/index.html"


def test_links_out_of_the_home_page_are_relative_to_the_root():
    view = view_of()
    assert site_paths.href(view.here, "models/thing/index.html") == "models/thing/index.html"


def test_links_between_two_deep_pages_climb_back_out():
    view = at(view_of(), "findings/XX-001/index.html")
    assert site_paths.href(view.here, "models/thing/index.html") == "../../models/thing/index.html"


def test_a_fragment_survives_the_relative_path_calculation():
    view = at(view_of(), "findings/XX-001/index.html")
    assert site_paths.href(view.here, "models/thing/index.html#a") == "../../models/thing/index.html#a"


def test_a_variant_has_no_page_and_points_at_an_anchor_on_its_model():
    view = view_of()
    assert site_paths.target_of("variant:thing-small", view.nodes) == (
        "models/thing/index.html#variant-thing-small")


def test_a_link_without_a_role_carries_no_role_marker():
    assert render.role_marker(None) == ""


def test_a_definition_list_drops_rows_with_no_value():
    assert render.definition_list([("Kept", "v"), ("Dropped", "")]) == (
        "<dl><dt>Kept</dt><dd>v</dd></dl>")


def test_a_definition_list_with_nothing_to_show_renders_nothing():
    assert render.definition_list([("Dropped", "")]) == ""


def test_a_model_link_carries_a_second_link_to_the_variant():
    view = view_of()
    row = render.model_row(view, view.nodes["XX-001"]["data"])
    assert row.count("<a href=") == 2
    assert "#variant-thing-small" in row


def test_a_variant_label_drops_the_model_name_it_repeats():
    view = view_of()
    assert render.variant_label(view, "model:thing", "variant:thing-small") == "small"


def test_a_model_named_once_per_variant_is_still_listed_once():
    def two_variants(entities):
        entities["model:thing"]["variants"]["variant:thing-big"] = {"name": "Thing big"}
        entities["variant:thing-big"] = {"type": graph_json.VARIANT, "name": "Thing big",
                                         "parent": "model:thing"}

    def both(findings):
        findings["XX-001"]["models"] = [
            {"ref": "model:thing", "variant": "variant:thing-small"},
            {"ref": "model:thing", "variant": "variant:thing-big"}]
    view = view_of(entities=two_variants, findings=both)
    row = render.model_row(view, view.nodes["XX-001"]["data"])
    assert row.count(">Thing<") == 1
    assert row.count("<a href=") == 3


def test_a_variant_recorded_as_not_specified_produces_no_link():
    def unspecified(findings):
        findings["XX-001"]["models"] = [
            {"ref": "model:thing", "variant": graph_json.VARIANT_NOT_SPECIFIED}]
    view = view_of(findings=unspecified)
    assert render.model_row(view, view.nodes["XX-001"]["data"]).count("<a href=") == 1


def test_a_verified_finding_carries_no_draft_label():
    view = view_of()
    assert render.DRAFT_LABEL not in render.finding_body(view, view.nodes["XX-001"])


def test_a_draft_is_labelled_in_its_heading():
    def draft(findings):
        findings["XX-001"]["review_status"] = "draft"
    view = view_of(findings=draft)
    body = render.finding_body(view, view.nodes["XX-001"])
    assert '<span class="draft">%s</span>' % render.DRAFT_LABEL in body


def test_a_draft_is_labelled_everywhere_it_is_listed():
    def draft(findings):
        findings["XX-001"]["review_status"] = "draft"
    pages = render.render_site(graph_of(findings=draft))
    for path in (render.HOME, "findings/index.html", "findings/XX-001/index.html"):
        assert render.DRAFT_LABEL in pages[path], path


def test_every_finding_states_its_record_status():
    view = view_of()
    body = render.finding_body(view, view.nodes["XX-001"])
    assert "<dt>Record</dt><dd>verified, manual</dd>" in body


def test_a_note_on_an_entity_with_an_anchor_qualifies_it_and_is_shown():
    def qualified(entities):
        entities["method:probe"]["note"] = "only the linear variant"
    view = view_of(entities=qualified)
    assert "only the linear variant" in render.entity_body(view, view.nodes["method:probe"])


def test_a_note_on_an_entity_without_an_anchor_explains_the_gap_and_is_hidden():
    def gap(entities):
        entities["method:probe"]["anchor"] = None
        entities["method:probe"]["note"] = "no publication exists"
    view = view_of(entities=gap)
    assert "no publication exists" not in render.entity_body(view, view.nodes["method:probe"])


def test_authors_are_collected_from_the_sources_without_repeating_anyone():
    def twice(findings):
        findings["XX-001"]["sources"] = [{"ref": "source:the-paper"},
                                         {"ref": "source:the-paper"}]
    view = view_of(findings=twice)
    assert render.finding_authors(view, view.nodes["XX-001"]["data"]) == ["person:ada-lovelace"]


def test_the_footer_counts_findings_and_verified_findings_separately():
    def draft(findings):
        findings["XX-001"]["review_status"] = "draft"
    assert render.footer(view_of(findings=draft)).startswith("1 findings, 0 verified.")


def test_the_navigation_marks_the_section_the_page_belongs_to():
    nav = render.navigation(at(view_of(), "methods/probe/index.html"))
    assert nav.count('aria-current="page"') == 1
    assert '<a href="../index.html" aria-current="page">Methods</a>' in nav


def test_the_navigation_marks_nothing_on_the_home_page():
    assert 'aria-current' not in render.navigation(view_of())


def test_a_shared_concept_bridges_two_models():
    def second_model(entities):
        entities["model:other"] = {"type": graph_json.MODEL, "name": "Other"}

    def second_finding(findings):
        findings["XX-002"] = dict(findings["XX-001"], id="XX-002",
                                  models=[{"ref": "model:other"}], related_findings=[])
    graph = graph_of(entities=second_model, findings=second_finding)
    assert graph_json.concept_bridges(graph, "model:thing") == [("concept:idea", ["model:other"])]


def test_a_model_page_names_the_models_it_shares_a_mechanism_with():
    def second_model(entities):
        entities["model:other"] = {"type": graph_json.MODEL, "name": "Other"}

    def second_finding(findings):
        findings["XX-002"] = dict(findings["XX-001"], id="XX-002",
                                  models=[{"ref": "model:other"}], related_findings=[])
    pages = render.render_site(graph_of(entities=second_model, findings=second_finding))
    page = pages["models/thing/index.html"]
    assert "Shared mechanisms" in page
    assert "also in" in page


def test_every_node_except_a_variant_gets_its_own_page():
    graph = graph_of()
    pages = render.render_site(graph)
    nodes = graph_json.nodes_by_id(graph)
    expected = {site_paths.page_of(node) for node in nodes.values()
                if node["type"] != graph_json.VARIANT}
    assert expected <= set(pages)
    assert "variants/thing-small/index.html" not in pages


def test_the_site_has_a_home_page_and_one_index_per_registry():
    pages = render.render_site(graph_of())
    assert render.HOME in pages
    for node_type, _ in render.SECTIONS:
        assert site_paths.registry_page(node_type) in pages


def test_every_internal_link_in_the_fixture_site_resolves():
    assert broken_links(render.render_site(graph_of())) == []


def test_every_internal_link_in_the_real_site_resolves():
    assert broken_links(render.render_site(build.build(build.load()))) == []


def test_the_stylesheet_is_written_once_and_linked_from_every_page():
    pages = render.render_site(graph_of())
    assert pages[render.STYLESHEET] == render.stylesheet_text()
    documents = [page for path, page in pages.items() if path.endswith(".html")]
    assert documents
    for document in documents:
        assert render.STYLESHEET in document
        assert "<style>" not in document


def test_pipeline_smoke_real_data_builds_site_and_csv_outputs():
    db = build.load()
    graph = build.build(db)
    pages = render.render_site(graph)
    grouped = export.nodes_by_type(graph)
    assert render.HOME in pages
    assert pages[render.STYLESHEET] == render.stylesheet_text()
    with tempfile.TemporaryDirectory() as directory:
        out = Path(directory)
        table_rows = 0
        for node_type in export.TABLE_FILES:
            nodes = grouped.get(node_type) or []
            if not nodes:
                continue
            rows, _ = export.write_table(nodes, out / export.TABLE_FILES[node_type])
            table_rows += rows
        edge_rows, _ = export.write_edges(graph, out / export.EDGE_FILE)
    assert table_rows == len(graph["nodes"])
    assert edge_rows == len(graph["edges"])
