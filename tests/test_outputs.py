import csv
import io
import posixpath
import re
import tempfile
from contextlib import redirect_stdout
from pathlib import Path

from tests.helpers import sample_db
from modelpedia.commands import export
from modelpedia.build import assemble
from modelpedia.build import database
from modelpedia import graph_io
from modelpedia import graph as graph_json
from modelpedia import paths
from modelpedia.commands import render
from modelpedia.site import parts
from modelpedia.build import report
from modelpedia.site import about
from modelpedia.site import charts
from modelpedia.site import site_paths


def graph_of(**changes):
    return assemble.graph_from(sample_db(**changes))


def audit_of(**changes):
    db = sample_db(**changes)
    graph = assemble.graph_from(db)
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


def test_record_status_counts_each_status():
    assert "1" in line_for(report.record_status(audit_of()), "manual-extraction")


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


def test_findings_without_concepts_says_none_when_every_finding_has_one():
    assert report.findings_without_concepts(audit_of())[-1] == "  none"


def test_findings_without_concepts_lists_an_uncovered_finding():
    def drop(findings):
        findings["XX-001"]["concepts"] = []
    lines = report.findings_without_concepts(audit_of(findings=drop))
    assert "no existing concept fits" in line_for(lines, "XX-001")


def test_gaps_list_entities_that_should_have_an_anchor_but_do_not():
    lines = report.entities_without_anchors(audit_of())
    assert "nothing to link to" in line_for(lines, "source:the-paper")


def test_gaps_distinguish_an_entity_that_has_only_an_artifact():
    def artifact_only(entities):
        entities["source:the-paper"]["artifact"] = "https://example.org/code"
    assert "artifact only" in line_for(report.entities_without_anchors(
        audit_of(entities=artifact_only)), "source:the-paper")


def test_gaps_never_report_concepts_as_missing_an_anchor():
    text = "\n".join(report.entities_without_anchors(audit_of()))
    assert "concept:idea" not in text


def test_unused_registry_entries_say_none_when_everything_is_reached():
    assert report.entities_without_findings(audit_of())[-1] == "  none"


def test_unused_registry_entries_name_the_type_of_each_orphan():
    def orphan(entities):
        entities["method:unused"] = {"type": graph_json.METHOD, "name": "Unused"}
    assert "(method)" in line_for(
        report.entities_without_findings(audit_of(entities=orphan)), "method:unused")


def test_render_separates_every_section_with_a_blank_line():
    db = sample_db()
    graph = assemble.graph_from(db)
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
    assert produced <= set(graph_json.NODE_TYPE_BY_NAME)


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


def test_the_graph_is_replaced_in_one_step_and_leaves_no_partial_file():
    with tempfile.TemporaryDirectory() as directory:
        path = Path(directory) / "graph.json"
        path.write_text("a stale graph from an earlier build", encoding="utf-8")
        graph_io.dump_graph(graph_of(), path)
        assert [entry.name for entry in Path(directory).iterdir()] == ["graph.json"]
        assert graph_io.load_graph(path)["nodes"]


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
    return parts.View(graph=graph,
                       nodes=graph_json.nodes_by_id(graph),
                       usage=graph_json.usage_by_entity(graph),
                       reached=graph_json.findings_reaching(graph),
                       here=parts.HOME)


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
    assert parts.role_marker(None) == ""


def test_a_definition_list_drops_rows_with_no_value():
    assert render.definition_list([("Kept", "v"), ("Dropped", "")]) == (
        "<dl><dt>Kept</dt><dd>v</dd></dl>")


def test_a_definition_list_with_nothing_to_show_renders_nothing():
    assert render.definition_list([("Dropped", "")]) == ""


def test_a_model_link_carries_a_second_link_to_the_variant():
    view = view_of()
    row = parts.model_row(view, view.nodes["XX-001"]["data"])
    assert row.count("<a href=") == 2
    assert "#variant-thing-small" in row


def test_a_variant_label_drops_the_model_name_it_repeats():
    view = view_of()
    assert parts.variant_label(view, "model:thing", "variant:thing-small") == "small"


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
    row = parts.model_row(view, view.nodes["XX-001"]["data"])
    assert row.count(">Thing<") == 1
    assert row.count("<a href=") == 3


