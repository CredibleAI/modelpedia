<div align="center">

# Modelpedia

**A catalog of model findings for the meta-science of AI**

[![Paper](https://img.shields.io/badge/Paper-arxiv.2609.01090-FF6B6B.svg)](https://arxiv.org/abs/2609.01090)
[![Catalog](https://img.shields.io/badge/Catalog-browse-4C9AFF.svg)](https://credibleai.github.io/modelpedia/index.html)
[![Credible AI](https://img.shields.io/badge/Credible%20AI-group-6C63FF.svg)](https://credibleai.github.io/)
[![License](https://img.shields.io/badge/Code-MIT-3DA639.svg)](LICENSE)
[![Data](https://img.shields.io/badge/Data-CC%20BY%204.0-EE9B00.svg)](LICENSE-DATA)

</div>

## About

From the paper ([arXiv:2609.01090](https://arxiv.org/abs/2609.01090)):

> Scientific knowledge about AI models is produced faster than the community can organize it.
> Every few months a new foundation model reshapes the field and hundreds of papers, blogs, and
> technical reports document how each behaves or fails. Yet, these findings remain scattered and
> effectively unretrievable. To address this gap we present Modelpedia, an automated, LLM-assisted
> framework that extracts findings about models from published papers, links it to the model,
> dataset, method, and concept it concerns, and aggregates the result into a searchable public
> catalog. Applying the prototype to accepted ICLR 2024 and 2025 papers, we extract over a
> thousand findings and, treating the catalog itself as an object of study, run a meta-analysis of
> how the community investigates models. Now, we invite the community to explore, contribute to,
> and build on the open catalog, and to help establish model findings as a shared foundation for
> the meta-science of AI.

A **finding** is a third-party claim about how one model behaves, made after the fact. Findings
about different models meet on the mechanisms they describe.

### What is in the catalog

| | |
|---|---|
| Findings | 1026, drawn from 460 source papers (ICLR 2024 and 2025) |
| Models | 500, plus 408 named variants |
| Datasets / methods / concepts | 679 / 475 / 12 |
| Graph | 3560 nodes, 11748 edges |

The YAML files under `data/` are the only source of truth. The graph, the site and the CSV exports
are derived from them and can be deleted and rebuilt at any time.

## Installation

Dependencies are declared in `pyproject.toml` and pinned in `uv.lock`. Install
[uv](https://docs.astral.sh/uv/), then:

```bash
uv sync
```

That covers the build, the site and the tests. The ingestion side is an optional extra:

```bash
uv sync --extra ingest
```

It adds `openreview-py`, `pypdfium2` and `pillow`, which only `modelpedia harvest` and
`modelpedia ask` use. Those are also the only two commands that reach the network.

## Documentation

The everyday loop, after editing any YAML file under `data/`:

```bash
modelpedia build && modelpedia render
```

`build` validates the data and writes `out/graph.json`. `render` writes `site/`, which opens by
double-clicking `site/index.html`; links are relative, so no server is needed.

Run the test suite with `pytest`. The pipeline is deterministic: rebuild from the same YAML and
you get the same graph and the same 3160 pages, byte for byte.

The [About page](https://credibleai.github.io/modelpedia/index.html) covers the record schema and
the pipeline. For method and results, read the paper.

## API Reference

One command with eight sub-commands. Run any of them bare to see its own options.

| Command | Purpose |
|---|---|
| `modelpedia build` | Validate `data/` and assemble `out/graph.json`, then print an audit. |
| `modelpedia render` | Write the static site to `site/`. |
| `modelpedia export` | Write one CSV per node type plus `edges.csv` to `out/csv/`. |
| `modelpedia check <file>` | Schema errors and link resolution for a candidate finding. |
| `modelpedia verify <finding> <pdf>` | Locate a record's numbers, entities and citations in its own source. |
| `modelpedia harvest …` | Fetch papers and reviews from OpenReview; screen and rank them. |
| `modelpedia ask …` | Send extraction prompts to the model endpoint and keep the answers. |
| `modelpedia extract …` | Turn model answers into records under `data/findings/`. |

## Appendix - the extraction pipeline, in order

Three LLM steps with deterministic checks between them, following the paper's appendix.
`modelpedia extract status` shows where a run has got to.

### Gathering papers

Needs `OPENREVIEW_USERNAME` and `OPENREVIEW_PASSWORD` in the environment.

| Command | |
|---|---|
| `modelpedia harvest doctor` | Offline check of dependencies and the API contract. |
| `modelpedia harvest venues ICLR` | Find the venue identifier. |
| `modelpedia harvest preflight <venue>` | Log in and confirm the venue answers. |
| `modelpedia harvest meta <venue>` | Metadata only, screened on the abstract. |
| `modelpedia harvest reviews <venue>` | Official reviews, one request per paper. |
| `modelpedia harvest rescreen` | Recompute every score from what is on disk. |
| `modelpedia harvest rank` | A ranking table per venue plus a combined one. |
| `modelpedia harvest stats` | Tier breakdown, and which rows are out of date. |
| `modelpedia harvest pdfs --ids <file>` | Download the PDFs for the selected papers. |
| `modelpedia harvest text` | PDF to text. Everything after this reads the text. |

### Step I - extraction

The model reads the whole paper and writes structured records, each citation copied from the
source.

| Command | |
|---|---|
| `modelpedia extract prompts` | One extraction prompt per paper. |
| `modelpedia ask doctor && modelpedia ask run` | Send them. Answers land in `corpus/runs/`. |
| `modelpedia extract collect <answers>` | Match each answer to its paper and keep it. |
| `modelpedia extract verify` | Check every citation against its own paper. |

### Step II - entities

The model decides whether a newly named entity earns a permanent registry entry, so the same
thing is not stored twice under two spellings.

| Command | |
|---|---|
| `modelpedia extract propose` | Entity names no registry holds yet. |
| `modelpedia extract entities` | One prompt per proposed entity. |
| `modelpedia extract adopt <answers> --write` | Write the accepted ones into the registries. |
| `modelpedia harvest anchors --write` | Derive their anchors from the citations. |
| `modelpedia extract split --write` | Collected answers become `data/findings/`. |

### Step III - concepts and model attributes

Findings that carry no concept get tagged, models that carry no facets get filled in.

| Command | |
|---|---|
| `modelpedia extract tags` | One tagging prompt per untagged finding. |
| `modelpedia extract retag <answers> --write` | Write the accepted concept tags. |
| `modelpedia extract facets` | One prompt per model with no modality, task or domain. |
| `modelpedia extract refacet <answers> --write` | Write the accepted facets. |
| `modelpedia build && modelpedia render` | Rebuild the graph and the site. |

The entity linker only proposes; whoever runs the pipeline accepts or rejects each suggestion.
After changing a prompt or a setting, `modelpedia extract compare <dir> <dir>` puts two runs over
the same papers side by side.

## Contributing

Ideas for where the catalog should go next are collected in an open
[development ideas spreadsheet](https://onedrive.live.com/:x:/g/personal/f467752cf3943808/IQAquTDDbdKnQY4OdRuWF4AXAdEOBNnJ-KAYPFNanxR4yFw?rtime=lhfvm-AI30g&redeem=aHR0cHM6Ly8xZHJ2Lm1zL3gvYy9mNDY3NzUyY2YzOTQzODA4L0lRQXF1VEREYmRLblFZNE9kUnVXRjRBWEFkRU9CTm5KLUtBWVBGTmFueFI0eUZ3P2U9YlhzYWhU) - add a row, or open an issue. Proposals are also welcome
by email at [franciszekbern1@gmail.com](mailto:franciszekbern1@gmail.com).

## License

Code is [MIT](LICENSE). The catalog under `data/`, and anything built from it, is
[CC BY 4.0](LICENSE-DATA): reuse it however you like, credit the paper. That covers our records,
not the papers they came from - those keep their publishers' terms, and every record links to its
own source.

If you use Modelpedia, please cite:

```bibtex
@article{bernat2026modelpedia,
    title   = {Modelpedia: A Catalog of Model Findings for the Meta-Science of AI},
    author  = {Franciszek Bernat and Dawid P{\l}udowski and Micha{\l} Jan W{\l}odarczyk and
               Luca Longo and Jianlong Zhou and Andreas Holzinger and Riccardo Guidotti and
               Wojciech Samek and Przemys{\l}aw Biecek},
    journal = {arXiv preprint arXiv:2609.01090},
    year    = {2026},
    url     = {https://arxiv.org/abs/2609.01090}
}
```
