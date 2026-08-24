import json
import os
import re
from typing import NamedTuple

REQUIRED_KEYS = ("id", "tier", "venue")
REVIEW_KEYS = ("id", "venue", "review_id")

SAFE_ID = re.compile(r"[A-Za-z0-9_-]{1,64}")
UNSAFE_NAME = re.compile(r"[^A-Za-z0-9._-]+")


class Manifest(NamedTuple):
    rows: tuple
    complaints: tuple
    repeated: int


class Reviews(NamedTuple):
    by_paper: dict
    complaints: tuple

    def texts(self, paper_id):
        return [review_text(row) for row in self.by_paper.get(paper_id, ())]

    def rating(self, paper_id):
        return mean_rating(self.by_paper.get(paper_id, ()))

    def count(self, paper_id):
        return len(self.by_paper.get(paper_id, ()))


def safe_id(paper_id):
    if not SAFE_ID.fullmatch(str(paper_id or "")):
        raise ValueError("refusing to use %r as a filename" % (paper_id,))
    return str(paper_id)


def venue_slug(venue_id):
    """One spelling of a venue identifier as a file name, for every file that carries one."""
    return UNSAFE_NAME.sub("-", str(venue_id or "")).strip("-")


def store_name(venue_id):
    return "%s.jsonl" % venue_slug(venue_id)


def keys_complaint(row, keys):
    if not isinstance(row, dict):
        return "is not a record"
    missing = [key for key in keys if not isinstance(row.get(key), str)]
    if missing:
        return "lacks %s" % " and ".join(missing)
    if not SAFE_ID.fullmatch(row["id"]):
        return "has an id that cannot become a filename: %r" % row["id"]
    return None


def row_complaint(row):
    return keys_complaint(row, REQUIRED_KEYS)


def review_complaint(row):
    complaint = keys_complaint(row, REVIEW_KEYS)
    if complaint:
        return complaint
    if not isinstance(row.get("fields"), dict):
        return "carries no review fields"
    return None


def json_lines(path):
    """Split on the newline and nothing else, and every JSONL reader in the project comes here
    to do it. `splitlines()` also breaks on U+2028 and U+2029, which
    `json.dumps(ensure_ascii=False)` writes through unescaped because JSON does not consider them
    line breaks -- 111 of them sat in ICLR 2025 review prose and cost 92 rows on 2026-08-23,
    silently, because half a record is not valid JSON and reads as one bad line."""
    if not path.exists():
        return
    for number, line in enumerate(path.read_text(encoding="utf-8").split("\n"), start=1):
        if line.strip():
            yield number, line


def read(path, complaint_of=row_complaint):
    for number, line in json_lines(path):
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            yield None, "%s line %d is not valid JSON, skipped" % (path.name, number)
            continue
        complaint = complaint_of(row)
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


def review_row(paper_id, venue_id, review_id, rating, fields):
    return {"id": paper_id, "venue": venue_id, "review_id": review_id,
            "rating": rating, "fields": fields}


def review_text(row):
    return " \n ".join(str(value) for value in (row.get("fields") or {}).values())


def mean_rating(rows):
    given = [row["rating"] for row in rows if isinstance(row.get("rating"), (int, float))]
    return round(sum(given) / len(given), 2) if given else None


def load_reviews(folder):
    """One store per venue, appended as it is fetched, so a run that dies halfway resumes. The
    same review written twice by two runs is the same `review_id` and is counted once. A row with
    no fields is the fetch writing down that it asked and this paper has no review: `reviewed_ids`
    counts it so the fetch does not ask again, and this function drops it so the review count
    stays the number of reviews rather than the number of answers."""
    by_paper, complaints, seen = {}, [], set()
    for path in sorted(folder.glob("*.jsonl")) if folder.exists() else ():
        for row, complaint in read(path, review_complaint):
            if complaint:
                complaints.append(complaint)
                continue
            if row["review_id"] in seen or not row["fields"]:
                continue
            seen.add(row["review_id"])
            by_paper.setdefault(row["id"], []).append(row)
    return Reviews(by_paper=by_paper, complaints=tuple(complaints))


def reviewed_ids(path):
    seen = set()
    for row, _ in read(path, review_complaint):
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


def row_for(paper_id, content, venue_id, screening, rules_version, pdf_url,
            reviews=0, rating=None):
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
        "subscores": dict(screening.subscores),
        "reviews": reviews,
        "rating": rating,
        "signals": ["%s:%s" % (signal.group, signal.term) for signal in screening.signals],
        "rules_version": rules_version,
    }


def rescored(row, screening, rules_version, reviews=0, rating=None):
    """The metadata is what it was; only the reading of it changes. Everything the fetch put in
    the row survives, so a rescreen never costs a field."""
    return dict(row, tier=screening.tier, score=screening.score,
                subscores=dict(screening.subscores), reviews=reviews, rating=rating,
                signals=["%s:%s" % (signal.group, signal.term) for signal in screening.signals],
                rules_version=rules_version)


def dump(rows):
    return "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)


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
