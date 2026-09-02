import collections
import http.client
import json
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from modelpedia import atomic
from modelpedia import cli
from modelpedia import graph as graph_json
from modelpedia import paths
from modelpedia.build import database
from modelpedia.ingest import anchors as anchorlib
from modelpedia.ingest import batching
from modelpedia.ingest import manifest as store
from modelpedia.ingest import openreview as api
from modelpedia.ingest import ranking
from modelpedia.ingest import registries
from modelpedia.ingest import screen
from modelpedia.ingest import text as textutil

MANIFEST = paths.MANIFEST
META = paths.META
PDFS = paths.PDFS
TEXTS = paths.TEXTS
REVIEWS = paths.REVIEWS
RANKING = paths.RANKING

PDF_MAGIC = b"%PDF"
DELAY = 1.0
REVIEW_DELAY = 0.3
RETRIES = 3

DOWNLOAD_TIERS = (screen.STRONG, screen.POSSIBLE)
ALL_TIERS = (screen.STRONG, screen.POSSIBLE, screen.WEAK)
PDF_OPTIONS = ("--tier", "--limit", "--ids", "--pause", "--venue")
REVIEW_OPTIONS = ("--limit", "--from", "--pause")
RANK_OPTIONS = ("--out", "--venue")
ANCHOR_OPTIONS = ("--at",)
ANCHOR_FLAGS = ("--write",)

IMPORT_PAPER_KEYS = ("forum", "id")
IMPORT_REVIEW_KEYS = ("review_id", "id")

FORUM_IN_ANCHOR = re.compile(r"[?&]id=([A-Za-z0-9]+)")

def interpreter_hint():
    return "\n      openreview-py is an optional extra; install it with: uv sync --extra ingest"


def openreview_module():
    try:
        return api.module()
    except api.Unavailable as error:
        raise SystemExit(fail("%s%s" % (error, interpreter_hint())))


def connect(generation=api.API2):
    try:
        return api.client_for(generation)
    except api.Unavailable as error:
        raise SystemExit(fail("%s%s" % (error, interpreter_hint())))


def source_for(venue_id):
    connection = connect()
    generation = api.generation_of(connection, venue_id)
    if generation == api.API1:
        connection = connect(api.API1)
    print("%s answers on API %s" % (venue_id, generation))
    return connection, generation


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
    connection, generation = source_for(venue_id)
    META.mkdir(parents=True, exist_ok=True)
    if store.close_unterminated_line(MANIFEST):
        print("  WARN %s did not end with a newline; an interrupted last row was closed off"
              % MANIFEST.name)
    already = store.ids_in(MANIFEST)

    counts = {screen.STRONG: 0, screen.POSSIBLE: 0, screen.WEAK: 0}
    added = skipped = refused = 0
    with MANIFEST.open("a", encoding="utf-8") as log:
        for note in api.submissions(connection, venue_id, accepted_only, generation):
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
                                      content.get("keywords") or [])
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
    print("these tiers stand on the abstract alone; the review half of the score is still zero.\n"
          "run: modelpedia harvest reviews %s, then modelpedia harvest rescreen" % venue_id)
    return 1 if refused else 0


def harvest_reviews(venue_id, limit=None, delay=REVIEW_DELAY, pause=None):
    wanted = [row for row in manifest_rows() if row["venue"] == venue_id]
    if not wanted:
        return fail("no paper from %s is in the manifest; run: modelpedia harvest meta %s"
                    % (venue_id, venue_id))
    REVIEWS.mkdir(parents=True, exist_ok=True)
    target = REVIEWS / store.store_name(venue_id)
    if store.close_unterminated_line(target):
        print("  WARN %s did not end with a newline; an interrupted last row was closed off"
              % target.name)

    already = store.reviewed_ids(target)
    outstanding = [row for row in wanted if row["id"] not in already]
    print("%d papers from %s, %d already answered for, %d to fetch"
          % (len(wanted), venue_id, len(already), len(outstanding)))
    if not outstanding:
        return 0
    connection, _ = source_for(venue_id)

    def one_review(row):
        written = 0
        with target.open("a", encoding="utf-8") as log:
            for note in api.reviews_of(connection, row["id"]):
                fields = api.prose_fields(note.content)
                if not fields:
                    continue
                log.write(json.dumps(store.review_row(
                    row["id"], venue_id, str(note.id), api.rating_of(note.content), fields),
                    ensure_ascii=False) + "\n")
                written += 1
            if not written:
                log.write(json.dumps(store.review_row(
                    row["id"], venue_id, "%s-none" % row["id"], None, {})) + "\n")
        return "got" if written else "empty"

    outcome = batching.fetched(outstanding, one_review, "papers", delay, limit, pause,
                      "what was answered for is on disk")
    if outcome is None:
        return 1
    totals, outstanding, gave_up = outcome

    print("reviews for %d papers, %d papers have none, %d requests failed"
          % (totals["got"], totals["empty"], totals["failed"]))
    batching.still_to_fetch(outstanding)
    print("run modelpedia harvest rescreen to fold them into the manifest")
    return 1 if gave_up or totals["failed"] else 0


