import collections
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
from modelpedia.ingest import answers, citations, prompt, proposals, split as splitter
from modelpedia.ingest import tagging
from modelpedia.ingest import text as textutil

PROMPTS = paths.CORPUS / "prompts"
ANSWERS = paths.CORPUS / "answers"
REPORT = paths.CORPUS / "entities.jsonl"
PROPOSED = paths.CORPUS / "proposed.jsonl"
EXAMPLES = ("TM-007", "FX-001")
READABLE = (".yaml", ".yml", ".txt", ".md", "")


def fail(message):
    print("ERROR %s" % message)
    return 1


def texts():
    folder = paths.CORPUS / "text"
    if not folder.exists():
        return {}
    return {path.stem: path for path in sorted(folder.glob("*.txt"))}


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


def write_prompts(argument=None):
    available = texts()
    if not available:
        return fail("no extracted text; run: harvest.py text")
    db = database.load()
    concepts = {key: entry for key, entry in db.entities.items()
                if entry.get("type") == graph_json.CONCEPT}
    examples = [yaml.safe_load((paths.FINDINGS / ("%s.yaml" % name)).read_text(encoding="utf-8"))
                for name in EXAMPLES]
    names = registry_names(db.entities)

    PROMPTS.mkdir(parents=True, exist_ok=True)
    sizes, truncated = [], []
    for paper in wanted(argument, available):
        raw = available[paper].read_text(encoding="utf-8", errors="replace")
        body, was_cut = prompt.build(paper, raw, concepts, examples, names)
        (PROMPTS / ("%s.txt" % paper)).write_text(body, encoding="utf-8")
        sizes.append(len(body))
        if was_cut:
            truncated.append(paper)
    if not sizes:
        return fail("nothing to write")
    sizes.sort()
    print("wrote %s under %s" % (console.plural(len(sizes), "prompt"), PROMPTS))
    print("  characters: min %d, median %d, max %d"
          % (sizes[0], sizes[len(sizes) // 2], sizes[-1]))
    print("  truncated: %d" % len(truncated))
    return 0


def corpus_index():
    return {paper: textutil.flatten(path.read_text(encoding="utf-8", errors="replace"))
            for paper, path in texts().items()}


def collect(inbox, assignments):
    folder = Path(inbox)
    if not folder.is_dir():
        return fail("%s is not a directory" % inbox)
    index = corpus_index()
    if not index:
        return fail("no extracted text to match answers against")

    ANSWERS.mkdir(parents=True, exist_ok=True)
    claimed, problems, repaired = {}, [], []
    for path in sorted(folder.iterdir()):
        if path.is_dir() or path.suffix.lower() not in READABLE:
            continue
        try:
            answer = answers.read(path.read_text(encoding="utf-8", errors="replace"))
        except answers.Unreadable as error:
            problems.append((path.name, str(error)))
            continue
        if answer.repaired:
            repaired.append(path.name)
        found = answers.match(answer.document, index)
        paper = assignments.get(path.name) or (found.paper if found.confident() else None)
        if paper is None:
            problems.append((path.name, "cannot be matched; pass %s=<paper>" % path.name))
            continue
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
    missing = [paper for paper in index if paper not in claimed]
    if missing:
        print("still missing (%d): %s" % (len(missing), ", ".join(sorted(missing))))
    return 1 if problems else 0


def verify():
    if not ANSWERS.exists():
        return fail("no collected answers; run: extract.py collect <directory>")
    rows, tally = [], {state: 0 for state in
                       (citations.CONFIRMED, citations.PARTIAL, citations.REJECTED,
                        citations.ABSENT)}
    for path in sorted(ANSWERS.glob("*.yaml")):
        document = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        entities = document.get(answers.ENTITIES) or []
        if not entities:
            continue
        pages = textutil.document(paths.CORPUS / "pdf" / ("%s.pdf" % path.stem)).pages
        for entity in entities:
            verdict = citations.judge(entity.get("citation"), pages)
            tally[verdict.state] += 1
            rows.append({"paper": path.stem, "name": entity.get("name"),
                         "kind": entity.get("kind"), "state": verdict.state,
                         "overlap": verdict.overlap, "page": verdict.page,
                         "identifier": citations.identifier_in(entity.get("citation")),
                         "citation": prompt.squeezed(entity.get("citation"))})
    if not rows:
        print("no entities with citations to check")
        return 0

    total = len(rows)
    for state in (citations.CONFIRMED, citations.PARTIAL, citations.REJECTED, citations.ABSENT):
        print("  %-10s %4d  %3.0f%%" % (state, tally[state], 100 * tally[state] / total))
    with REPORT.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, ensure_ascii=False) + "\n")
    print("\n%d entities, %d rejected, report in %s"
          % (total, tally[citations.REJECTED], REPORT))
    return 1 if tally[citations.REJECTED] else 0


def collected():
    return {path.stem: yaml.safe_load(path.read_text(encoding="utf-8")) or {}
            for path in sorted(ANSWERS.glob("*.yaml"))}


def verdicts_for(documents):
    found = {}
    for paper, document in documents.items():
        entities = document.get(answers.ENTITIES) or []
        if not entities:
            continue
        pages = textutil.document(paths.CORPUS / "pdf" / ("%s.pdf" % paper)).pages
        for entity in entities:
            key = textutil.flatten(str(entity.get("name") or ""))
            found[(paper, key)] = citations.judge(entity.get("citation"), pages).state
    return found


def propose(least=1):
    documents = collected()
    if not documents:
        return fail("no collected answers; run: extract.py collect <directory>")
    db = database.load()
    found = proposals.gather(documents, db.entities, verdicts_for(documents))
    kept = [item for item in found if item.reach() >= least]

    print("%d proposed entities from %d papers, %d reaching %d paper(s) or more"
          % (len(found), len(documents), len(kept), least))
    spread = collections.Counter(item.reach() for item in found)
    print("  reach: %s" % ", ".join("%d paper(s) x%d" % (reach, count)
                                    for reach, count in sorted(spread.items(), reverse=True)))
    usable = collections.Counter(item.state for item in found)
    print("  citation: %s" % ", ".join("%s %d" % pair for pair in sorted(usable.items())))

    kin = proposals.families(kept)
    if kin:
        print("\nrelated names that belong to one decision, resolve each group together:")
        for family in kin:
            print("  %s" % family.stem)
            for member in family.members:
                print("      %-52s %d paper(s)" % (member.name[:52], member.reach()))

    undecided = [item for item in kept if item.candidates]
    if undecided:
        print("\nclose to something already in a registry, a human decides:")
        for item in undecided:
            print("  %-40s -> %s" % (item.name[:40], ", ".join(item.candidates)))

    concepts = proposals.concept_answers(documents)
    print("\nconcepts: %s with none, %d answered, %d unanswered"
          % (console.plural(concepts.without_concept, "finding"),
             concepts.answered(), len(concepts.silent)))
    if concepts.stray:
        print("  %d entr(ies) answer for a finding that did take a concept, not counted"
              % concepts.stray)

    known = {key for key, entry in db.entities.items()
             if entry.get("type") == graph_json.CONCEPT}
    unknown, misshapen = proposals.off_list(documents, known)
    if unknown:
        print("\nconcepts chosen from outside the closed list, the model invented these:")
        for item in unknown:
            print("  %-14s %-46s %s" % (item.paper, item.finding[:46], item.value))
    if misshapen:
        print("\nconcepts on the list but written in the wrong shape, plain 'concept:slug' wanted:")
        for item in misshapen:
            print("  %-14s %-46s %s" % (item.paper, item.finding[:46], item.written))

    if concepts.proposals:
        print("\nproposed concepts, none accepted automatically:")
        for record in concepts.proposals:
            print("  %-28s %d paper(s)" % (record["name"][:28], len(record["papers"])))
            if record["definitions"]:
                print("      %s" % record["definitions"][0][:150])

    if concepts.refusals:
        print("\nfindings left untagged on purpose, closest concept and the reason:")
        for refusal in concepts.refusals:
            print("  %-52s %s" % (refusal.finding[:52], refusal.closest or "-"))
            if refusal.why:
                print("      %s" % refusal.why[:150])

    if concepts.silent:
        print("\nfindings with no concept and no answer at all, the block did not fire:")
        for gap in concepts.silent:
            print("  %-14s %s" % (gap.paper, gap.finding[:60]))

    with PROPOSED.open("w", encoding="utf-8") as handle:
        for item in kept:
            handle.write(json.dumps(item._asdict(), ensure_ascii=False) + "\n")
    print("\nwrote %d rows to %s" % (len(kept), PROPOSED))
    return 0


META = paths.CORPUS / "meta"
FINDING_PREFIX = "IC"


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
                                            db.vocabularies[schema.ROLE_SCOPE])

    print("%s from %s, %s refused"
          % (console.plural(len(kept), "candidate finding"),
             console.plural(len(documents), "paper"), console.plural(len(refused), "finding")))
    used = {link[keys.REF] for candidate in kept
            for link in candidate.record.get("sources") or []}
    print("  source entries needed: %d of %d papers; %s yielded no finding"
          % (len(used), len(entries), console.plural(len(entries) - len(used), "paper")))
    print("  links dropped because nothing resolved: %d" % len(dropped))

    if refused:
        print("\nfindings refused outright:")
        for item in refused:
            print("  %-13s %-46s %s" % (item.paper, item.finding[:46], item.why))

    if dropped:
        print("\nlinks dropped, by field:")
        tally = collections.Counter(item.field for item in dropped)
        for field, count in tally.most_common():
            print("  %-14s %d" % (field, count))

    if not write:
        print("\nnothing written; add --write to create the files")
        return 0

    needed = {link[keys.REF] for candidate in kept
              for link in candidate.record.get("sources") or []}
    written = 0
    for key, entry in sorted(entries.items()):
        if key in needed and key not in db.entities:
            with paths.REGISTRIES.joinpath("sources.yaml").open("a", encoding="utf-8") as handle:
                handle.write("\n" + yaml.safe_dump({key: entry}, allow_unicode=True,
                                                    sort_keys=False, width=98))
            written += 1
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