def test_a_variant_recorded_as_not_specified_produces_no_link():
    def unspecified(findings):
        findings["XX-001"]["models"] = [
            {"ref": "model:thing", "variant": graph_json.VARIANT_NOT_SPECIFIED}]
    view = view_of(findings=unspecified)
    assert parts.model_row(view, view.nodes["XX-001"]["data"]).count("<a href=") == 1


def test_every_finding_states_its_extraction_method_only():
    view = view_of()
    body = render.finding_body(view, view.nodes["XX-001"])
    assert ('<dt>Extraction</dt><dd><span class="badge manual-extraction">'
            'manual-extraction</span></dd>') in body
    assert "<dt>Record</dt>" not in body


def test_evidence_renders_as_a_badge_carrying_its_value_as_a_class():
    view = view_of()
    body = render.finding_body(view, view.nodes["XX-001"])
    assert '<dt>Evidence</dt><dd><span class="badge observational">observational</span></dd>' in body


def test_the_source_is_labelled_on_its_own_line_under_the_authors():
    view = view_of()
    body = render.finding_body(view, view.nodes["XX-001"])
    authors = body.index('<p class="byline">')
    source = body.index('<p class="source">')
    assert authors < source < body.index("<p>")
    line = body[source:body.index("</p>", source)]
    assert '<span class="source-label">Source</span>' in line
    assert 'href="sources/the-paper/index.html"' in line
    assert "Ada Lovelace" not in line
    assert "<dt>Source</dt>" not in body


def test_a_finding_whose_source_carries_no_authors_still_labels_its_source():
    def anonymous(entities):
        entities["source:the-paper"]["authors"] = []
    view = view_of(entities=anonymous)
    body = render.finding_body(view, view.nodes["XX-001"])
    assert '<p class="byline">' not in body
    assert body.count('<p class="source">') == 1
    assert '<span class="source-label">Source</span>' in body


def test_outside_links_open_in_a_new_tab_and_internal_links_do_not():
    view = view_of()
    body = render.finding_body(view, view.nodes["XX-001"])
    assert ('<a href="https://example.org/earlier" target="_blank" '
            'rel="external noopener">Earlier work</a>') in body
    assert 'index.html" target' not in body


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
    assert parts.finding_authors(view, view.nodes["XX-001"]["data"]) == ["Ada Lovelace"]


def test_the_navigation_marks_the_section_the_page_belongs_to():
    nav = parts.navigation(at(view_of(), "methods/probe/index.html"))
    assert nav.count('aria-current="page"') == 1
    assert '<a href="../index.html" aria-current="page">Methods</a>' in nav


def test_the_navigation_marks_nothing_on_the_home_page():
    assert 'aria-current' not in parts.navigation(view_of())


def test_every_page_marks_the_site_as_a_prototype_beside_its_name():
    pages = render.render_site(graph_of())
    documents = [doc for path, doc in pages.items() if path != parts.STYLESHEET]
    assert documents
    for document in documents:
        assert parts.STATUS_LABEL in document
        assert document.index(parts.PAGE_TITLE) < document.index('class="wip"')


def test_the_home_page_marks_the_prototype_on_its_heading_too():
    view = view_of()
    body = render.home_body(view)
    heading = body[body.index("<h1>"):body.index("</h1>")]
    assert 'class="wip"' in heading
    assert parts.STATUS_LABEL in heading


def test_the_navigation_carries_the_theme_toggle():
    nav = parts.navigation(view_of())
    assert 'class="theme-toggle"' in nav
    assert nav.count("<button") == 1


def test_every_page_ships_the_theme_init_and_toggle_scripts():
    pages = render.render_site(graph_of())
    documents = [doc for path, doc in pages.items() if path != parts.STYLESHEET]
    assert documents
    for document in documents:
        assert "localStorage.getItem('theme')" in document
        assert 'class="theme-toggle"' in document
        assert document.index(parts.THEME_INIT) < document.index("<title>")


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


def test_only_node_types_with_a_url_segment_get_their_own_page():
    graph = graph_of()
    pages = render.render_site(graph)
    nodes = graph_json.nodes_by_id(graph)
    expected = {site_paths.page_of(node) for node in nodes.values()
                if graph_json.NODE_TYPE_BY_NAME[node["type"]].url_segment}
    assert expected <= set(pages)
    assert "variants/thing-small/index.html" not in pages
    assert not any(page.startswith("None/") for page in pages)