def first_of(row, keys):
    for key in keys:
        if row.get(key):
            return str(row[key])
    return ""


def import_reviews(venue_id, source):
    held = {row["id"] for row in manifest_rows() if row["venue"] == venue_id}
    if not held:
        return fail("no paper from %s is in the manifest" % venue_id)
    REVIEWS.mkdir(parents=True, exist_ok=True)
    target = REVIEWS / store.store_name(venue_id)
    if target.exists():
        return fail("%s already exists; move it aside before importing over it" % target)

    taken = outside = unusable = 0
    with source.open(encoding="utf-8") as handle, target.open("w", encoding="utf-8") as log:
        for number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                row = json.loads(line)
            except json.JSONDecodeError:
                unusable += 1
                continue
            paper_id = first_of(row, IMPORT_PAPER_KEYS)
            if paper_id not in held:
                outside += 1
                continue
            fields = api.prose_fields(row)
            if not fields:
                unusable += 1
                continue
            review_id = first_of(row, IMPORT_REVIEW_KEYS) or "%s-line%d" % (paper_id, number)
            log.write(json.dumps(store.review_row(
                paper_id, venue_id, review_id, api.rating_of(row), fields),
                ensure_ascii=False) + "\n")
            taken += 1

    print("imported %d reviews into %s" % (taken, target.name))
    print("  %d belong to papers outside %s, %d carried no review prose" % (outside, venue_id,
                                                                            unusable))
    print("run modelpedia harvest rescreen to fold them into the manifest")
    return 0 if taken else 1


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


fail = cli.fail
positive = cli.positive


def options(rest, allowed, flags=()):
    return cli.options(rest, allowed, flags)


def chosen_tiers(value):
    return cli.comma_list(value, ALL_TIERS, "--tier", default=DOWNLOAD_TIERS)


def number_between(value, flag, low=0.0, high=1.0):
    return cli.number(value, flag, low=low, high=high)


def selected_rows(tiers, ids, venue_id=None):
    held = manifest_rows()
    if venue_id:
        venues = sorted({row["venue"] for row in held})
        if venue_id not in venues:
            raise ValueError("no paper from %r is in the manifest; it holds %s"
                             % (venue_id, ", ".join(venues)))
        held = [row for row in held if row["venue"] == venue_id]
    rows, absent = store.selected_rows(held, tiers, ids)
    if absent:
        print("  WARN %d of %d requested ids are not in the manifest%s: %s"
              % (len(absent), len(ids), " under %s" % venue_id if venue_id else "",
                 ", ".join(absent[:5])))
    return rows


def harvest_pdfs(tiers=DOWNLOAD_TIERS, limit=None, delay=DELAY, ids=None, pause=None,
                 venue_id=None):
    PDFS.mkdir(parents=True, exist_ok=True)
    atomic.clear_partials(PDFS)
    try:
        wanted = selected_rows(tiers, ids, venue_id)
    except ValueError as error:
        return fail(str(error))
    if ids is not None and not wanted:
        return fail("none of the %d requested ids is in the manifest" % len(ids))
    held, outstanding = [], []
    for row in wanted:
        (held if (PDFS / ("%s.pdf" % row["id"])).exists() else outstanding).append(row)

    source = "from --ids" if ids is not None else "in tiers %s" % "/".join(tiers)
    print("%d %s%s, %d already on disk, %d to fetch"
          % (len(wanted), source, " from %s" % venue_id if venue_id else "",
             len(held), len(outstanding)))
    if not outstanding:
        return 0
    clients = {}

    def client_for(row):
        venue = row["venue"]
        if venue not in clients:
            clients[venue] = source_for(venue)[0]
        return clients[venue]

    def one_pdf(row):
        return "got" if fetch_pdf(client_for(row), row["id"], PDFS / ("%s.pdf" % row["id"])) \
            else "refused"

    outcome = batching.fetched(outstanding, one_pdf, "pdfs", delay, limit, pause,
                      "what is on disk stays", every=50)
    if outcome is None:
        return 1
    totals, outstanding, gave_up = outcome

    print("downloaded %d, %d answered with something that is not a PDF, %d requests failed"
          % (totals["got"], totals["refused"], totals["failed"]))
    batching.still_to_fetch(outstanding)
    return 1 if gave_up or totals["failed"] or totals["refused"] else 0


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