TAGS = paths.CORPUS / "tags"


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
    print("  the extraction prompt for a whole paper has a median of ~117000, so this is ~21x less")
    print("  scope: %s" % ("findings with no concept yet" if only_untagged else "every finding"))
    return 0


USAGE = """usage:
  python3 extract.py prompts [paper,paper]   corpus/text -> corpus/prompts, one per paper
  python3 extract.py collect <directory>     model answers -> corpus/answers, matched by content
                                             add file.txt=<paper> for answers with no findings
  python3 extract.py verify                  check every citation against its source PDF
  python3 extract.py propose [N]             new entities not in any registry, N papers or more
  python3 extract.py split [--write] [--force]  collected answers -> data/findings/, reports first;
                                             --write never overwrites, --force does"""


def main(argv):
    if len(argv) < 2:
        print(USAGE)
        return 2
    command, rest = argv[1], argv[2:]
    if command == "prompts":
        return write_prompts(rest[0] if rest else None)
    if command == "collect":
        if not rest:
            return fail("collect needs a directory")
        return collect(rest[0], dict(pair.split("=", 1) for pair in rest[1:] if "=" in pair))
    if command == "verify":
        return verify()
    if command == "tags":
        return tags(rest[0] if rest else None)
    if command == "split":
        return split("--write" in rest, "--force" in rest)
    if command == "propose":
        try:
            least = int(rest[0]) if rest else 1
        except ValueError:
            return fail("propose takes a whole number of papers, not %r" % rest[0])
        return propose(max(least, 1))
    print(USAGE)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
