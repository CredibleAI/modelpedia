import http.client
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from modelpedia import atomic
from modelpedia import paths
from modelpedia.build import database
from modelpedia.ingest import anchors as anchorlib
from modelpedia.ingest import citations
from modelpedia.ingest import link
from modelpedia.ingest import manifest as store
from modelpedia.ingest import openreview as api
from modelpedia.ingest import screen
from modelpedia.ingest import text as textutil

MANIFEST = paths.CORPUS / "manifest.jsonl"
META = paths.CORPUS / "meta"
PDFS = paths.CORPUS / "pdf"
TEXTS = paths.CORPUS / "text"

PDF_MAGIC = b"%PDF"
DELAY = 1.0
RETRIES = 3

DOWNLOAD_TIERS = (screen.STRONG, screen.POSSIBLE)
ALL_TIERS = (screen.STRONG, screen.POSSIBLE, screen.WEAK)
PDF_OPTIONS = ("--tier", "--limit", "--ids")

VENV_PYTHON = paths.ROOT / ".venv" / "bin" / "python"


def fail(message):
    print("ERROR %s" % message)
    return 1


def interpreter_hint():
    if VENV_PYTHON.exists() and Path(sys.executable) != VENV_PYTHON:
        return ("\n      it is installed in .venv, so run this as:"
                "\n      %s harvest.py ..." % VENV_PYTHON)
    return ""


def openreview_module():
    try:
        return api.module()
    except api.Unavailable as error:
        raise SystemExit(fail("%s%s" % (error, interpreter_hint())))


def connect():
    try:
        return api.connect()
    except api.Unavailable as error:
        raise SystemExit(fail("%s%s" % (error, interpreter_hint())))


def report(complaints, repeated):
    for complaint in complaints:
        print("  WARN %s" % complaint)
    if complaints:
        print("  WARN %d unusable line(s); those papers will be re-harvested" % len(complaints))
    if repeated:
        print("  WARN %d repeated id(s); the last row for each wins" % repeated)


def manifest_rows():
    held = store.load(MANIFEST)
    report(held.complaints, held.repeated)
    return list(held.rows)


def extractor_version():
    if textutil.available() is None:
        return "MISSING"
    return textutil.installed_version()


def doctor():
    openreview_module()
    missing = api.missing_methods()
    print("%-26s %s" % ("python", sys.executable))
    print("%-26s %s" % (api.PACKAGE, api.package_version()))
    print("%-26s %s" % ("API client contract", "ok" if not missing
                        else "MISSING " + ", ".join(missing)))
    print("%-26s %s" % (textutil.TOOL, extractor_version()))
    print("%-26s %s / %s" % ("credentials",
                             "set" if api.credentials_present() else "MISSING",
                             "set" if api.credentials_present() else "MISSING"))
    return 1 if missing or textutil.available() is None else 0


def harvest_meta(venue_id, accepted_only=True):
    connection = connect()
    terms = screen.registry_terms(database.load_registries())
    META.mkdir(parents=True, exist_ok=True)
    if store.close_unterminated_line(MANIFEST):
        print("  WARN %s did not end with a newline; an interrupted last row was closed off"
              % MANIFEST.name)
    already = store.ids_in(MANIFEST)

    counts = {screen.STRONG: 0, screen.POSSIBLE: 0, screen.WEAK: 0}
    added = skipped = refused = 0
    with MANIFEST.open("a", encoding="utf-8") as log:
        for note in api.submissions(connection, venue_id, accepted_only):
            if note.id in already:
                skipped += 1
                continue
            try:
                paper_id = store.safe_id(note.id)
            except ValueError as error:
                print("  WARN %s; that paper was left out" % error)
                refused += 1
                continue
            content = api.flat_content(note.content)
            screening = screen.screen(content.get("title"), content.get("abstract"),
                                      content.get("keywords") or [], terms)
            (META / ("%s.json" % paper_id)).write_text(
                json.dumps(note.to_json(), ensure_ascii=False, indent=1), encoding="utf-8")
            row = store.row_for(paper_id, content, venue_id, screening,
                                screen.RULES_VERSION, api.pdf_url(paper_id))
            log.write(json.dumps(row, ensure_ascii=False) + "\n")
            log.flush()
            counts[screening.tier] += 1
            added += 1

    print("%s: %d new, %d already held%s"
          % (venue_id, added, skipped,
             ", %d refused" % refused if refused else ""))
    for tier in ALL_TIERS:
        print("  %-9s %d" % (tier, counts[tier]))
    return 1 if refused else 0