def held_reviews():
    held = store.load_reviews(REVIEWS)
    for complaint in held.complaints[:5]:
        print("  WARN %s" % complaint)
    if len(held.complaints) > 5:
        print("  WARN %d more unusable review line(s)" % (len(held.complaints) - 5))
    return held


def rescreen():
    rows = manifest_rows()
    if not rows:
        return fail("%s is empty; run: modelpedia harvest meta <venue_id>" % MANIFEST.name)
    held = held_reviews()

    moved, unreviewed, fresh = collections.Counter(), 0, []
    for row in rows:
        total = ranking.screened(row, held)
        moved[(row.get("tier") or screen.UNKNOWN_VERSION, total.tier)] += 1
        count = held.count(row["id"])
        unreviewed += not count
        fresh.append(store.rescored(row, total, screen.RULES_VERSION,
                                    reviews=count, rating=held.rating(row["id"])))
    atomic.write_text(MANIFEST, store.dump(fresh))

    print("rescreened %d row(s) under rules %s" % (len(fresh), screen.RULES_VERSION))
    for (before, after), count in sorted(moved.items(), key=lambda pair: -pair[1]):
        print("  %-9s -> %-9s %6d%s" % (before, after, count, "" if before != after else "  (held)"))
    if unreviewed:
        print("  %d row(s) carry no review, so their total is the abstract half alone and their\n"
              "  tier is not comparable with the rest; run modelpedia harvest reviews for their venue"
              % unreviewed)
    return 0


def findings_per_forum():
    forum_of = {}
    for slug, entity in database.load_registries().items():
        if entity.get("type") != graph_json.SOURCE:
            continue
        found = FORUM_IN_ANCHOR.search(entity.get("anchor") or "")
        if found:
            forum_of[slug] = found.group(1)
    counted = collections.Counter()
    for finding in database.load_findings().values():
        for source in finding.get("sources") or []:
            forum = forum_of.get(source.get("ref"))
            if forum:
                counted[forum] += 1
    return counted


def band_table(ranked):
    print(ranking.band_table(ranked))


def show_stats():
    rows = manifest_rows()
    if not rows:
        print("corpus/manifest.jsonl is empty; run: modelpedia harvest meta <venue_id>")
        return 0
    by_rules = collections.Counter(row.get("rules_version") or screen.UNKNOWN_VERSION
                                   for row in rows)
    print(ranking.tier_table(rows, ALL_TIERS))
    print("\npdf on disk: %d, text on disk: %d"
          % (len(list(PDFS.glob("*.pdf"))) if PDFS.exists() else 0,
             len(list(TEXTS.glob("*.txt"))) if TEXTS.exists() else 0))
    print(ranking.rules_in_use(by_rules, screen.RULES_VERSION, screen.UNKNOWN_VERSION))
    return 0


def write_ranking(target, ranked):
    atomic.write_text(target, ranking.as_csv(ranked, ranking.RANK_COLUMNS))
    print("  %-56s %d row(s)" % (target, len(ranked)))


def rank(target=RANKING, venue_id=None):
    rows = manifest_rows()
    if not rows:
        return fail("%s is empty; run: modelpedia harvest meta <venue_id>" % MANIFEST.name)
    venues = sorted({row["venue"] for row in rows})
    if venue_id and venue_id not in venues:
        return fail("no paper from %r is in the manifest; it holds %s"
                    % (venue_id, ", ".join(venues)))
    if venue_id:
        rows = [row for row in rows if row["venue"] == venue_id]

    held = held_reviews()
    ranked = ranking.ranked_rows(rows, held, findings_per_forum())

    target.parent.mkdir(parents=True, exist_ok=True)
    write_ranking(target, ranked)
    if not venue_id and len(venues) > 1:
        for one in venues:
            write_ranking(ranking.venue_target(target, one),
                          [entry for entry in ranked if entry["venue"] == one])
    print()

    band_table(ranked)
    return 0


def sample_submission(connection, venue_id, generation):
    if generation == api.API1:
        notes = connection.get_notes(invitation=api.BLIND_SUBMISSION % venue_id, limit=25)
        accepted = [note for note in notes if api.accepted(note)]
        return "accepted (venue field)", accepted[:1], "%d of the first %d" % (len(accepted),
                                                                              len(notes))
    notes, count = connection.get_notes(content={"venueid": venue_id}, limit=1, with_count=True)
    return "accepted (venueid)", notes, count


