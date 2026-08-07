import re
import shutil
import subprocess
import unicodedata
from typing import NamedTuple

TOOL = "pdftotext"
PAGE_BREAK = "\f"
TIMEOUT = 120

SOFT_HYPHEN = "\u00ad"
HYPHENATED = re.compile(r"(\w)[\u00ad\-][ \t]*\n[ \t]*(\w)")
SPACED_CAPS = re.compile(r"(?<![A-Za-z])([A-Z])[ \t]+([A-Z]{2,})(?![a-z])")
WHITESPACE = re.compile(r"\s+")
NOT_ALNUM = re.compile(r"[^a-z0-9]+")


class MissingTool(Exception):
    pass


class Document(NamedTuple):
    name: str
    pages: tuple
    text: str
    flat: str


def fold(value):
    stripped = unicodedata.normalize("NFKD", str(value))
    return "".join(char for char in stripped if not unicodedata.combining(char)).lower()


def join_hyphens(value):
    return HYPHENATED.sub(r"\1\2", str(value))


def join_small_caps(value):
    return SPACED_CAPS.sub(r"\1\2", str(value))


def repaired(value):
    return join_small_caps(join_hyphens(value))


def normalise(value):
    return WHITESPACE.sub(" ", fold(repaired(value))).strip()


def flatten(value):
    return NOT_ALNUM.sub("", fold(repaired(value)))


def read_pdf(path):
    if shutil.which(TOOL) is None:
        raise MissingTool("%s is not on PATH; install poppler-utils" % TOOL)
    try:
        finished = subprocess.run([TOOL, "-layout", str(path), "-"],
                                  capture_output=True, check=False, timeout=TIMEOUT)
    except subprocess.TimeoutExpired:
        raise MissingTool("%s timed out after %ds on %s" % (TOOL, TIMEOUT, path))
    if finished.returncode != 0:
        raise MissingTool("%s failed on %s: %s"
                          % (TOOL, path, finished.stderr.decode("utf-8", "replace").strip()))
    return finished.stdout.decode("utf-8", "replace")


def document(path):
    return from_text(str(path), read_pdf(path))


def from_text(name, raw):
    pages = tuple(raw.split(PAGE_BREAK))
    return Document(name=name, pages=pages, text=normalise(raw), flat=flatten(raw))


def contains(doc, needle):
    packed = flatten(needle)
    return bool(packed) and packed in doc.flat


def contains_spaced(doc, needle):
    packed = normalise(needle)
    return bool(packed) and packed in doc.text


def pages_with(doc, needle):
    packed = flatten(needle)
    if not packed:
        return ()
    return tuple(number for number, page in enumerate(doc.pages, start=1)
                 if packed in flatten(page))
