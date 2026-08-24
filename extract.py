import json
import sys
from pathlib import Path

import yaml

from modelpedia import console
from modelpedia import graph as graph_json
from modelpedia import paths
from modelpedia import record_keys as keys
from modelpedia import schema
from modelpedia.build import database
from modelpedia.ingest import adoption, answers, citations, comparison, facets, prompt
from modelpedia.ingest import proposals, registries, split as splitter
from modelpedia.ingest import report
from modelpedia.ingest import link
from modelpedia.ingest import manifest as store
from modelpedia.ingest import tagging
from modelpedia.ingest import text as textutil

PROMPTS = paths.PROMPTS
PAGE_PROMPTS = paths.PAGE_PROMPTS
ENTITY_PROMPTS = paths.ENTITY_PROMPTS
ENTITY_INDEX_FILE = "_index.jsonl"
FACET_PROMPTS = paths.FACET_PROMPTS
ANSWERS = paths.ANSWERS
TEXTS = paths.TEXTS
PDFS = paths.PDFS
META = paths.META
TAGS = paths.TAGS
REPORT = paths.ENTITY_REPORT
PROPOSED = paths.PROPOSED
EXAMPLES = ("TM-007", "FX-001")
READABLE = (".yaml", ".yml", ".txt", ".md", "")


def fail(message):
    print("ERROR %s" % message)
    return 1


def texts():
    if not TEXTS.exists():
        return {}
    return {path.stem: path for path in sorted(TEXTS.glob("*.txt"))}


def raw_texts(papers):
    found = texts()
    return {paper: found[paper].read_text(encoding="utf-8")
            for paper in papers if paper in found}


def document_of(paper):
    cached = TEXTS / ("%s.txt" % paper)
    if cached.exists():
        return textutil.from_text(str(cached), cached.read_text(encoding="utf-8"))
    return textutil.document(PDFS / ("%s.pdf" % paper))


def pages_of(paper):
    return document_of(paper).pages


def wanted(argument, available):
    if not argument:
        return sorted(available)
    chosen = [paper for paper in argument.split(",") if paper in available]
    missing = [paper for paper in argument.split(",") if paper not in available]
    for paper in missing:
        print("  WARN %s has no extracted text, skipped" % paper)
    return chosen


def registry_names(entities):
    names = {}
    for key, entry in entities.items():
        if entry.get("name"):
            names[key] = entry["name"]
    return names


