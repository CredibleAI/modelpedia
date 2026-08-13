# Modelpedia

Structured, citable database of third-party findings about how specific machine learning models
behave. Built for explainability research.

One record is one **finding**: a claim about the behaviour of a specific model, made by someone
other than the model's authors, after the fact. Findings link to a shared set of entities - models,
concepts, methods, datasets, sources, people - so that claims about different models meet on the
mechanisms they describe.

## Quick start

Requires Python 3.10 or newer.

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

```bash
source .venv/bin/activate
```

Activate once per shell; every command below then works as written. `PyYAML` is all the build
needs — `openreview-py` is used only by `harvest.py`, the one script that talks to the network.
Without activation `harvest.py` will tell you so and print the interpreter to use.

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
python3 check.py path/to/candidate.yaml
```

```bash
python3 verify.py data/findings/TM-006.yaml path/to/source.pdf
```

`verify.py` is a read-only verification aid. It locates numeric claims, linked entity names,
stable arXiv/DOI/OpenReview identifiers and the page that shares most of the caveat's vocabulary.
Missing items are suspicions for a human reviewer, not automatic corrections, and the command
changes nothing on disk. It exits 1 when any check is blocking, or when the record had nothing to
check at all — an empty or near-empty finding must not report `0 blocking` and look like a pass.

```bash
python3 harvest.py doctor
```

This offline check needs no OpenReview account. It verifies the installed client methods and
`pypdfium2`. Once the account is active and the two credential variables are set, run:

```bash
python3 harvest.py preflight ICML.cc/2026/Conference
```

```bash
python3 run_tests.py
```

308 tests. PyYAML is the only dependency of the build; `harvest.py` additionally needs `openreview-py` and is the one place that talks to the network. `test_build.py` covers every branch of the validator plus a
case asserting that the real `data/` still validates; `test_outputs.py` covers the three consumers
of `out/graph.json`; `test_pipeline.py` covers the ingestion core; `test_extract.py` covers the
`extract.py` command line. The repository does not depend on pytest; install it separately if you
prefer it to the bundled runner.

`build.py` validates `data/`, writes `out/graph.json` and prints an audit of record status,
concept usage, shared nodes, findings whose source names no dataset, findings no concept covers,
missing anchors and unused registry entries. It exits non-zero and writes nothing if validation
fails, so run it before every commit. The work itself is in `modelpedia/`: `database.load()` reads
the YAML, `validate.errors()` returns the problems, `assemble.graph_from()` builds the artifact.

Validation also checks filename/id consistency for findings and variant-to-model consistency on
model links. Two kinds of duplicate identifier are rejected at load time: the same key in two
different registry files, and the same key twice inside one file. The second one matters more than
it sounds - YAML itself keeps only the last of two identical keys, so without the check a repeated
entry silently replaces the earlier definition and the count never moves.

`render.py` writes a static site into `site/`: a home page, one index per registry, and one page
per finding and per entity. `export.py` writes one CSV per node type plus `edges.csv` into
`out/csv/`. Both read `out/graph.json` and neither reads the YAML, so the build artifact is the
only contract between the data and its consumers.

`export.py` fails rather than writing a partial export if a core node type has no rows, on the
grounds that an empty table is far more likely to mean a broken build than an intended one.

Every artifact in this repository is replaced in one step or not at all. `build.py` writes
`out/graph.json` to `out/graph.json.part` and renames it into position. `render.py` and `export.py`
build their whole output in `site.part/` and `out/csv.part/` and swap only once it is complete.
So a corrupt `graph.json`, a crash half way through 146 pages, or a disk that fills up on the third
CSV all leave the last good output exactly where it was. Nothing survives from a build that no
longer produces it, and nothing half-written survives at all.

Open `site/index.html` by double-clicking it. The links are relative, so the site needs no server,
and the whole folder can be zipped and sent as one thing. The directory layout is the path scheme
the API will serve, so `site/findings/TM-003/` is the page for what will be `GET /findings/TM-003`.

## Layout

```
build.py         YAML -> validate -> assemble -> out/graph.json, then the audit
render.py        out/graph.json -> site/, one page per finding and per entity
export.py        out/graph.json -> out/csv/*.csv
check.py         a candidate finding -> schema errors plus link resolution
verify.py        an existing finding + PDF -> evidence locations and suspicions
harvest.py       OpenReview -> corpus/, resumable; metadata and screening before any download
run_tests.py     runs all five suites

modelpedia/      the library, imported and never run
  graph.py       node and edge type names, the NODE_TYPES table, how to query out/graph.json
  schema.py      the finding schema: link fields, field lists, vocabulary scopes, regexes
  record_keys.py shared string constants for keys inside records (ref, role, authors, ...)
  graph_io.py    load/dump out/graph.json with the format_version guard
  atomic.py      write-to-partial-then-rename; the single .part convention for files
  paths.py       every filesystem location in the repository
  console.py     console output primitives; the terminal twin of html_bits.py

  build/         data/*.yaml -> out/graph.json
    database.py  the only YAML reader; -> Database(vocabularies, entities, findings)
    validate.py  Database -> list of error strings; creates nothing
    assemble.py  Database -> the out/graph.json dict; validates nothing
    report.py    the console audit that build.py prints

  site/          out/graph.json -> HTML
    site_paths.py  URL/path and slug logic for the static site
    html_bits.py   low-level HTML templating helpers

  ingest/        papers -> candidate findings
    text.py        PDF -> normalised searchable text
    link.py        entity name -> hit / candidates / miss, against the registries
    screen.py      title/abstract -> relevance score and tier, plus RULES_VERSION
    manifest.py    corpus/manifest.jsonl: row validation, reading, tier selection
    openreview.py  everything that knows the OpenReview API; signals, never exits
    report.py      console reports for extract.py; returns strings, prints nothing
    verification.py deterministic evidence checks used by verify.py

tests/           test_build.py (data -> graph), test_outputs.py (graph -> outputs),
                 test_pipeline.py (ingestion), test_extract.py (extract.py CLI),
                 test_verification.py (verification head)
data/            vocabularies.yaml, registries/*.yaml, findings/*.yaml
assets/style.css stylesheet for the static site, loaded at render time
```

The eight runnable scripts sit at the root and match the documented commands; everything imported
lives in `modelpedia/`, and nothing in `modelpedia/` imports a runnable script. `build.py` is 29
lines: it loads, validates, assembles, writes and prints, and every one of those verbs is a call
into the library. `harvest.py` follows the same shape since 2026-08-12 — the manifest store, the
OpenReview client and the atomic write all moved into the library, leaving orchestration behind.

Inside `modelpedia/`, depth is a permission. Top-level modules may be imported from anywhere;
`build/`, `site/` and `ingest/` are imported only by their own area's entry points and by their own
siblings. No subpackage imports another subpackage.

Three tables are defined once and imported everywhere else. `modelpedia/graph.py` holds
`NODE_TYPES` — one row per kind of node, carrying its label, its YAML registry file, its site
directory, its CSV file, whether it is expected to have an anchor and whether the export requires
it to have rows. `modelpedia/schema.py` holds the finding schema. `modelpedia/paths.py` holds every
filesystem location, and is the only file that derives the repository root from its own location.
Adding a registry is one row and one YAML file.

`out/` and `site/` are build artifacts and are not tracked. Delete them and rebuild at any time.

`render.py` can stream page generation through `iter_pages()` to avoid keeping the whole site in
memory when scaling to larger datasets.

## Data model

The YAML files are the only source of truth; everything else is derived. The central rule is that
**identity lives on the node and role lives on the edge**: `dataset:terramesh` is the same entity
whether one paper trained on it and another evaluated on it, and `[train]` or `[eval]` sits on the
link rather than in the registry.


## Current state

62 findings across 5 registries; 388 nodes and 756 edges. Nine were written by hand
(`extracted_by: manual-extraction`); the other 53 were admitted from ICLR 2025 by automatic
extraction and have not been read against their sources.

Every finding carries one record field, `extracted_by` — `manual-extraction` or
`automatic-extraction` — and records how the entry was produced, nothing more. An earlier
`review_status` field (`draft`/`verified`) was removed: reading the sources found errors in 5 of
the 7 records that already carried `verified`, so the label recorded that someone had checked, not
that the check was good. There is no default requirement that a human check a record before it
counts. Consumers of the site or the export must not read a record's presence in the database as
evidence that anyone has verified it against the source.

Gaps in the data are stated rather than guessed. Where a source names no dataset or prints no URL,
the field is empty and `build.py` lists it in the audit.

