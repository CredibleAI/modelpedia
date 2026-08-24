import json
import re
from typing import NamedTuple

import yaml

from modelpedia.ingest import text as textutil

FINDINGS = "findings"
CONSIDERED = "considered"
ENTITIES = "entities"
CONCEPTS_CONSIDERED = "concepts_considered"
RESULTS_COVERED = "results_covered"

NAME_FIELDS = ("models", "datasets", "methods", "related_work")
MIN_MARK = 3
MIN_MARGIN = 1.5

FORBIDDEN = re.compile("[^\u0009\u000a\u000d\u0020-\u007e\u0085"
                       "\u00a0-\ud7ff\ue000-\ufffd\U00010000-\U0010ffff]")

FENCE_OPEN = re.compile(r"\A\s*```[a-zA-Z]*\s*")
FENCE_CLOSE = re.compile(r"```\s*\Z")
PROSE = re.compile(r"^(\s*-?\s*)(title|description|key_metric|caveat|name|why|citation"
                   r"|finding|closest|definition|instead_of):[ \t]+(?![|>])(.*)$")
MIS_INDENT = re.compile(r"^ (\w+):(\s)")


class Answer(NamedTuple):
    document: dict
    repaired: bool


class Match(NamedTuple):
    paper: str
    score: float
    runner_up: float

    def confident(self):
        return self.paper is not None and self.score > 0 and \
            (not self.runner_up or self.score >= self.runner_up * MIN_MARGIN)


class Unreadable(Exception):
    pass


def without_fence(raw):
    return FENCE_CLOSE.sub("", FENCE_OPEN.sub("", raw.strip())).strip()


def quoted_prose(raw):
    lines = []
    for line in raw.splitlines():
        match = PROSE.match(line)
        if not match:
            lines.append(line)
            continue
        lead, field, value = match.groups()
        body = value.strip()
        if not body or body[0] in "'\"[{" or (": " not in body and not body.endswith(":")):
            lines.append(line)
            continue
        lines.append("%s%s: %s" % (lead, field, json.dumps(body, ensure_ascii=False)))
    return "\n".join(lines)


def one_scalar(raw):
    """A field whose value is a closed quote followed by more text: `key_metric: "a"; "b"`.
    YAML reads the quote, then finds a scalar where the mapping should end, and the whole answer
    is lost over one line. Seen 2026-08-20 on JVkdSi7Ekg. The value is re-encoded whole, so
    nothing the model wrote is dropped. Only reached after the plainer repairs have failed, which
    is what keeps it away from documents that already parse -- a quoted scalar spanning two lines
    is legal YAML and must not be touched."""
    lines = []
    for line in raw.splitlines():
        match = PROSE.match(line)
        if not match or not match.group(3).strip():
            lines.append(line)
            continue
        lead, field, value = match.groups()
        try:
            yaml.safe_load(value)
            lines.append(line)
        except yaml.YAMLError:
            lines.append("%s%s: %s" % (lead, field, json.dumps(value.strip(), ensure_ascii=False)))
    return "\n".join(lines)


def loadable(raw):
    """YAML forbids a handful of codepoints outright, and a citation copied verbatim out of a
    paper can carry one: U+FFFE reached us from an oracle-bone paper on 2026-08-19 and made the
    whole answer unparseable. Dropping them is a repair and is reported as one."""
    return FORBIDDEN.sub("", raw)


def evenly_indented(raw):
    return "\n".join(MIS_INDENT.sub(r"  \1:\2", line) if MIS_INDENT.match(line) else line
                     for line in raw.splitlines())


def read(raw):
    body = without_fence(raw)
    complaint = None
    for attempt, repaired in ((body, False),
                              (quoted_prose(body), True),
                              (quoted_prose(evenly_indented(body)), True),
                              (loadable(quoted_prose(evenly_indented(body))), True),
                              (loadable(one_scalar(evenly_indented(body))), True)):
        try:
            document = yaml.safe_load(attempt)
        except yaml.YAMLError as error:
            complaint = complaint or str(error).split("\n")[0]
            continue
        return Answer(validated(document), repaired)
    raise Unreadable("not valid YAML even after repair: %s" % complaint)


BLOCKS = (FINDINGS, CONSIDERED, ENTITIES, CONCEPTS_CONSIDERED, RESULTS_COVERED)


def entries_of(document, block):
    value = document.get(block)
    if value is None:
        return []
    if not isinstance(value, list):
        raise Unreadable("%r is not a list" % block)
    if any(not isinstance(entry, dict) for entry in value):
        raise Unreadable("%r holds something that is not a record" % block)
    return value


def validated(document):
    if not isinstance(document, dict) or FINDINGS not in document:
        raise Unreadable("no top-level %r key" % FINDINGS)
    for block in BLOCKS:
        entries_of(document, block)
    return document


def concepts_of(finding):
    for item in finding.get("concepts") or []:
        written = item if isinstance(item, str) else None
        if written is None and isinstance(item, dict):
            written = item.get("concept") or item.get("id") or item.get("name")
        value = str(written or "").strip()
        if not value:
            continue
        yield (value if value.startswith("concept:") else "concept:%s" % value), item


def named_in(document):
    marks = set()
    for entry in entries_of(document, CONSIDERED):
        marks.add(textutil.flatten(str(entry.get("model") or "")))
    for finding in entries_of(document, FINDINGS):
        for field in NAME_FIELDS:
            for item in finding.get(field) or []:
                name = item if isinstance(item, str) else (item or {}).get("name")
                marks.add(textutil.flatten(str(name or "")))
        for word in re.findall(r"[A-Za-z][A-Za-z0-9-]{5,}", str(finding.get("title") or "")):
            marks.add(textutil.flatten(word))
    return {mark for mark in marks if len(mark) >= MIN_MARK}


def weight_of(mark, corpus):
    seen = sum(1 for flat in corpus.values() if mark in flat)
    return 1.0 / seen if seen else 0.0


def match(document, corpus):
    marks = named_in(document)
    if not marks or not corpus:
        return Match(None, 0.0, 0.0)
    weights = {mark: weight_of(mark, corpus) for mark in marks}
    scored = sorted(((round(sum(weight for mark, weight in weights.items() if mark in flat), 3),
                      paper) for paper, flat in corpus.items()), reverse=True)
    best = scored[0]
    runner_up = scored[1][0] if len(scored) > 1 else 0.0
    return Match(best[1], best[0], runner_up)