def is_pdf(payload):
    return bool(payload) and payload.startswith(PDF_MAGIC)


def fetch_pdf(connection, paper_id, target, retries=RETRIES, delay=DELAY):
    payload = None
    for attempt in range(retries):
        try:
            payload = connection.get_attachment(field_name="pdf", id=paper_id)
            break
        except Exception as error:
            if not api.retryable(error) or attempt + 1 == retries:
                raise
            wait = delay * (2 ** attempt)
            print("  RETRY %s in %.1fs: %s" % (paper_id, wait, error))
            time.sleep(wait)
    if not is_pdf(payload):
        print("  SKIPPED %s: response is not a PDF (%d bytes)" % (paper_id, len(payload or b"")))
        return False
    atomic.write_bytes(target, payload)
    return True


def options(rest, allowed):
    given, index = {}, 0
    while index < len(rest):
        flag = rest[index]
        if flag not in allowed:
            raise ValueError("unknown option %r; expected one of %s" % (flag, ", ".join(allowed)))
        if index + 1 >= len(rest):
            raise ValueError("%s needs a value" % flag)
        given[flag] = rest[index + 1]
        index += 2
    return given


def chosen_tiers(value):
    if value is None:
        return DOWNLOAD_TIERS
    tiers = tuple(part.strip() for part in value.split(",") if part.strip())
    unknown = [tier for tier in tiers if tier not in ALL_TIERS]
    if not tiers or unknown:
        raise ValueError("--tier takes %s, not %r" % ("/".join(ALL_TIERS), value))
    return tiers


def positive(value):
    if value is None:
        return None
    if not value.isdigit() or int(value) < 1:
        raise ValueError("--limit takes a positive whole number, not %r" % value)
    return int(value)


def selected_rows(tiers, ids):
    rows, absent = store.selected_rows(manifest_rows(), tiers, ids)
    if absent:
        print("  WARN %d of %d requested ids are not in the manifest: %s"
              % (len(absent), len(ids), ", ".join(absent[:5])))
    return rows


def harvest_pdfs(tiers=DOWNLOAD_TIERS, limit=None, delay=DELAY, ids=None):
    PDFS.mkdir(parents=True, exist_ok=True)
    atomic.clear_partials(PDFS)
    wanted = selected_rows(tiers, ids)
    if ids is not None and not wanted:
        return fail("none of the %d requested ids is in the manifest" % len(ids))
    held, todo = [], []
    for row in wanted:
        if (PDFS / ("%s.pdf" % row["id"])).exists():
            held.append(row)
        else:
            todo.append(row)
    capped = len(todo) - limit if limit and len(todo) > limit else 0
    if capped:
        todo = todo[:limit]

    source = "from --ids" if ids is not None else "in tiers %s" % "/".join(tiers)
    print("%d %s, %d already on disk, fetching %d%s"
          % (len(wanted), source, len(held), len(todo),
             ", %d held back by --limit" % capped if capped else ""))
    if not todo:
        return 0

    connection = connect()
    got = failed = 0
    for number, row in enumerate(todo, start=1):
        try:
            if fetch_pdf(connection, row["id"], PDFS / ("%s.pdf" % row["id"])):
                got += 1
            else:
                failed += 1
        except Exception as error:
            failed += 1
            print("  FAILED %s: %s" % (row["id"], error))
        if number % 50 == 0:
            print("  %d/%d" % (number, len(todo)))
        time.sleep(delay)
    print("downloaded %d of %d, %d failed" % (got, len(todo), failed))
    return 1 if failed else 0


def harvest_text():
    TEXTS.mkdir(parents=True, exist_ok=True)
    done = failed = 0
    for pdf in sorted(PDFS.glob("*.pdf")):
        target = TEXTS / ("%s.txt" % pdf.stem)
        if target.exists():
            continue
        try:
            atomic.write_text(target, textutil.read_pdf(pdf))
            done += 1
        except (OSError, textutil.MissingTool) as error:
            print("  FAILED %s: %s" % (pdf.name, error))
            failed += 1
    print("extracted %d, failed %d, total on disk %d"
          % (done, failed, len(list(TEXTS.glob("*.txt")))))
    return 1 if failed else 0


