import json
import os
import re
import shutil
import sys
import time
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from modelpedia.build import database
from modelpedia.ingest import screen
from modelpedia import paths
from modelpedia.ingest import text as textutil

MANIFEST = paths.CORPUS / "manifest.jsonl"
META = paths.CORPUS / "meta"
PDFS = paths.CORPUS / "pdf"
TEXTS = paths.CORPUS / "text"

BASEURL = "https://api2.openreview.net"
PDF_URL = "https://openreview.net/pdf?id=%s"
PDF_MAGIC = b"%PDF"
SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
DELAY = 1.0
RETRIES = 3

USERNAME_ENV = "OPENREVIEW_USERNAME"
PASSWORD_ENV = "OPENREVIEW_PASSWORD"
CLIENT_METHODS = ("get_all_notes", "get_notes", "get_group", "get_invitation",
                  "get_attachment")

DOWNLOAD_TIERS = (screen.STRONG, screen.POSSIBLE)


def fail(message):
    print("ERROR %s" % message)
    return 1


VENV_PYTHON = paths.ROOT / ".venv" / "bin" / "python"


def import_openreview():
    try:
        import openreview
        return openreview
    except ImportError:
        hint = ""
        if VENV_PYTHON.exists() and Path(sys.executable) != VENV_PYTHON:
            hint = ("\n      it is installed in .venv, so run this as:"
                    "\n      %s harvest.py ..." % VENV_PYTHON)
        raise SystemExit(fail("openreview-py is not importable by %s%s"
                              % (sys.executable, hint)))


def credentials():
    missing = [name for name in (USERNAME_ENV, PASSWORD_ENV) if not os.environ.get(name)]
    if missing:
        raise SystemExit(fail("set %s in the environment; never put them in the repo"
                              % " and ".join(missing)))
    return os.environ[USERNAME_ENV], os.environ[PASSWORD_ENV]


def package_version():
    try:
        return version("openreview-py")
    except PackageNotFoundError:
        return "unknown"


def client_contract(openreview):
    client_type = openreview.api.OpenReviewClient
    return tuple(name for name in CLIENT_METHODS if not callable(getattr(client_type, name, None)))


def doctor():
    openreview = import_openreview()
    missing = client_contract(openreview)
    print("%-26s %s" % ("python", sys.executable))
    print("%-26s %s" % ("openreview-py", package_version()))
    print("%-26s %s" % ("API client contract", "ok" if not missing
                         else "MISSING " + ", ".join(missing)))
    print("%-26s %s" % ("pdftotext", shutil.which(textutil.TOOL) or "MISSING"))
    print("%-26s %s / %s" % ("credentials",
                             "set" if os.environ.get(USERNAME_ENV) else "MISSING",
                             "set" if os.environ.get(PASSWORD_ENV) else "MISSING"))
    return 1 if missing or shutil.which(textutil.TOOL) is None else 0


def client():
    openreview = import_openreview()
    username, password = credentials()
    try:
        return openreview.api.OpenReviewClient(baseurl=BASEURL, username=username,
                                               password=password)
    except openreview.MfaRequiredException:
        raise SystemExit(fail("this account requires multi-factor authentication; "
                              "openreview-py cannot complete it non-interactively"))
    except openreview.OpenReviewException as error:
        raise SystemExit(fail("OpenReview rejected the login: %s" % error))


def safe_id(paper_id):
    if not SAFE_ID.fullmatch(str(paper_id or "")):
        raise ValueError("refusing to use %r as a filename" % (paper_id,))
    return str(paper_id)


def value_of(field):
    return field.get("value") if isinstance(field, dict) else field


def flatten_content(note):
    return {key: value_of(field) for key, field in (note.content or {}).items()}


def submission_invitation(connection, venue_id):
    group = connection.get_group(venue_id)
    name = value_of((group.content or {}).get("submission_name")) or "Submission"
    return "%s/-/%s" % (venue_id, name)


def submissions(connection, venue_id, accepted_only=True):
    if accepted_only:
        return connection.get_all_notes(content={"venueid": venue_id})
    return connection.get_all_notes(invitation=submission_invitation(connection, venue_id))