def write_prompts(argument=None, pages=False):
    available = texts()
    if not available:
        return fail("no extracted text; run: harvest.py text")
    db = database.load()
    concepts = {key: entry for key, entry in db.entities.items()
                if entry.get("type") == graph_json.CONCEPT}
    examples = [yaml.safe_load((paths.FINDINGS / ("%s.yaml" % name)).read_text(encoding="utf-8"))
                for name in EXAMPLES]
    names = registry_names(db.entities)

    target = PAGE_PROMPTS if pages else PROMPTS
    target.mkdir(parents=True, exist_ok=True)
    sizes, truncated = [], []
    for paper in wanted(argument, available):
        if pages:
            body, was_cut = prompt.build_pages(paper, concepts, examples, names), False
        else:
            raw = available[paper].read_text(encoding="utf-8", errors="replace")
            body, was_cut = prompt.build(paper, raw, concepts, examples, names)
        (target / ("%s.txt" % paper)).write_text(body, encoding="utf-8")
        sizes.append(len(body))
        if was_cut:
            truncated.append(paper)
    if not sizes:
        return fail("nothing to write")
    sizes.sort()
    print("wrote %s under %s" % (console.plural(len(sizes), "prompt"), target))
    print("  characters: min %d, median %d, max %d"
          % (sizes[0], sizes[len(sizes) // 2], sizes[-1]))
    print("  truncated: %d" % len(truncated))
    return 0


def corpus_index():
    return {paper: textutil.flatten(path.read_text(encoding="utf-8", errors="replace"))
            for paper, path in texts().items()}


def assignments_from(arguments):
    pairs = {}
    for argument in arguments:
        name, separator, paper = argument.partition("=")
        if not separator or not name or not paper:
            raise ValueError("expected <file>=<paper>, got %r" % argument)
        pairs[name] = paper
    return pairs


def collect(inbox, assignments):
    folder = Path(inbox)
    if not folder.is_dir():
        return fail("%s is not a directory" % inbox)
    index = corpus_index()
    if not index:
        return fail("no extracted text to match answers against")

    ANSWERS.mkdir(parents=True, exist_ok=True)
    claimed, problems, repaired, disagreed = {}, [], [], []
    for path in sorted(folder.iterdir()):
        if not an_answer(path):
            continue
        try:
            answer = answers.read(path.read_text(encoding="utf-8", errors="replace"))
        except answers.Unreadable as error:
            problems.append((path.name, str(error)))
            continue
        if answer.repaired:
            repaired.append(path.name)
        found = answers.match(answer.document, index)
        named = path.stem if path.stem in index else None
        paper = assignments.get(path.name) or named or (found.paper if found.confident() else None)
        if paper is None:
            problems.append((path.name, "cannot be matched; pass %s=<paper>" % path.name))
            continue
        if named and found.confident() and found.paper != named and not assignments.get(path.name):
            disagreed.append((path.name, found.paper, round(found.score, 1)))
        if paper in claimed:
            problems.append((path.name, "same paper as %s" % claimed[paper][0]))
            continue
        claimed[paper] = (path.name, answer.document, found.score)

    for paper, (name, document, score) in sorted(claimed.items()):
        target = ANSWERS / ("%s.yaml" % paper)
        replaced = ""
        if target.exists():
            before = yaml.safe_load(target.read_text(encoding="utf-8")) or {}
            replaced = "  REPLACES an answer with %s" % console.plural(
                len(before.get(answers.FINDINGS) or []), "finding")
        target.write_text(
            yaml.safe_dump(document, allow_unicode=True, sort_keys=False, width=100),
            encoding="utf-8")
        print("  %-12s %s, %.1f score%s%s"
              % (paper, console.plural(len(document[answers.FINDINGS]), "finding"), score,
                 "" if name == "%s.yaml" % paper else "  <- %s" % name, replaced))

    print("\ncollected %d of %d papers" % (len(claimed), len(index)))
    if repaired:
        print("repaired formatting in: %s" % ", ".join(repaired))
    for name, why in problems:
        print("  UNRESOLVED %-26s %s" % (name, why))
    for name, other, score in disagreed:
        print("  NAME OVER CONTENT %-18s content scored %.1f for %s; the name won"
              % (name, score, other))
    missing = [paper for paper in index if paper not in claimed]
    if missing:
        print("still missing (%d): %s" % (len(missing), ", ".join(sorted(missing))))
    return 1 if problems or disagreed else 0


def collected():
    if not ANSWERS.exists():
        return {}
    return {path.stem: yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            for path in sorted(ANSWERS.glob("*.yaml"))}


def judged_entities(documents):
    for paper, document in sorted(documents.items()):
        entities = document.get(answers.ENTITIES) or []
        if not entities:
            continue
        pages = pages_of(paper)
        for entity in entities:
            yield paper, entity, citations.judge(entity.get("citation"), pages)


CITATION_STATES = (citations.CONFIRMED, citations.PARTIAL, citations.REJECTED, citations.ABSENT)


def verify():
    documents = collected()
    if not documents:
        return fail("no collected answers; run: extract.py collect <directory>")
    rows, tally = [], {state: 0 for state in CITATION_STATES}
    for paper, entity, verdict in judged_entities(documents):
        tally[verdict.state] += 1
        rows.append({"paper": paper, "name": entity.get("name"),
                     "kind": entity.get("kind"), "state": verdict.state,
                     "overlap": verdict.overlap, "page": verdict.page,
                     "identifier": citations.identifier_in(entity.get("citation")),
                     "citation": prompt.squeezed(entity.get("citation"))})
    if not rows:
        print("no entities with citations to check")
        return 0

    with REPORT.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    print(report.for_citations(tally, CITATION_STATES, citations.REJECTED, REPORT))
    return 1 if tally[citations.REJECTED] else 0


def verdicts_for(documents):
    found = {}
    for paper, entity, verdict in judged_entities(documents):
        key = textutil.flatten(str(entity.get("name") or ""))
        found[(paper, key)] = verdict.state
    return found


def propose(least=1):
    documents = collected()
    if not documents:
        return fail("no collected answers; run: extract.py collect <directory>")
    db = database.load()
    found = proposals.gather(documents, db.entities, verdicts_for(documents))
    kept = [item for item in found if item.reach() >= least]
    known = {key for key, entry in db.entities.items()
             if entry.get("type") == graph_json.CONCEPT}
    unknown, misshapen = proposals.off_list(documents, known)

    print(report.for_proposals(report.Proposed(
        found=tuple(found), kept=tuple(kept), families=tuple(proposals.families(kept)),
        concepts=proposals.concept_answers(documents), unknown=unknown, misshapen=misshapen,
        papers=len(documents), least=least)))

    with PROPOSED.open("w", encoding="utf-8") as handle:
        for item in kept:
            handle.write(json.dumps(item._asdict(), ensure_ascii=False) + "\n")
    print("\nwrote %d rows to %s" % (len(kept), PROPOSED))
    return 0


FINDING_PREFIX = "IC"


def highest_number(prefix):
    """A second run continues the sequence instead of starting at one. Without this the writer
    numbered from `IC-001` again, found those files on disk, skipped them as "already there" and
    dropped 42 fresh records without saying so."""
    found = [int(path.stem.rpartition("-")[2]) for path in paths.FINDINGS.glob("%s-*.yaml" % prefix)
             if path.stem.rpartition("-")[2].isdigit()]
    return max(found, default=0)


def written_sources(findings):
    return {link[keys.REF] for finding in findings.values()
            for link in finding.get("sources") or [] if link.get(keys.REF)}


def without_written_sources(kept, findings):
    """A paper whose findings are already in the base is not written a second time. The base is
    additive here: replacing a record is a decision about data, not a step in a run."""
    held = written_sources(findings)
    fresh = [candidate for candidate in kept
             if not ({link[keys.REF] for link in candidate.record.get("sources") or []} & held)]
    return fresh, len(kept) - len(fresh)


def source_entries(documents):
    entries, by_paper = {}, {}
    for paper in sorted(documents):
        path = META / ("%s.json" % paper)
        if not path.exists():
            continue
        slug, entry = splitter.source_entry(json.loads(path.read_text(encoding="utf-8")))
        key = "%s:%s" % (graph_json.SOURCE, slug)
        entries[key] = entry
        by_paper[paper] = key
    return entries, by_paper


def split(write=False, force=False):
    documents = collected()
    if not documents:
        return fail("no collected answers; run: extract.py collect <directory>")
    db = database.load()
    entries, by_paper = source_entries(documents)
    known = {key for key, entity in db.entities.items()
             if entity.get("type") == graph_json.CONCEPT}
    kept, dropped, refused = splitter.split(documents, db.entities, by_paper,
                                            FINDING_PREFIX, known,
                                            db.vocabularies[schema.ROLE_SCOPE],
                                            texts=raw_texts(documents),
                                            start=highest_number(FINDING_PREFIX))
    kept, already = without_written_sources(kept, db.findings)

    used = {link[keys.REF] for candidate in kept
            for link in candidate.record.get("sources") or []}
    if already:
        print("  %d kandydatow pominietych: ich praca ma juz rekordy w bazie" % already)
    print(report.for_split(report.Split(
        kept=tuple(kept), dropped=tuple(dropped), refused=tuple(refused),
        papers=len(documents), entries=len(entries), used=len(used))))

    if not write:
        print("\nnothing written; add --write to create the files")
        return 0

    fresh_sources = [(key, entry) for key, entry in sorted(entries.items())
                     if key in used and key not in db.entities]
    if fresh_sources:
        with paths.REGISTRIES.joinpath("sources.yaml").open("a", encoding="utf-8") as handle:
            for key, entry in fresh_sources:
                handle.write("\n" + yaml.safe_dump({key: entry}, allow_unicode=True,
                                                   sort_keys=False, width=98))
    written = len(fresh_sources)
    fresh = skipped = 0
    for candidate in kept:
        target = paths.FINDINGS / ("%s.yaml" % candidate.identifier)
        if target.exists() and not force:
            skipped += 1
            continue
        body = yaml.safe_dump(dict([("id", candidate.identifier)] + list(candidate.record.items())),
                              allow_unicode=True, sort_keys=False, width=98)
        target.write_text(body, encoding="utf-8")
        fresh += 1
    print("\nwrote %s and %s"
          % (console.plural(fresh, "finding"), console.plural(written, "new source")))
    if skipped:
        print("kept %s already in data/findings; add --force to overwrite them"
              % console.plural(skipped, "record"))
    return 0


def an_answer(path):
    """A leading underscore marks a file that is about the run rather than an answer in it:
    `_log.jsonl` and any note left beside it. Without this rule a README in a run directory
    would be collected as if a model had written it."""
    return not (path.is_dir() or path.name.startswith("_")
                or path.suffix.lower() not in READABLE)


def raw_answers_in(folder):
    return {path.stem: path.read_text(encoding="utf-8", errors="replace")
            for path in sorted(Path(folder).iterdir()) if an_answer(path)}


def compare(left_name, right_name):
    for name in (left_name, right_name):
        if not Path(name).is_dir():
            return fail("%s is not a directory" % name)
    left, right = raw_answers_in(left_name), raw_answers_in(right_name)
    if not left or not right:
        return fail("%s holds no answers" % (left_name if not left else right_name))
    available = texts()
    both = sorted(set(left) & set(right) & set(available))
    if not both:
        return fail("no paper is answered in both directories and has extracted text; "
                    "answers are matched by file name here, so run collect first if needed")

    rows = comparison.rows({paper: left[paper] for paper in both},
                           {paper: right[paper] for paper in both}, document_of,
                           comparison.registry_keys(database.load().entities))
    print(report.for_comparison(report.Comparison(
        rows=tuple(rows), left_name=left_name, right_name=right_name,
        left=comparison.totals(rows, lambda row: row.left),
        right=comparison.totals(rows, lambda row: row.right),
        agreement=comparison.agreement(rows),
        only_left=tuple(sorted(set(left) - set(right))),
        only_right=tuple(sorted(set(right) - set(left))))))
    return 0


def tags(argument=None):
    db = database.load()
    concepts = tagging.concepts_in(db)
    if not concepts:
        return fail("no concepts in the registry")
    only_untagged = argument != "all"
    chosen = tagging.wanted(db, only_untagged)
    if not chosen:
        print("every finding already carries a concept; pass 'all' to re-tag them anyway")
        return 0

    TAGS.mkdir(parents=True, exist_ok=True)
    sizes = []
    for fid, finding in chosen.items():
        body = tagging.build(finding, concepts)
        (TAGS / ("%s.txt" % fid)).write_text(body, encoding="utf-8")
        sizes.append(len(body))
    sizes.sort()
    print("wrote %s under %s" % (console.plural(len(sizes), "tagging prompt"), TAGS))
    print("  characters: min %d, median %d, max %d"
          % (sizes[0], sizes[len(sizes) // 2], sizes[-1]))
    print("  scope: %s" % ("findings with no concept yet" if only_untagged else "every finding"))
    return 0


def proposed_rows(least):
    if not PROPOSED.exists():
        return None
    rows = [json.loads(line) for _, line in store.json_lines(PROPOSED)]
    return [row for row in rows if len(row["papers"]) >= least]


def entity_prompts(least=3):
    rows = proposed_rows(least)
    if rows is None:
        return fail("no proposals; run: extract.py propose")
    if not rows:
        return fail("no proposal reaches %d papers" % least)
    db = database.load()
    families = adoption.families_of(db.entities)

    ENTITY_PROMPTS.mkdir(parents=True, exist_ok=True)
    for stale in ENTITY_PROMPTS.glob("*.txt"):
        stale.unlink()
    sizes = []
    with (ENTITY_PROMPTS / ENTITY_INDEX_FILE).open("w", encoding="utf-8") as index:
        for number, row in enumerate(rows, start=1):
            body = adoption.build(row, db.entities, families)
            name = "%04d-%s.txt" % (number, link.slugify(row["name"])[:40])
            (ENTITY_PROMPTS / name).write_text(body, encoding="utf-8")
            index.write(json.dumps({"file": name, "row": row}, ensure_ascii=False) + "\n")
            sizes.append(len(body))
    sizes.sort()
    print("wrote %s under %s" % (console.plural(len(sizes), "entity prompt"), ENTITY_PROMPTS))
    print("  reaching %d paper(s) or more" % least)
    print("  characters: min %d, median %d, max %d"
          % (sizes[0], sizes[len(sizes) // 2], sizes[-1]))
    return 0


def adopt(inbox, write=False):
    folder = Path(inbox)
    if not folder.is_dir():
        return fail("%s is not a directory" % inbox)
    index_path = ENTITY_PROMPTS / ENTITY_INDEX_FILE
    if not index_path.exists():
        return fail("%s is missing; run: extract.py entities" % index_path)
    rows = {json.loads(line)["file"]: json.loads(line)["row"]
            for line in index_path.read_text(encoding="utf-8").splitlines()}
    db = database.load()
    families = adoption.families_of(db.entities)

    verdicts, unreadable = [], []
    for path in sorted(folder.iterdir()):
        if not an_answer(path):
            continue
        row = rows.get(path.name)
        if row is None:
            unreadable.append((path.name, "no such prompt in %s" % ENTITY_INDEX_FILE))
            continue
        try:
            document = adoption.read(path.read_text(encoding="utf-8", errors="replace"))
        except adoption.Unreadable as error:
            unreadable.append((path.name, str(error)))
            continue
        pages = [page for paper in row["papers"][:3] for page in pages_of(paper)]
        verdicts.append(adoption.judge(row, document, pages, families))

    print(report.for_adoption(verdicts, unreadable, len(rows)))
    indexes = {field: link.index_of(db.entities, node_type)
               for field, node_type in adoption.KINDS.items()}
    variants = link.index_of(db.entities, graph_json.VARIANT)
    parents = link.parents_of(db.entities)
    held = {}
    for verdict in verdicts:
        if verdict.adopted():
            found = adoption.already_held(verdict, indexes, variants, parents)
            if found:
                held[verdict.name] = found
    if held:
        print("\nadopted but already in the registry, so not written:")
        for name, slug in sorted(held.items()):
            print("  %-30s %s" % (name[:30], slug))

    if not write:
        print("\nnothing written; add --write to create the entries")
        return 1 if unreadable else 0

    written = registries.apply(verdicts, held, db.entities)
    for line in written:
        print("  %s" % line)
    print("\nwrote %s" % console.plural(len(written), "registry change"))
    return 1 if unreadable else 0


STAGES = (
    ("1. pdf pobrane", lambda: (len(list(PDFS.glob("*.pdf"))) if PDFS.exists() else 0, None)),
    ("2. tekst wyciagniety", lambda: (len(texts()), len(list(PDFS.glob("*.pdf")))
                                      if PDFS.exists() else 0)),
    ("3. prompty ekstrakcyjne", lambda: (len(list(PROMPTS.glob("*.txt"))) if PROMPTS.exists()
                                         else 0, len(texts()))),
    ("4. odpowiedzi modelu", lambda: (answered(), len(list(PROMPTS.glob("*.txt")))
                                      if PROMPTS.exists() else 0)),
    ("5. zebrane do answers", lambda: (len(list(ANSWERS.glob("*.yaml"))) if ANSWERS.exists()
                                       else 0, answered())),
    ("6. cytaty sprawdzone", lambda: (lines_in(REPORT), None)),
    ("7. encje zaproponowane", lambda: (lines_in(PROPOSED), None)),
    ("8. decyzje o encjach", lambda: (entity_answers(), lines_in(PROPOSED))),
    ("9. findingi w bazie", lambda: (len(list(paths.FINDINGS.glob("*.yaml"))), None)),
)


def lines_in(path):
    if not path.exists():
        return 0
    return sum(1 for _ in path.open(encoding="utf-8"))


def answered():
    if not paths.RUNS.exists():
        return 0
    return max((len(list(run.glob("*.txt"))) for run in paths.RUNS.iterdir() if run.is_dir()),
               default=0)


def entity_answers():
    folder = paths.RUNS / "entities-medium"
    return len(list(folder.glob("*.txt"))) if folder.exists() else 0


def findings_waiting():
    documents = collected()
    return sum(len(document.get(answers.FINDINGS) or []) for document in documents.values())


def status():
    print("gdzie jestesmy")
    for label, measure in STAGES:
        done, total = measure()
        share = "" if not total else "  %3.0f%%" % (100 * done / total)
        print("  %-26s %6d%s%s"
              % (label, done, "" if total is None else " z %d" % total, share))
    print()
    print("  findingow w zebranych odpowiedziach: %d" % findings_waiting())
    db = database.load()
    print("  rejestry: %d modeli, %d wariantow, %d zbiorow, %d metod, %d konceptow"
          % tuple(sum(1 for entity in db.entities.values() if entity.get("type") == node_type)
                  for node_type in (graph_json.MODEL, graph_json.VARIANT, graph_json.DATASET,
                                    graph_json.METHOD, graph_json.CONCEPT)))
    return 0


def retag(inbox, write=False):
    folder = Path(inbox)
    if not folder.is_dir():
        return fail("%s is not a directory" % inbox)
    db = database.load()
    known = set(tagging.concepts_in(db))
    before = {fid: [link[keys.REF] for link in finding.get("concepts") or []]
              for fid, finding in db.findings.items()}

    after, invented, unreadable, none = {}, [], [], 0
    for path in sorted(folder.iterdir()):
        if not an_answer(path) or path.stem not in db.findings:
            continue
        try:
            document = tagging.read(path.read_text(encoding="utf-8", errors="replace"))
        except (tagging.Unreadable, yaml.YAMLError) as error:
            unreadable.append((path.name, str(error).split("\n")[0]))
            continue
        taken, made_up = tagging.chosen(document, known)
        invented += [(path.stem, key) for key in made_up]
        after[path.stem] = taken
        none += 1 if not taken else 0

    print(report.for_tagging(tagging.agreement(before, after), none, invented, unreadable))
    if not write:
        print("\nnothing written; add --write to put these concepts on the records")
        return 1 if unreadable or invented else 0

    changed = 0
    for fid, taken in sorted(after.items()):
        if not taken or set(taken) == set(before.get(fid, [])):
            continue
        path = paths.FINDINGS / ("%s.yaml" % fid)
        record = yaml.safe_load(path.read_text(encoding="utf-8"))
        record[schema.CONCEPTS_FIELD] = [{keys.REF: key} for key in taken]
        path.write_text(yaml.safe_dump(record, allow_unicode=True, sort_keys=False, width=98),
                        encoding="utf-8")
        changed += 1
    print("\nwrote concepts onto %s" % console.plural(changed, "record"))
    return 0


def citations_by_name():
    found = {}
    if not REPORT.exists():
        return found
    for line in REPORT.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row.get("state") == citations.CONFIRMED and (row.get("citation") or "").strip():
            found.setdefault(textutil.flatten(str(row.get("name") or "")), row["citation"])
    return found


def facet_prompts():
    db = database.load()
    todo = facets.wanted(db.entities)
    if not todo:
        print("every model already carries a facet")
        return 0
    allowed = facets.vocabulary(db)
    quotes = citations_by_name()

    FACET_PROMPTS.mkdir(parents=True, exist_ok=True)
    for stale in FACET_PROMPTS.glob("*.txt"):
        stale.unlink()
    sizes = []
    for key, entity in todo.items():
        citation = quotes.get(textutil.flatten(str(entity.get("name") or "")), "")
        body = facets.build(key, entity, allowed, citation)
        (FACET_PROMPTS / ("%s.txt" % key.partition(":")[2])).write_text(body, encoding="utf-8")
        sizes.append(len(body))
    sizes.sort()
    print("wrote %s under %s" % (console.plural(len(sizes), "facet prompt"), FACET_PROMPTS))
    print("  characters: min %d, median %d, max %d"
          % (sizes[0], sizes[len(sizes) // 2], sizes[-1]))
    return 0


def refacet(inbox, write=False):
    folder = Path(inbox)
    if not folder.is_dir():
        return fail("%s is not a directory" % inbox)
    db = database.load()
    allowed = facets.vocabulary(db)
    taken, refused, unreadable, empty = {}, [], [], 0
    for path in sorted(folder.iterdir()):
        if not an_answer(path):
            continue
        key = "%s:%s" % (graph_json.MODEL, path.stem)
        if key not in db.entities:
            unreadable.append((path.name, "no such model in the registry"))
            continue
        try:
            document = facets.read(path.read_text(encoding="utf-8", errors="replace"))
        except (facets.Unreadable, yaml.YAMLError) as error:
            unreadable.append((path.name, str(error).split("\n")[0]))
            continue
        values, off_list = facets.chosen(document, allowed)
        refused += [(path.stem, field, value) for field, value in off_list]
        if values:
            taken[key] = values
        else:
            empty += 1

    print(report.for_facets(taken, empty, refused, unreadable))
    if not write:
        print("\nnothing written; add --write to put these facets on the entries")
        return 1 if unreadable or refused else 0
    written = sum(1 for key, values in sorted(taken.items())
                  if registries.add_fields("models", key, values))
    print("\nwrote facets onto %s" % console.plural(written, "model"))
    return 0


USAGE = """usage:
  python3 extract.py status                   gdzie jestesmy: etap po etapie, z licznikami\n  python3 extract.py prompts [paper,paper] [--pages]
                                             corpus/text -> corpus/prompts, one per paper;
                                             --pages writes the instructions alone to
                                             corpus/prompts-pages, for ask.py --pdf
  python3 extract.py collect <directory>     model answers -> corpus/answers, matched by content
                                             add file.txt=<paper> for answers with no findings
  python3 extract.py verify                  check every citation against its source PDF
  python3 extract.py propose [N]             new entities not in any registry, N papers or more
  python3 extract.py tags [all]              one tagging prompt per finding without a concept\n  python3 extract.py entities [N]            one prompt per proposed entity reaching N papers,\n                                             asking the model whether it earns a registry entry\n  python3 extract.py adopt <dir> [--write]   read those answers, check every anchor against the\n                                             citing paper, report; --write applies them
  python3 extract.py compare <dir> <dir>     two sets of answers to the same papers, side by side:
                                             findings, models, numbers and citations checked
                                             against each paper's own text. Matched by file name
  python3 extract.py split [--write] [--force]  collected answers -> data/findings/, reports first;
                                             --write never overwrites, --force does"""


def main(argv):
    if len(argv) < 2:
        print(USAGE)
        return 2
    command, rest = argv[1], argv[2:]
    if command == "prompts":
        chosen = [item for item in rest if item != "--pages"]
        return write_prompts(chosen[0] if chosen else None, "--pages" in rest)
    if command == "collect":
        if not rest:
            return fail("collect needs a directory")
        try:
            pairs = assignments_from(rest[1:])
        except ValueError as error:
            return fail(str(error))
        return collect(rest[0], pairs)
    if command == "verify":
        return verify()
    if command == "facets":
        return facet_prompts()
    if command == "refacet":
        if not rest:
            return fail("refacet needs a directory of answers")
        return refacet(rest[0], "--write" in rest)
    if command == "retag":
        if not rest:
            return fail("retag needs a directory of tagging answers")
        return retag(rest[0], "--write" in rest)
    if command == "tags":
        return tags(rest[0] if rest else None)
    if command == "entities":
        try:
            least = int(rest[0]) if rest else 3
        except ValueError:
            return fail("entities takes a whole number of papers, not %r" % rest[0])
        return entity_prompts(least)
    if command == "adopt":
        if not rest:
            return fail("adopt needs a directory of answers")
        return adopt(rest[0], write="--write" in rest)
    if command == "status":
        return status()
    if command == "compare":
        if len(rest) < 2:
            return fail("compare needs two directories of answers")
        return compare(rest[0], rest[1])
    if command == "split":
        return split("--write" in rest, "--force" in rest)
    if command == "propose":
        try:
            least = int(rest[0]) if rest else 1
        except ValueError:
            return fail("propose takes a whole number of papers, not %r" % rest[0])
        if least < 1:
            return fail("propose takes a positive number of papers, not %d" % least)
        return propose(least)
    print(USAGE)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