def show_stats():
    rows = manifest_rows()
    if not rows:
        print("corpus/manifest.jsonl is empty; run: python3 harvest.py meta <venue_id>")
        return 0
    by_venue, by_rules = {}, {}
    for row in rows:
        by_venue.setdefault(row["venue"], {}).setdefault(row["tier"], 0)
        by_venue[row["venue"]][row["tier"]] += 1
        version = row.get("rules_version") or screen.UNKNOWN_VERSION
        by_rules[version] = by_rules.get(version, 0) + 1

    print("%-44s %8s %9s %6s %7s" % ("venue", "strong", "possible", "weak", "total"))
    totals = {tier: 0 for tier in ALL_TIERS}
    for venue, tiers in sorted(by_venue.items()):
        for tier in ALL_TIERS:
            totals[tier] += tiers.get(tier, 0)
        print("%-44s %8d %9d %6d %7d"
              % (venue[:44], tiers.get(screen.STRONG, 0), tiers.get(screen.POSSIBLE, 0),
                 tiers.get(screen.WEAK, 0), sum(tiers.values())))
    print("%-44s %8d %9d %6d %7d"
          % ("ALL", totals[screen.STRONG], totals[screen.POSSIBLE], totals[screen.WEAK],
             len(rows)))

    print("\npdf on disk: %d, text on disk: %d"
          % (len(list(PDFS.glob("*.pdf"))) if PDFS.exists() else 0,
             len(list(TEXTS.glob("*.txt"))) if TEXTS.exists() else 0))

    print("screening rules in use now: %s" % screen.RULES_VERSION)
    for version, count in sorted(by_rules.items(), key=lambda pair: -pair[1]):
        print("  %-14s %d row(s)%s"
              % (version, count, "" if version == screen.RULES_VERSION else "  <- stale"))
    stale = sum(count for version, count in by_rules.items() if version != screen.RULES_VERSION)
    if stale:
        print("  %d row(s) carry a tier computed by rules that are no longer the ones in "
              "screen.py; their tier and signals were never recomputed" % stale)
    return 0


def preflight(venue_id=None):
    openreview = openreview_module()
    if doctor():
        return 1
    connection = connect()
    print("%-26s %s" % ("login", "ok"))

    if not venue_id:
        print("\npass a venue id to check it, e.g. ICML.cc/2026/Conference")
        return 0

    try:
        connection.get_group(venue_id)
        print("%-26s %s" % ("venue group", "found"))
    except openreview.OpenReviewException as error:
        print("%-26s NOT FOUND: %s" % ("venue group", error))
        print("\nrun: harvest.py venues %s" % venue_id.split(".")[0])
        return 1

    invitation = api.submission_invitation(connection, venue_id)
    try:
        connection.get_invitation(invitation)
        print("%-26s %s" % ("submission invitation", invitation))
    except openreview.OpenReviewException as error:
        invitation = None
        print("%-26s UNAVAILABLE: %s" % ("submission invitation", error))

    checks = [("accepted (venueid)", {"content": {"venueid": venue_id}}, True)]
    if invitation:
        checks.append(("all submissions", {"invitation": invitation}, False))

    blocking = False
    for label, query, required in checks:
        try:
            notes, count = connection.get_notes(limit=1, with_count=True, **query)
            print("%-26s %s" % (label, count))
            if notes:
                content = api.flat_content(notes[0].content)
                missing = [field for field in ("title", "abstract", "pdf")
                           if not content.get(field)]
                print("%-26s %s" % (label + " sample",
                                    "ok" if not missing else "MISSING " + ", ".join(missing)))
                blocking = blocking or bool(missing) and required
        except openreview.OpenReviewException as error:
            print("%-26s unavailable: %s" % (label, error))
            blocking = blocking or required

    if blocking:
        print("\nnot ready: fix the checks above before harvesting")
        return 1
    print("\nready: harvest.py meta %s" % venue_id)
    if not invitation:
        print("--all is not available for this venue; accepted-only harvesting is unaffected")
    return 0