def manifest_rows():
    if not MANIFEST.exists():
        return []
    rows, damaged = [], 0
    for number, line in enumerate(MANIFEST.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            rows.append(json.loads(line))
        except json.JSONDecodeError:
            damaged += 1
            print("  WARN %s line %d is not valid JSON, skipped" % (MANIFEST.name, number))
    if damaged:
        print("  WARN %d damaged line(s); they will be re-harvested" % damaged)
    return rows


def seen_ids():
    return {row["id"] for row in manifest_rows() if "id" in row}


def close_unterminated_line():
    if not MANIFEST.exists() or not MANIFEST.stat().st_size:
        return False
    with MANIFEST.open("rb+") as handle:
        handle.seek(-1, os.SEEK_END)
        if handle.read(1) == b"\n":
            return False
        handle.write(b"\n")
    return True


def row_for(note, content, venue_id, screening):
    return {
        "id": note.id,
        "venue": venue_id,
        "title": content.get("title"),
        "abstract": content.get("abstract"),
        "keywords": content.get("keywords") or [],
        "pdf": PDF_URL % note.id,
        "has_pdf": bool(content.get("pdf")),
        "tier": screening.tier,
        "score": screening.score,
        "signals": ["%s:%s" % (s.group, s.term) for s in screening.signals],
    }


def harvest_meta(venue_id, accepted_only=True):
    connection = client()
    terms = screen.registry_terms(database.load_registries())
    META.mkdir(parents=True, exist_ok=True)
    if close_unterminated_line():
        print("  WARN %s did not end with a newline; an interrupted last row was closed off"
              % MANIFEST.name)
    already = seen_ids()

    counts = {screen.STRONG: 0, screen.POSSIBLE: 0, screen.WEAK: 0}
    added = skipped = 0
    with MANIFEST.open("a", encoding="utf-8") as log:
        for note in submissions(connection, venue_id, accepted_only):
            if note.id in already:
                skipped += 1
                continue
            content = flatten_content(note)
            screening = screen.screen(content.get("title"), content.get("abstract"),
                                      content.get("keywords") or [], terms)
            (META / ("%s.json" % safe_id(note.id))).write_text(
                json.dumps(note.to_json(), ensure_ascii=False, indent=1), encoding="utf-8")
            log.write(json.dumps(row_for(note, content, venue_id, screening),
                                 ensure_ascii=False) + "\n")
            log.flush()
            counts[screening.tier] += 1
            added += 1

    print("%s: %d new, %d already held" % (venue_id, added, skipped))
    for tier in (screen.STRONG, screen.POSSIBLE, screen.WEAK):
        print("  %-9s %d" % (tier, counts[tier]))
    return 0


def is_pdf(payload):
    return bool(payload) and payload.startswith(PDF_MAGIC)


def save_bytes(payload, target):
    partial = target.with_suffix(paths.PARTIAL)
    partial.write_bytes(payload)
    partial.replace(target)


def clear_partials(folder):
    for leftover in folder.glob("*" + paths.PARTIAL):
        leftover.unlink()


def error_status(error):
    payload = error.args[0] if error.args else None
    return payload.get("status") if isinstance(payload, dict) else None


def retryable(error):
    status = error_status(error)
    return isinstance(error, OSError) or status == 429 or \
        isinstance(status, int) and status >= 500


def fetch_pdf(connection, paper_id, target, retries=RETRIES, delay=DELAY):
    payload = None
    for attempt in range(retries):
        try:
            payload = connection.get_attachment(field_name="pdf", id=paper_id)
            break
        except Exception as error:
            if not retryable(error) or attempt + 1 == retries:
                raise
            wait = delay * (2 ** attempt)
            print("  RETRY %s in %.1fs: %s" % (paper_id, wait, error))
            time.sleep(wait)
    if not is_pdf(payload):
        print("  SKIPPED %s: response is not a PDF (%d bytes)" % (paper_id, len(payload or b"")))
        return False
    save_bytes(payload, target)
    return True


def harvest_pdfs(tiers=DOWNLOAD_TIERS, limit=None, delay=DELAY):
    PDFS.mkdir(parents=True, exist_ok=True)
    clear_partials(PDFS)
    wanted = [row for row in manifest_rows() if row["tier"] in tiers]
    todo = [row for row in wanted if not (PDFS / ("%s.pdf" % safe_id(row["id"]))).exists()]
    if limit:
        todo = todo[:limit]

    print("%d in tiers %s, %d already on disk, fetching %d"
          % (len(wanted), "/".join(tiers), len(wanted) - len(todo), len(todo)))
    if not todo:
        return 0

    connection = client()
    got = failed = 0
    for number, row in enumerate(todo, start=1):
        try:
            target = PDFS / ("%s.pdf" % safe_id(row["id"]))
            if fetch_pdf(connection, row["id"], target):
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
            partial = target.with_suffix(paths.PARTIAL)
            partial.write_text(textutil.read_pdf(pdf), encoding="utf-8")
            partial.replace(target)
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
    by_venue = {}
    for row in rows:
        by_venue.setdefault(row["venue"], {}).setdefault(row["tier"], 0)
        by_venue[row["venue"]][row["tier"]] += 1
    print("%-44s %8s %9s %6s %7s" % ("venue", "strong", "possible", "weak", "total"))
    for venue, tiers in sorted(by_venue.items()):
        total = sum(tiers.values())
        print("%-44s %8d %9d %6d %7d" % (venue[:44], tiers.get(screen.STRONG, 0),
                                         tiers.get(screen.POSSIBLE, 0),
                                         tiers.get(screen.WEAK, 0), total))
    print("%-44s %8d %9d %6d %7d" % ("ALL",
          sum(1 for r in rows if r["tier"] == screen.STRONG),
          sum(1 for r in rows if r["tier"] == screen.POSSIBLE),
          sum(1 for r in rows if r["tier"] == screen.WEAK), len(rows)))
    print("\npdf on disk: %d, text on disk: %d"
          % (len(list(PDFS.glob("*.pdf"))) if PDFS.exists() else 0,
             len(list(TEXTS.glob("*.txt"))) if TEXTS.exists() else 0))
    return 0


def preflight(venue_id=None):
    openreview = import_openreview()
    if doctor():
        return 1
    connection = client()
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

    invitation = submission_invitation(connection, venue_id)
    try:
        connection.get_invitation(invitation)
        print("%-26s %s" % ("submission invitation", invitation))
    except openreview.OpenReviewException as error:
        print("%-26s NOT FOUND: %s" % ("submission invitation", error))
        return 1

    checks = (("accepted (venueid)", {"content": {"venueid": venue_id}}),
              ("all submissions", {"invitation": invitation}))
    failed = False
    for label, query in checks:
        try:
            notes, count = connection.get_notes(limit=1, with_count=True, **query)
            print("%-26s %s" % (label, count))
            if notes:
                content = flatten_content(notes[0])
                fields = ("title", "abstract", "pdf")
                missing = [field for field in fields if not content.get(field)]
                print("%-26s %s" % (label + " sample",
                                     "ok" if not missing
                                     else "MISSING " + ", ".join(missing)))
                failed = failed or bool(missing)
        except openreview.OpenReviewException as error:
            print("%-26s unavailable: %s" % (label, error))
            failed = True

    if failed:
        print("\nnot ready: fix the checks above before harvesting")
        return 1
    print("\nready: harvest.py meta %s" % venue_id)
    return 0


def list_venues(needle=None):
    connection = client()
    members = connection.get_group("venues").members
    for venue in sorted(members):
        if not needle or needle.lower() in venue.lower():
            print(venue)
    return 0


USAGE = """usage: run these with .venv/bin/python -- openreview-py lives there, not in system python

  .venv/bin/python harvest.py doctor                 offline dependency and API contract checks
  .venv/bin/python harvest.py preflight [venue_id]   check python, package, login and venue
  .venv/bin/python harvest.py venues [substring]     list venue identifiers
  .venv/bin/python harvest.py meta <venue_id> [--all]  metadata only, screened, resumable
  .venv/bin/python harvest.py stats                  tier breakdown of the manifest
  .venv/bin/python harvest.py pdfs [--tier a,b] [--limit N]
  .venv/bin/python harvest.py text                   pdftotext over downloaded pdfs

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
        tiers = DOWNLOAD_TIERS
        limit = None
        for index, flag in enumerate(rest):
            if flag == "--tier" and index + 1 < len(rest):
                tiers = tuple(rest[index + 1].split(","))
            if flag == "--limit" and index + 1 < len(rest):
                limit = int(rest[index + 1])
        return harvest_pdfs(tiers, limit)
    if command == "text":
        return harvest_text()

    print(USAGE)
    return 2


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