def test_the_site_has_a_home_page_and_one_index_per_registry():
    pages = render.render_site(graph_of())
    assert parts.HOME in pages
    for node_type in graph_json.PAGE_TYPES:
        assert site_paths.registry_page(node_type.name) in pages


def test_every_internal_link_in_the_fixture_site_resolves():
    assert broken_links(render.render_site(graph_of())) == []


def test_a_graph_that_fails_to_load_leaves_the_previous_site_untouched():
    original_graph, original_site = paths.GRAPH, paths.SITE
    try:
        with tempfile.TemporaryDirectory() as directory:
            paths.GRAPH = Path(directory) / "graph.json"
            paths.SITE = Path(directory) / "site"
            paths.SITE.mkdir()
            kept = paths.SITE / "index.html"
            kept.write_text("the page from the last good build", encoding="utf-8")
            paths.GRAPH.write_text('{"format_version": 999, "nodes": [], "edges": []}',
                                   encoding="utf-8")
            try:
                render.main()
            except ValueError:
                pass
            else:
                raise AssertionError("render.main accepted an incompatible graph")
            assert kept.read_text(encoding="utf-8") == "the page from the last good build"
    finally:
        paths.GRAPH, paths.SITE = original_graph, original_site


def test_the_export_clears_a_table_this_build_does_not_produce():
    original_graph, original_csv = paths.GRAPH, paths.CSV
    try:
        with tempfile.TemporaryDirectory() as directory:
            paths.GRAPH = Path(directory) / "graph.json"
            paths.CSV = Path(directory) / "csv"
            graph_io.dump_graph(graph_of(), paths.GRAPH)
            paths.CSV.mkdir()
            stale = paths.CSV / "retired_registry.csv"
            stale.write_text("id\n", encoding="utf-8")
            stream = io.StringIO()
            with redirect_stdout(stream):
                code = export.main()
            assert code == 0
            assert not stale.exists()
            assert (paths.CSV / "findings.csv").exists()
    finally:
        paths.GRAPH, paths.CSV = original_graph, original_csv


def test_the_export_keeps_the_previous_tables_when_the_graph_fails_to_load():
    original_graph, original_csv = paths.GRAPH, paths.CSV
    try:
        with tempfile.TemporaryDirectory() as directory:
            paths.GRAPH = Path(directory) / "graph.json"
            paths.CSV = Path(directory) / "csv"
            paths.CSV.mkdir()
            kept = paths.CSV / "findings.csv"
            kept.write_text("id\nXX-001\n", encoding="utf-8")
            paths.GRAPH.write_text('{"format_version": 999, "nodes": [], "edges": []}',
                                   encoding="utf-8")
            try:
                export.main()
            except ValueError:
                pass
            else:
                raise AssertionError("export.main accepted an incompatible graph")
            assert kept.read_text(encoding="utf-8") == "id\nXX-001\n"
    finally:
        paths.GRAPH, paths.CSV = original_graph, original_csv


def test_a_render_that_crashes_part_way_leaves_the_previous_site_intact():
    original_graph, original_site, real = paths.GRAPH, paths.SITE, render.iter_pages

    def crashing(graph, stylesheet=None):
        for number, item in enumerate(real(graph, stylesheet=stylesheet)):
            if number >= 3:
                raise RuntimeError("the disk filled up")
            yield item

    try:
        with tempfile.TemporaryDirectory() as directory:
            paths.GRAPH = Path(directory) / "graph.json"
            paths.SITE = Path(directory) / "site"
            graph_io.dump_graph(graph_of(), paths.GRAPH)
            paths.SITE.mkdir()
            kept = paths.SITE / "index.html"
            kept.write_text("the page from the last good build", encoding="utf-8")
            render.iter_pages = crashing
            try:
                render.main()
            except RuntimeError:
                pass
            else:
                raise AssertionError("the injected failure did not reach render.main")
            assert [entry.name for entry in paths.SITE.iterdir()] == ["index.html"]
            assert kept.read_text(encoding="utf-8") == "the page from the last good build"
    finally:
        render.iter_pages = real
        paths.GRAPH, paths.SITE = original_graph, original_site


