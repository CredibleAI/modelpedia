# Modelpedia

A database of **findings** about machine learning models: third-party claims about how a specific
model behaves, made after the fact. The YAML files under `data/` are the only source of truth;
the graph, the site and the CSV exports are all derived from them and can be deleted and rebuilt
at any time.

This README is a manual. What every command does, in the order you would normally run them.

---

## Setup

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

```bash
source .venv/bin/activate
```

Activate once per shell and every command below works as written. `PyYAML` is all the build and
the site need. `pypdfium2` is used to read PDFs and `openreview-py` only by `harvest.py`. That
script and `ask.py` are the two that touch the network, and `ask.py` needs nothing installed:
the model endpoint speaks the OpenAI chat API, which is one POST that `urllib` can make. `pillow`
is needed only by `ask.py --pdf`, which renders pages to images; nothing else imports it.

---

## Everyday commands

You will run these most often. None of them touches the network.

| Command | What it does |
|---|---|
| `python3 run_tests.py` | Runs all 372 tests across six suites. No arguments. |
| `python3 build.py` | Validates `data/`, writes `out/graph.json`, prints an audit. **Exits non-zero and writes nothing if validation fails, so run it before every commit.** |
| `python3 render.py` | Builds the static site into `site/` from `out/graph.json`. |
| `python3 export.py` | Writes one CSV per node type plus `edges.csv` into `out/csv/`. |

The usual loop after editing any YAML file:

```bash
python3 build.py && python3 render.py
```

Then open `site/index.html` by double-clicking it. Links are relative, so no server is needed and
the whole folder can be zipped and sent as one thing.

### What the audit tells you

`build.py` prints more than a pass or a fail. It lists how many records came from manual versus
automatic extraction, how many findings and how many models each concept reaches, which entities
more than one finding reaches, which findings have no dataset link, which no concept covers,
which registry entries lack an anchor, and which registry entries nothing reaches at all.

---

## Checking one record

| Command | What it does |
|---|---|
| `python3 check.py path/to/candidate.yaml` | Schema errors and link resolution for a candidate finding that is not in `data/` yet. Blames only the candidate, never the existing registries. |
| `python3 verify.py data/findings/ID.yaml source.pdf` | Locates the record's numbers, linked entity names and stable identifiers in the PDF, and finds the page sharing most of the caveat's vocabulary. |

`verify.py` changes nothing on disk. Missing items are suspicions for a human, not corrections.
It exits 1 when a check is blocking **or when the record offered nothing to check at all** — an
empty finding must not report `0 blocking` and look like a pass.

---

## Gathering papers

`harvest.py` is one of the two scripts that talk to the network, the other being `ask.py`. Run it
with `.venv/bin/python`, because
`openreview-py` lives there. `OPENREVIEW_USERNAME` and `OPENREVIEW_PASSWORD` must be set in the
environment — never in the repository.

| Command | What it does |
|---|---|
| `harvest.py doctor` | Offline. Checks the interpreter, the installed packages and that the API client still has the methods we call. No account needed. |
| `harvest.py preflight [venue_id]` | Everything `doctor` does, plus a real login. With a venue id it also reports which API generation answers for it, a sample paper's fields and whether that paper's reviews are reachable. **Run this once before harvesting a new conference.** |
| `harvest.py venues [substring]` | Lists venue identifiers, e.g. `harvest.py venues ICML`. |
| `harvest.py meta <venue_id> [--all]` | Fetches metadata only — no PDFs — screens each paper and appends a row per paper to `corpus/manifest.jsonl`. Resumable: papers already in the manifest are skipped. `--all` includes rejected submissions. |
| `harvest.py reviews <venue_id> [--limit N] [--pause S] [--from FILE]` | Fetches the official reviews into `corpus/reviews/<venue>.jsonl`, one request per paper, resumable. `--limit N --pause S` fetches N papers, waits S seconds and carries on by itself until the venue is done — the way to spend a quota that refills on a clock without retyping the command. `--from` imports a review dump that is already on disk instead of asking the API. |
| `harvest.py rescreen` | Offline. Recomputes every score in the manifest from the metadata and reviews already on disk. Run it after changing `screen.py`. |
| `harvest.py rank [--out FILE] [--venue ID]` | Offline. Writes one deterministic table for every venue in the manifest to `corpus/reports/ranking.csv`, plus `ranking-<venue>.csv` beside it for each venue on its own, and prints a venue-by-venue comparison. `--venue` narrows the whole thing to one. The comparison carries a `reviewed` column, because a venue whose reviews are still downloading scores low for a reason that has nothing to do with the venue. |
| `harvest.py stats` | Tier breakdown of the manifest, how many papers carry a review, how many PDFs and texts are on disk, and which screening rules produced each row. |
| `harvest.py pdfs [--tier a,b] [--venue ID] [--limit N] [--pause S] [--ids FILE]` | Downloads PDFs for the chosen tiers into `corpus/pdf/`. Defaults to `strong,possible`, which reaches **every** venue in the manifest until `--venue` narrows it. `--ids` takes a file with one identifier per line and overrides `--tier`. `--limit N --pause S` batches it exactly as `reviews` does, against the same quota. |
| `harvest.py text` | Extracts text from every PDF into `corpus/text/`, skipping files already done. |

