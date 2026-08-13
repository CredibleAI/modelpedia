import json
import os
import re
from typing import NamedTuple

REQUIRED_KEYS = ("id", "tier", "venue")

SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")


class Manifest(NamedTuple):
    rows: tuple
    complaints: tuple
    repeated: int


def safe_id(paper_id):
    if not SAFE_ID.fullmatch(str(paper_id or "")):
        raise ValueError("refusing to use %r as a filename" % (paper_id,))
    return str(paper_id)


def row_complaint(row):
    if not isinstance(row, dict):
        return "is not a record"
    missing = [key for key in REQUIRED_KEYS if not isinstance(row.get(key), str)]
    if missing:
        return "lacks %s" % " and ".join(missing)
    if not SAFE_ID.fullmatch(row["id"]):
        return "has an id that cannot become a filename: %r" % row["id"]
    return None


def read(path):
    if not path.exists():
        return
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            yield None, "%s line %d is not valid JSON, skipped" % (path.name, number)
            continue
        complaint = row_complaint(row)
        if complaint:
            yield None, "%s line %d %s, skipped" % (path.name, number, complaint)
            continue
        yield row, None


def load(path):
    by_id, complaints, repeated = {}, [], 0
    for row, complaint in read(path):
        if complaint:
            complaints.append(complaint)
            continue
        repeated += row["id"] in by_id
        by_id[row["id"]] = row
    return Manifest(rows=tuple(by_id.values()), complaints=tuple(complaints), repeated=repeated)


def ids_in(path):
    seen = set()
    for row, _ in read(path):
        if row:
            seen.add(row["id"])
    return seen


def close_unterminated_line(path):
    if not path.exists() or not path.stat().st_size:
        return False
    with path.open("rb+") as handle:
        handle.seek(-1, os.SEEK_END)
        if handle.read(1) == b"\n":
            return False
        handle.write(b"\n")
    return True


def row_for(paper_id, content, venue_id, screening, rules_version, pdf_url):
    return {
        "id": paper_id,
        "venue": venue_id,
        "title": content.get("title"),
        "abstract": content.get("abstract"),
        "keywords": content.get("keywords") or [],
        "pdf": pdf_url,
        "has_pdf": bool(content.get("pdf")),
        "tier": screening.tier,
        "score": screening.score,
        "signals": ["%s:%s" % (signal.group, signal.term) for signal in screening.signals],
        "rules_version": rules_version,
    }


def selected_rows(rows, tiers, ids):
    if ids is None:
        return [row for row in rows if row["tier"] in tiers], ()
    held = {row["id"]: row for row in rows}
    absent = tuple(paper_id for paper_id in ids if paper_id not in held)
    return [held[paper_id] for paper_id in ids if paper_id in held], absent


def read_ids(path):
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError as error:
        raise ValueError("cannot read %s: %s" % (path, error))
    ids = []
    for number, line in enumerate(lines, start=1):
        candidate = line.strip()
        if not candidate or candidate.startswith("#"):
            continue
        try:
            ids.append(safe_id(candidate))
        except ValueError:
            raise ValueError("%s line %d is not an identifier: %r" % (path, number, candidate))
    if not ids:
        raise ValueError("%s contains no identifiers" % path)
    return list(dict.fromkeys(ids))