def test_an_export_that_crashes_part_way_leaves_the_previous_tables_intact():
    original_graph, original_csv, real = paths.GRAPH, paths.CSV, export.write_table
    calls = []

    def crashing(nodes, path):
        calls.append(path.name)
        if len(calls) >= 2:
            raise RuntimeError("the disk filled up")
        return real(nodes, path)

    try:
        with tempfile.TemporaryDirectory() as directory:
            paths.GRAPH = Path(directory) / "graph.json"
            paths.CSV = Path(directory) / "csv"
            graph_io.dump_graph(graph_of(), paths.GRAPH)
            paths.CSV.mkdir()
            for name in ("findings.csv", "models.csv", "edges.csv"):
                (paths.CSV / name).write_text("the last complete export\n", encoding="utf-8")
            export.write_table = crashing
            try:
                export.main()
            except RuntimeError:
                pass
            else:
                raise AssertionError("the injected failure did not reach export.main")
            assert sorted(entry.name for entry in paths.CSV.iterdir()) == [
                "edges.csv", "findings.csv", "models.csv"]
            assert (paths.CSV / "findings.csv").read_text(
                encoding="utf-8") == "the last complete export\n"
    finally:
        export.write_table = real
        paths.GRAPH, paths.CSV = original_graph, original_csv


def test_a_successful_render_and_export_leave_no_staging_directory():
    original_graph, original_site, original_csv = paths.GRAPH, paths.SITE, paths.CSV
    try:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            paths.GRAPH = root / "graph.json"
            paths.SITE = root / "site"
            paths.CSV = root / "csv"
            graph_io.dump_graph(graph_of(), paths.GRAPH)
            stream = io.StringIO()
            with redirect_stdout(stream):
                render.main()
                assert export.main() == 0
            assert list(root.glob("*" + paths.PARTIAL)) == []
            assert (paths.SITE / parts.HOME).is_file()
            assert (paths.CSV / "findings.csv").is_file()
    finally:
        paths.GRAPH, paths.SITE, paths.CSV = original_graph, original_site, original_csv


def test_every_internal_link_in_the_real_site_resolves():
    assert broken_links(render.render_site(assemble.graph_from(database.load()))) == []


def test_the_stylesheet_is_written_once_and_linked_from_every_page():
    pages = render.render_site(graph_of())
    assert pages[parts.STYLESHEET] == parts.stylesheet_text()
    documents = [page for path, page in pages.items() if path.endswith(".html")]
    assert documents
    for document in documents:
        assert parts.STYLESHEET in document
        assert "<style>" not in document


def test_pipeline_smoke_real_data_builds_site_and_csv_outputs():
    db = database.load()
    graph = assemble.graph_from(db)
    pages = render.render_site(graph)
    grouped = export.nodes_by_type(graph)
    assert parts.HOME in pages
    assert pages[parts.STYLESHEET] == parts.stylesheet_text()
    with tempfile.TemporaryDirectory() as directory:
        out = Path(directory)
        table_rows = 0
        for node_type in graph_json.NODE_TYPES:
            nodes = grouped.get(node_type.name) or []
            if not nodes:
                continue
            rows, _ = export.write_table(nodes, out / node_type.table_file)
            table_rows += rows
        edge_rows, _ = export.write_edges(graph, out / export.EDGE_FILE)
    assert table_rows == len(graph["nodes"])
    assert edge_rows == len(graph["edges"])


def about_view():
    return at(view_of(), about.PATH)


def test_the_about_page_is_built_and_linked_from_every_page():
    pages = render.render_site(graph_of())
    assert about.PATH in pages
    documents = [page for path, page in pages.items() if path.endswith(".html")]
    assert documents
    for document in documents:
        assert ">About</a>" in document


def test_the_navigation_marks_about_only_when_the_reader_is_there():
    assert 'aria-current="page">About</a>' in parts.navigation(about_view())
    assert "aria-current" not in parts.navigation(view_of())


def test_the_about_charts_count_the_graph_rather_than_a_fixed_table():
    assert charts.evidence_counts(about_view()) == [
        ("observational", 1), ("correlational", 0), ("interventional", 0)]


def test_a_second_finding_moves_the_evidence_chart():
    def second(findings):
        findings["XX-002"] = dict(findings["XX-001"], id="XX-002",
                                  evidence_type="interventional", related_findings=[])
    view = at(view_of(findings=second), about.PATH)
    assert charts.evidence_counts(view) == [
        ("observational", 1), ("correlational", 0), ("interventional", 1)]