A venue runs `meta` → `reviews` → `rescreen` → `rank`. Only the first two touch the network, so
changing a screening rule costs one offline pass rather than a second visit to the API.

Screening never rejects a paper, it only sorts it into `strong`, `possible` or `weak`. The score
is the sum of two halves in the same currency: the abstract read in the authors' voice, and the
reviews read in the reviewers', where a term counts only once at least half the reviewers used it.
Nothing in the score depends on what the registries currently hold or on which other papers are in
the corpus, so the same paper scores the same today and next year, and two conferences are
comparable. Downloading is a separate step so a bad screening rule costs nothing but a rerun.

---

## Turning papers into findings

`extract.py` drives the extraction pipeline. Everything here reads `corpus/text/`, never the PDFs.

| Command | What it does |
|---|---|
| `extract.py prompts [paper,paper] [--pages]` | Builds one extraction prompt per paper from `corpus/text/` into `corpus/prompts/`. With no argument, every paper. The whole paper goes in: there is no length limit, because the endpoint's window is three times the longest text in the corpus. `--pages` writes the instructions without the text into `corpus/prompts-pages/`, for `ask.py --pdf`. |
| `extract.py collect <directory>` | Reads model answers from a directory, repairs common YAML damage, matches each answer to its paper by content and saves it into `corpus/answers/`. Add `file.txt=<paper>` to assign an answer that cannot be matched. |
| `extract.py verify` | Checks every citation the model wrote against the text of its own paper and writes `corpus/reports/entities.jsonl`. Exits 1 if any citation is rejected. |
| `extract.py propose [N]` | Lists entities the answers name that no registry holds, reaching N papers or more. Also reports which concepts the model refused, proposed or silently skipped. Writes `corpus/reports/proposed.jsonl`. |
| `extract.py tags [all]` | Writes one small tagging prompt per finding that carries no concept. `all` re-tags every finding instead. |
| `extract.py split [--write] [--force]` | Turns collected answers into records under `data/findings/`. **Reports only by default.** `--write` creates files but never overwrites; `--force` overwrites. |
| `extract.py compare <dir> <dir>` | Two sets of answers to the same papers, side by side: findings, models, `key_metric` numbers and citations, each checked against that paper's own text. Answers are paired by file name, and only papers both sides answered are counted. |

A candidate from the entity linker is **never** accepted automatically. It is a suggestion for a
human: on four real suggestions, two were wrong.

### Asking the model

`ask.py` sends the prompts and keeps the answers, so the loop between `prompts` and `collect` runs
without a person in the middle. It speaks the OpenAI chat API over `urllib` and adds no dependency.
`MODEL_API_USERNAME` and `MODEL_API_PASSWORD` must be set in the environment; `MODEL_API_URL` and
`MODEL_API_MODEL` override the defaults.

| Command | What it does |
|---|---|
| `ask.py doctor` | Endpoint, models served, and one 200-token round trip. Exits 1 if the chosen model is not served or does not answer. |
| `ask.py run [options]` | `corpus/prompts/` → `corpus/runs/text/`, one file per paper. Resumable: a paper already answered is not asked again unless `--force`. `--dir` and `--out` move both ends, `--only` and `--limit` narrow the run, `--dry-run` prints the plan and sends nothing. |

```bash
python3 ask.py doctor && python3 ask.py run --limit 5 && python3 extract.py collect corpus/runs/text
```

`--pdf corpus/pdf` sends the paper as one image per page instead of as text, paired with
`extract.py prompts --pages`. The endpoint refuses PDF files outright but accepts images, so the
pages are rendered locally with `pypdfium2` and Pillow. It costs about 1900 tokens per page —
1.8x the text of the same paper — and a paper beyond roughly 65 pages no longer fits the model's
window at all. Use it where the extracted text is poor, not by default: given the figures, the
model starts reading numbers off them, which the prompt forbids and the text path cannot do.

