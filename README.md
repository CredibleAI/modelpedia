# Modelpedia

Structured, citable database of third-party findings about how specific machine learning models
behave. Built for explainability research.

One record is one **finding**: a claim about the behaviour of a specific model, made by someone
other than the model's authors, after the fact. Findings link to a shared set of entities - models,
concepts, methods, datasets, sources, people - so that claims about different models meet on the
mechanisms they describe.

## Quick start

Requires Python 3.10 or newer. PyYAML is the only dependency.

```bash
pip install pyyaml
```

```bash
python3 build.py
```

```bash
python3 render.py
```

```bash
python3 export.py
```

```bash
python3 run_tests.py
```

99 tests, no dependency beyond PyYAML. `test_build.py` covers every branch of the validator plus a
case asserting that the real `data/` still validates; `test_outputs.py` covers the three consumers
of `out/graph.json`. `pytest` picks both files up if you prefer it.

`build.py` validates `data/`, writes `out/graph.json` and prints an audit of record status,
concept usage, shared nodes, findings whose source names no dataset, missing anchors and unused
registry entries. It exits non-zero and writes nothing if validation fails, so run it before every
commit.

Validation also checks identifier collisions, filename/id consistency for findings, and variant-to-
model consistency on model links.

`render.py` writes a static site into `site/`: a home page, one index per registry, and one page
per finding and per entity. `export.py` writes one CSV per node type plus `edges.csv` into
`out/csv/`. Both read `out/graph.json` and neither reads the YAML, so the build artifact is the
only contract between the data and its consumers.

`export.py` fails rather than writing a partial export if a core node type has no rows, on the
grounds that an empty table is far more likely to mean a broken build than an intended one.

Open `site/index.html` by double-clicking it. The links are relative, so the site needs no server,
and the whole folder can be zipped and sent as one thing. The directory layout is the path scheme
the API will serve, so `site/findings/TM-003/` is the page for what will be `GET /findings/TM-003`.

## Layout

```
build.py         the schema declaration; YAML -> validate -> out/graph.json
render.py        out/graph.json -> site/, one page per finding and per entity
export.py        out/graph.json -> out/csv/*.csv
run_tests.py     runs both suites

modelpedia/      the library, imported and never run
  graph.py       node and edge type names, and how to query out/graph.json
  record_keys.py shared string constants for link keys (ref, role, variant, ...)
  graph_io.py    loads out/graph.json and checks format_version
  site_paths.py  URL/path and slug logic for the static site
  html_bits.py   low-level HTML templating helpers
  report.py      the console audit that build.py prints

tests/           test_build.py (data -> graph), test_outputs.py (graph -> report, HTML, CSV)
data/            vocabularies.yaml, registries/*.yaml, findings/*.yaml
assets/style.css stylesheet for the static site, loaded at render time
```

The four runnable scripts sit at the root and match the four commands above; everything imported
lives in `modelpedia/`. Node and edge type names are defined once, in `modelpedia/graph.py`, and
imported everywhere else.

`out/` and `site/` are build artifacts and are not tracked. Delete them and rebuild at any time.

`render.py` can stream page generation through `iter_pages()` to avoid keeping the whole site in
memory when scaling to larger datasets.

## Data model

The YAML files are the only source of truth; everything else is derived. The central rule is that
**identity lives on the node and role lives on the edge**: `dataset:terramesh` is the same entity
whether one paper trained on it and another evaluated on it, and `[train]` or `[eval]` sits on the
link rather than in the registry.


## Current state

20 findings across 7 registries; 152 nodes and 277 edges.

Every finding carries two record fields. `review_status` is `verified` for the 9 that a human has
confirmed against the full text of the source, and `draft` for the 11 that have not been promoted
yet. `extracted_by` is `manual` or `automatic-extraction` and records how the entry was produced.
They are separate on purpose: an entry pulled out automatically and then checked is stronger than
one typed by hand and never read back. **The site labels every draft and never hides one**, and
the footer counts both numbers.

Gaps in the data are stated rather than guessed. Where a source names no dataset or prints no URL,
the field is empty and `build.py` lists it in the audit.


## Context

Built during an internship at the University of Warsaw. Mentor: Przemysław Biecek.

Theoretical foundation: *The Case for Model Science: Verify, Explore, Steer, Refine*
([arXiv 2606.01189](https://arxiv.org/abs/2606.01189), extended version
[arXiv 2508.20040](https://arxiv.org/abs/2508.20040)).
