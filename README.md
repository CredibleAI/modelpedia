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

`build.py` validates `data/`, writes `out/graph.json` and prints a report of concept usage, shared
nodes, missing anchors and unused registry entries. It exits non-zero and writes nothing if
validation fails, so run it before every commit.

`render.py` writes `site/modelpedia.html` and `export.py` writes one CSV per node type plus
`edges.csv` into `out/csv/`. Both read `out/graph.json` and neither reads the YAML, so the build
artifact is the only contract between the data and its consumers.

## Layout

```
data/*.yaml    source of truth
graph.py       the shape of out/graph.json and how to query it
build.py       YAML -> validate -> out/graph.json, plus the console report
render.py      out/graph.json -> site/modelpedia.html
export.py      out/graph.json -> out/csv/*.csv
```

`out/` and `site/` are build artifacts and are not tracked. Delete them and rebuild at any time.

## Data model

The YAML files are the only source of truth; everything else is derived. The central rule is that
**identity lives on the node and role lives on the edge**: `dataset:terramesh` is the same entity
whether one paper trained on it and another evaluated on it, and `[train]` or `[eval]` sits on the
link rather than in the registry.


## Current state

7 verified findings across 7 registries; 107 nodes and 110 edges. Every finding has been checked by
hand against the full text of its source, and gaps in the data are stated rather than guessed.


## Context

Built during an internship at the University of Warsaw. Mentor: Przemysław Biecek.

Theoretical foundation: *The Case for Model Science: Verify, Explore, Steer, Refine*
([arXiv 2606.01189](https://arxiv.org/abs/2606.01189), extended version
[arXiv 2508.20040](https://arxiv.org/abs/2508.20040)).