Reasoning is charged against `max_tokens`: left alone, the model spends the whole budget thinking
and returns nothing. `--think off` turns thinking off in the chat template, `low`/`medium`/`high`
bound it. A reply cut off by `max_tokens` is kept as `<paper>.truncated`, an extension `collect`
does not read, and the run exits 1 — a half-written YAML file must not reach the answers directory
looking like an answer. Every attempt is logged to `corpus/runs/text/_log.jsonl`.

---

## Layout

```
build.py         YAML -> validate -> assemble -> out/graph.json, then the audit
render.py        out/graph.json -> site/
export.py        out/graph.json -> out/csv/*.csv
check.py         a candidate finding -> schema errors plus link resolution
verify.py        a finding + its PDF -> evidence locations and suspicions
harvest.py       OpenReview -> corpus/; one of the two scripts that use the network
extract.py       corpus/ -> prompts, answers, proposals, data/findings/
ask.py           corpus/prompts/ -> corpus/runs/text/; the other script that uses the network
run_tests.py     runs all six suites

modelpedia/      the library; imported, never run
  graph.py       node and edge types, the NODE_TYPES table
  schema.py      the finding schema: link fields, vocabularies, regexes
  paths.py       every filesystem location; the only file that derives the root
  graph_io.py    load/dump out/graph.json with the format_version guard
  atomic.py      write-then-rename; the single .part convention
  record_keys.py string constants for keys inside records
  console.py     console output primitives

  build/         data/*.yaml -> out/graph.json
    database.py  the only YAML reader
    validate.py  Database -> error strings; creates nothing
    assemble.py  Database -> the graph dict; validates nothing
    report.py    the audit build.py prints

  site/          out/graph.json -> HTML
  ingest/        papers -> candidate findings
    text.py        PDF -> normalised searchable text
    link.py        entity name -> hit / candidates / miss
    screen.py      abstract and reviews -> score and tier; one scorer, two rule sets
    manifest.py    the corpus jsonl stores: validation, reading, selection
    openreview.py  everything that knows the OpenReview API
    chat.py        request body, reply reading and retry policy for ask.py
    comparison.py  two answer sets over one paper -> counts that can be compared
    report.py      console reports for extract.py
    verification.py the evidence checks verify.py runs

data/            vocabularies.yaml, registries/*.yaml, findings/*.yaml
corpus/          harvested papers and model answers; not tracked
  manifest.jsonl one row per paper: metadata, score, tier, review count
  reviews/       one jsonl per venue, one row per review
  reports/       ranking.csv and the entity reports
out/, site/      build artifacts; not tracked, delete and rebuild freely
```

---

## Rules worth knowing before you edit anything

**Never search a PDF except through `modelpedia/ingest/text.py`.** Extracted text breaks words
across lines, splits small-capital headings and mangles ligatures. A plain `grep` misses them and
reports absence that is not real. This has already cost the project one wrongly deleted citation.

**Identity lives on the node, role lives on the edge.** `dataset:terramesh` is the same entity
whether one paper trained on it and another evaluated on it; `[train]` or `[eval]` belongs on the
link, never in the registry.

**Gaps are stated, not guessed.** Where a source names no dataset or prints no URL, the field
stays empty and `build.py` lists it in the audit. A visible gap beats false precision.

**Every artifact is replaced in one step or not at all.** Each writer stages its output beside the
target and renames it into place, so a crash or a full disk leaves the last good output untouched.

**Read a JSONL file only through `manifest.json_lines`.** `str.splitlines()` also breaks on
U+2028 and U+2029, which `json.dumps(ensure_ascii=False)` writes through unescaped because JSON
does not treat them as line breaks. 111 of them sit in ICLR 2025 review prose, and reading that
file the obvious way cut 92 records in half and reported them as bad JSON.

---

## Current state

62 findings across 5 registries; 388 nodes and 755 edges. Nine were written by hand
(`extracted_by: manual-extraction`); the other 53 came from ICLR 2025 through automatic extraction
and **have not been read against their sources**.

`extracted_by` is the only record-level field and it states origin, nothing more. An earlier
`review_status` field was removed because reading the sources found errors in 5 of the 7 records
that carried `verified` — the label recorded that someone had checked, not that the check was
good. **A record's presence in this database is not evidence that anyone verified it against its
source.**