def test_every_evidence_bar_carries_its_value_as_a_class():
    body = charts.evidence_chart(about_view())
    for value in charts.EVIDENCE_ORDER:
        assert 'class="bar-fill ev-%s"' % value in body


def test_a_bar_is_scaled_against_the_largest_value_in_its_own_chart():
    body = charts.chart("Title", "Note", [("a", 5, ""), ("b", 1, "")])
    assert "--share:100.0%" in body
    assert "--share:20.0%" in body


def test_a_chart_with_nothing_counted_does_not_divide_by_zero():
    assert "--share:0.0%" in charts.chart("Title", "Note", [("a", 0, "")])


def test_a_ranked_chart_says_how_much_of_the_registry_it_leaves_out():
    def orphan(entities):
        entities["method:unused"] = {"type": graph_json.METHOD, "name": "Unused"}
    view = at(view_of(entities=orphan), about.PATH)
    body = charts.ranked_chart(view, parts.link, graph_json.METHOD, "Methods", "Note.")
    assert "All 1 reached by at least one finding, out of 2 in the registry." in body
    assert "Unused" not in body


def test_a_ranked_chart_that_shows_everything_claims_no_top_slice():
    body = charts.ranked_chart(about_view(), parts.link, graph_json.CONCEPT, "Concepts", "Note.")
    assert "All 1 in the registry." in body


def test_a_chart_label_drops_the_synonyms_the_registry_name_carries():
    assert charts.short_name("GPT-4 / ChatGPT4 / GPT-4 Code Interpreter") == "GPT-4"
    assert charts.short_name("CLIP") == "CLIP"


def test_the_dataset_distribution_folds_its_tail_into_one_bucket():
    rows, longest = charts.datasets_per_finding(about_view())
    assert len(rows) == charts.DATASET_BUCKETS + 1
    assert rows[-1][0] == "%d or more" % charts.DATASET_BUCKETS
    assert (rows[1], longest) == (("1", 1), 1)


def test_the_diagram_is_inline_svg_carrying_no_stylesheet_and_no_baked_colour():
    body = about.diagram(about_view())
    assert body.count("<svg") == 1
    assert "<style" not in body
    assert "fill=" not in body
    assert 'class="dg-cap"' in body


def test_the_diagram_chart_is_drawn_from_the_data_not_from_fixed_heights():
    body = about.mini_chart([("observational", 1), ("correlational", 0), ("interventional", 4)])
    assert 'height="16.0" class="dg-mini ev-observational"' in body
    assert 'height="2" class="dg-mini ev-correlational"' in body
    assert 'height="64.0" class="dg-mini ev-interventional"' in body


def test_the_about_page_counts_the_base_it_is_built_from():
    assert about.counts(about_view()).startswith("1 finding drawn from 1 source")


def test_about_is_the_first_section_after_the_site_name():
    labels = re.findall(r"<li>(?:.*?)>([^<]+)</a>", parts.navigation(view_of()))
    assert labels[0] == parts.PAGE_TITLE
    assert labels[1] == about.LABEL


def test_no_page_carries_the_counter_footer_any_more():
    pages = render.render_site(graph_of())
    documents = [page for path, page in pages.items() if path.endswith(".html")]
    assert documents
    for document in documents:
        assert "<footer" not in document


def test_the_catalog_charts_are_tabbed_with_exactly_one_open():
    body = charts.all_charts(about_view(), parts.link)
    assert body.count('class="tab-input"') == body.count('class="tab" for=')
    assert body.count(" checked>") == 1
    assert body.count('class="chart panel"') == body.count('class="tab" for=')


def test_a_tab_label_points_at_the_input_that_opens_it():
    body = charts.tabbed([("One", "<figure></figure>"), ("Two", "<figure></figure>")], "g")
    assert '<input class="tab-input" type="radio" name="g" id="g-0" checked>' in body
    assert '<label class="tab" for="g-1">Two</label>' in body


def test_more_charts_than_stylesheet_slots_fall_back_to_a_plain_stack():
    items = [("T%d" % index, "<figure>%d</figure>" % index)
             for index in range(charts.TAB_SLOTS + 1)]
    body = charts.tabbed(items, "g")
    assert "tab-input" not in body
    assert body.count("<figure>") == len(items)