def list_venues(needle=None):
    for venue in sorted(connect().get_group("venues").members):
        if not needle or needle.lower() in venue.lower():
            print(venue)
    return 0


DBLP = "https://dblp.org/search/publ/api"
DBLP_ROWS = 6
DBLP_DELAY = 3.0
DBLP_RETRIES = 5
DBLP_AGENT = "modelpedia-anchor-lookup"

CROSSREF = "https://api.crossref.org/works"
CROSSREF_ROWS = 3
CROSSREF_DELAY = 1.0
NETWORK = (urllib.error.HTTPError, urllib.error.URLError, http.client.HTTPException,
           ConnectionError, TimeoutError, ValueError, OSError)

ENTITIES = paths.CORPUS / "entities.jsonl"
PROPOSALS = paths.CORPUS / "anchor_proposals.jsonl"


class LookupFailed(Exception):
    pass


def ask_dblp(query, rows=DBLP_ROWS):
    """Raises rather than returning nothing: a failed request must never read as 'no match'."""
    address = "%s?%s" % (DBLP, urllib.parse.urlencode(
        {"q": query, "format": "json", "h": rows}))
    request = urllib.request.Request(address, headers={"User-Agent": DBLP_AGENT})
    last = None
    for attempt in range(DBLP_RETRIES):
        time.sleep(DBLP_DELAY if attempt == 0 else 2 ** attempt * DBLP_DELAY)
        try:
            with urllib.request.urlopen(request, timeout=40) as response:
                payload = json.loads(response.read().decode("utf-8"))
            hits = payload.get("result", {}).get("hits", {}).get("hit", [])
            return [hit.get("info", {}) for hit in hits if isinstance(hit, dict)]
        except NETWORK as error:
            last = error
    raise LookupFailed("%s: %s" % (query[:48], last))


def ask_crossref(citation, rows=CROSSREF_ROWS):
    """Crossref matches a whole reference string; DBLP does not index anything but computing."""
    address = "%s?%s" % (CROSSREF, urllib.parse.urlencode(
        {"query.bibliographic": anchorlib.bibliographic(citation), "rows": rows,
         "select": "DOI,title"}))
    request = urllib.request.Request(address, headers={"User-Agent": DBLP_AGENT})
    last = None
    for attempt in range(DBLP_RETRIES):
        time.sleep(CROSSREF_DELAY if attempt == 0 else 2 ** attempt * CROSSREF_DELAY)
        try:
            with urllib.request.urlopen(request, timeout=40) as response:
                payload = json.loads(response.read().decode("utf-8"))
            return payload.get("message", {}).get("items", [])
        except NETWORK as error:
            last = error
    raise LookupFailed("crossref: %s" % last)


def crossref_match(citation):
    best = (0.0, "", "")
    for item in ask_crossref(citation):
        title = " ".join(item.get("title") or [])
        score = anchorlib.match_score(title, citation)
        if score > best[0]:
            best = (score, title, anchorlib.doi_url(item.get("DOI")))
    return best


def links_of(record):
    found = record.get("ee")
    if isinstance(found, list):
        return [str(item) for item in found]
    return [str(found)] if found else []


def dblp_match(citation):
    best, answered = (0.0, "", ""), False
    for query in anchorlib.queries(citation):
        try:
            records = ask_dblp(query)
            answered = True
        except LookupFailed:
            continue
        for record in records:
            title = str(record.get("title") or "")
            score = anchorlib.match_score(title, citation)
            if score > best[0]:
                best = (score, title, anchorlib.url_from(links_of(record)))
    if not answered:
        raise LookupFailed("every query for this citation failed")
    return best


def resolved(citation):
    """(index, score, title, url). DBLP first; Crossref covers what it does not index."""
    failures = []
    try:
        score, title, url = dblp_match(citation)
        if url and score >= anchorlib.DBLP_MATCH_AT:
            return "dblp", score, title, url
    except LookupFailed as error:
        failures.append(str(error))
        score, title, url = 0.0, "", ""
    try:
        other, other_title, other_url = crossref_match(citation)
        if other_url and other >= anchorlib.CROSSREF_MATCH_AT:
            return "crossref", other, other_title, other_url
    except LookupFailed as error:
        failures.append(str(error))
        other, other_title, other_url = 0.0, "", ""
    if len(failures) == 2:
        raise LookupFailed("; ".join(failures))
    if other > score:
        return "crossref", other, other_title, other_url
    return "dblp", score, title, url