def review_reach(connection, note):
    reviews = api.reviews_of(connection, note.id)
    if not reviews:
        return "no %s note in the sample forum" % api.REVIEW_INVITATION, False
    fields = api.prose_fields(reviews[0].content)
    if not fields:
        return "%d found, none carries prose this reader keeps" % len(reviews), False
    return "%d found, prose fields: %s" % (len(reviews), ", ".join(sorted(fields))), True


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
        generation = api.generation_of(connection, venue_id)
    except openreview.OpenReviewException as error:
        print("%-26s unavailable: %s" % ("venue probe", error))
        return 1
    print("%-26s %s" % ("API generation", generation))
    if generation == api.API1:
        connection = connect(api.API1)

    try:
        label, notes, count = sample_submission(connection, venue_id, generation)
    except openreview.OpenReviewException as error:
        print("%-26s unavailable: %s" % ("submissions", error))
        print("\nrun: modelpedia harvest venues %s" % venue_id.split(".")[0])
        return 1
    print("%-26s %s" % (label, count))
    if not notes:
        print("\nnot ready: this venue returns no accepted paper")
        return 1

    content = api.flat_content(notes[0].content)
    missing = [field for field in ("title", "abstract", "pdf") if not content.get(field)]
    print("%-26s %s" % ("sample metadata",
                        "ok" if not missing else "MISSING " + ", ".join(missing)))
    try:
        message, reachable = review_reach(connection, notes[0])
    except Exception as error:
        message, reachable = "unavailable: %s" % error, False
    print("%-26s %s" % ("sample reviews", message))

    if generation == api.API2:
        try:
            print("%-26s %s (report only, the fetch still walks the forums)"
                  % ("venue-wide review query", api.venue_review_count(connection, venue_id)))
        except Exception as error:
            print("%-26s unavailable: %s" % ("venue-wide review query", error))

    if missing:
        print("\nnot ready: fix the checks above before harvesting")
        return 1
    print("\nready: modelpedia harvest meta %s" % venue_id)
    if not reachable:
        print("reviews are not reachable for the sampled paper, so the review half of the score\n"
              "would stay zero for this venue; check that before running modelpedia harvest reviews")
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

ENTITIES = paths.ENTITY_REPORT
PROPOSALS = paths.ANCHOR_PROPOSALS


def ask_dblp(query, rows=DBLP_ROWS):
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
    raise anchorlib.LookupFailed("%s: %s" % (query[:48], last))


def ask_crossref(citation, rows=CROSSREF_ROWS):
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
    raise anchorlib.LookupFailed("crossref: %s" % last)


def apply_anchors(taken, entities, write_at):
    strong = [row for row in taken if row["score"] >= write_at]
    wrote, refused = 0, []
    for row in sorted(strong, key=lambda item: item["key"]):
        field = registries.ALIAS_FILE.get(entities[row["key"]].get("type"))
        if field and registries.set_anchor(field, row["key"], row["anchor"]):
            wrote += 1
        else:
            refused.append(row["key"])
    print("\n%d of %d proposals reach %.2f; %d anchors written"
          % (len(strong), len(taken), write_at, wrote))
    if refused:
        print("  %d left alone (already anchored, or not a registry file): %s"
              % (len(refused), ", ".join(refused[:4])))
    print("%d proposals below the threshold stay in %s for review"
          % (len(taken) - len(strong), PROPOSALS))
    return 0


def propose_anchors(write_at=None):
    db = database.load()
    wanted = set(anchorlib.missing_anchor(db.entities))
    citations_by_key = anchorlib.confirmed_citations(ENTITIES, db.entities, wanted)
    print("%d entities have no anchor, %d of them carry a confirmed citation"
          % (len(wanted), len(citations_by_key)))
    if not citations_by_key:
        return 0

    taken, weak, silent, failed = [], 0, 0, 0
    for key in sorted(citations_by_key):
        citation = citations_by_key[key]
        try:
            index, score, title, url = anchorlib.resolved(citation, ask_dblp, ask_crossref)
        except anchorlib.LookupFailed as error:
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
    if write_at is not None:
        return apply_anchors(taken, db.entities, write_at)
    print("\n%d proposed, %d below threshold, %d with no hit, %d requests failed"
          % (len(taken), weak, silent, failed))
    print("written to %s -- these are candidates, not results: a matched title proves the URL fits"
          % PROPOSALS)
    print("the citation, not that the citation belongs to the entity. Review before writing.")
    return 1 if failed else 0