def confirmed_citations(entities, wanted):
    found = {}
    if not ENTITIES.exists():
        return found
    index = link.index_of(entities)
    for line in ENTITIES.read_text(encoding="utf-8").splitlines():
        row = json.loads(line)
        if row.get("state") != citations.CONFIRMED or not (row.get("citation") or "").strip():
            continue
        hit = link.resolve(str(row.get("name") or ""), index)
        if hit.kind != link.HIT or hit.slug not in wanted:
            continue
        if len(row["citation"]) > len(found.get(hit.slug, "")):
            found[hit.slug] = row["citation"]
    return found


def propose_anchors():
    db = database.load()
    wanted = set(anchorlib.missing_anchor(db.entities))
    citations_by_key = confirmed_citations(db.entities, wanted)
    print("%d entities have no anchor, %d of them carry a confirmed citation"
          % (len(wanted), len(citations_by_key)))
    if not citations_by_key:
        return 0

    taken, weak, silent, failed = [], 0, 0, 0
    for key in sorted(citations_by_key):
        citation = citations_by_key[key]
        try:
            index, score, title, url = resolved(citation)
        except LookupFailed as error:
            failed += 1
            print("  request failed  %-42s %s" % (key, error))
            continue
        if not url:
            silent += 1
            continue
        if score < min(anchorlib.DBLP_MATCH_AT, anchorlib.CROSSREF_MATCH_AT):
            weak += 1
            continue
        taken.append({"key": key, "anchor": url, "score": round(score, 3),
                      "title": title, "index": index})
        print("  %-42s %.2f %-9s %s" % (key, score, index, url))

    atomic.write_text(PROPOSALS, "".join(json.dumps(row) + "\n" for row in taken))
    print("\n%d proposed, %d below threshold, %d with no hit, %d requests failed"
          % (len(taken), weak, silent, failed))
    print("written to %s -- these are candidates, not results: a matched title proves the URL fits"
          % PROPOSALS)
    print("the citation, not that the citation belongs to the entity. Review before writing.")
    return 1 if failed else 0


USAGE = """usage: run these with .venv/bin/python -- openreview-py lives there, not in system python

  .venv/bin/python harvest.py doctor                 offline dependency and API contract checks
  .venv/bin/python harvest.py preflight [venue_id]   check python, package, login and venue
  .venv/bin/python harvest.py venues [substring]     list venue identifiers
  .venv/bin/python harvest.py meta <venue_id> [--all]  metadata only, screened, resumable
  .venv/bin/python harvest.py stats                  tier breakdown of the manifest
  .venv/bin/python harvest.py pdfs [--tier a,b] [--limit N] [--ids FILE]
                                                     --ids overrides --tier; one id per line
  .venv/bin/python harvest.py text                   pypdfium2 over downloaded pdfs
  .venv/bin/python harvest.py anchors                DBLP lookup for entities with no anchor,
                                                     proposals only, never writes a registry

  OPENREVIEW_USERNAME and OPENREVIEW_PASSWORD must be set in the environment."""


def main(argv):
    if len(argv) < 2:
        print(USAGE)
        return 2
    command, rest = argv[1], argv[2:]

    if command == "doctor":
        return doctor()
    if command == "preflight":
        return preflight(rest[0] if rest else None)
    if command == "venues":
        return list_venues(rest[0] if rest else None)
    if command == "meta":
        if not rest:
            return fail("meta needs a venue id")
        return harvest_meta(rest[0], accepted_only="--all" not in rest)
    if command == "stats":
        return show_stats()
    if command == "pdfs":
        try:
            given = options(rest, PDF_OPTIONS)
            tiers = chosen_tiers(given.get("--tier"))
            limit = positive(given.get("--limit"))
            ids = store.read_ids(Path(given["--ids"])) if "--ids" in given else None
        except ValueError as error:
            return fail(str(error))
        return harvest_pdfs(tiers, limit, ids=ids)
    if command == "text":
        return harvest_text()
    if command == "anchors":
        return propose_anchors()

    print(USAGE)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