BATCH_NEEDS_LIMIT = ("--pause needs --limit: the pause is what separates one batch from the "
                     "next, and with no batch size there is only ever one batch")


def run_doctor(rest):
    return doctor()


def run_preflight(rest):
    return preflight(rest[0] if rest else None)


def run_venues(rest):
    return list_venues(rest[0] if rest else None)


def run_meta(rest):
    if not rest:
        return fail("meta needs a venue id")
    return harvest_meta(rest[0], accepted_only="--all" not in rest)


def run_reviews(rest):
    if not rest:
        return fail("reviews needs a venue id")
    try:
        given = options(rest[1:], REVIEW_OPTIONS)
        limit = positive(given.get("--limit"))
        pause = positive(given.get("--pause"), "--pause")
    except ValueError as error:
        return fail(str(error))
    if "--from" in given:
        source = Path(given["--from"])
        if not source.exists():
            return fail("cannot read %s" % source)
        return import_reviews(rest[0], source)
    if pause and not limit:
        return fail(BATCH_NEEDS_LIMIT)
    return harvest_reviews(rest[0], limit, pause=pause)


def run_rescreen(rest):
    return rescreen()


def run_rank(rest):
    try:
        given = options(rest, RANK_OPTIONS)
    except ValueError as error:
        return fail(str(error))
    return rank(Path(given["--out"]) if "--out" in given else RANKING, given.get("--venue"))


def run_stats(rest):
    return show_stats()


def run_pdfs(rest):
    try:
        given = options(rest, PDF_OPTIONS)
        tiers = chosen_tiers(given.get("--tier"))
        limit = positive(given.get("--limit"))
        pause = positive(given.get("--pause"), "--pause")
        ids = store.read_ids(Path(given["--ids"])) if "--ids" in given else None
    except ValueError as error:
        return fail(str(error))
    if pause and not limit:
        return fail(BATCH_NEEDS_LIMIT)
    return harvest_pdfs(tiers, limit, ids=ids, pause=pause, venue_id=given.get("--venue"))


def run_text(rest):
    return harvest_text()


def run_anchors(rest):
    try:
        given = options(rest, ANCHOR_OPTIONS, ANCHOR_FLAGS)
        at = number_between(given.get("--at"), "--at") if "--at" in given else 1.0
    except ValueError as error:
        return fail(str(error))
    return propose_anchors(at if given.get("--write") else None)


COMMANDS = (
    cli.Command("doctor", run_doctor,
                note="offline dependency and API contract checks"),
    cli.Command("preflight", run_preflight, "preflight [venue_id]",
                "check python, package, login and venue"),
    cli.Command("venues", run_venues, "venues [substring]",
                "list venue identifiers"),
    cli.Command("meta", run_meta, "meta <venue_id> [--all]",
                "metadata only, screened, resumable"),
    cli.Command("reviews", run_reviews,
                "reviews <venue_id> [--limit N] [--pause S] [--from FILE]",
                """official reviews, one request per paper,
                   resumable; --limit N --pause S fetches N,
                   waits S seconds and carries on by itself
                   until the venue is done; --from imports a
                   dump instead of asking the API"""),
    cli.Command("rescreen", run_rescreen,
                note="recompute every score from what is on disk"),
    cli.Command("rank", run_rank, "rank [--out FILE] [--venue ID]",
                """one table for every venue plus one per venue
                   beside it; --venue narrows it to one"""),
    cli.Command("stats", run_stats,
                note="tier breakdown of the manifest"),
    cli.Command("pdfs", run_pdfs,
                "pdfs [--tier a,b] [--venue ID] [--limit N] [--pause S] [--ids FILE]",
                """--ids overrides --tier; one id per line.
                   --limit N --pause S batches it the same way
                   reviews does, for the same quota"""),
    cli.Command("text", run_text,
                note="pypdfium2 over downloaded pdfs"),
    cli.Command("anchors", run_anchors, "anchors [--write] [--at S]",
                """DBLP then Crossref for entities with no
                   anchor; proposals only unless --write, which
                   applies those scoring --at or above (1.00)"""),
)

USAGE = cli.usage_text(
    COMMANDS, "modelpedia harvest",
    header="OPENREVIEW_USERNAME and OPENREVIEW_PASSWORD must be set in the environment.\n",
    footer="  a venue runs meta -> reviews -> rescreen -> rank. Only the first two touch the "
           "network.")


main = cli.runner(COMMANDS, USAGE)

