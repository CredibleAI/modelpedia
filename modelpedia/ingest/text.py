import re
import unicodedata
from importlib.metadata import PackageNotFoundError, version
from typing import NamedTuple

TOOL = "pypdfium2"
PAGE_BREAK = "\f"

SOFT_HYPHEN = "­"
HYPHENATED = re.compile(r"(\w)[­\-][ \t]*\n[ \t]*(\w)")
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


def normalise(value):
    return WHITESPACE.sub(" ", fold(join_hyphens(value))).strip()


def flatten(value):
    return NOT_ALNUM.sub("", fold(join_hyphens(value)))


def library():
    try:
        import pypdfium2
    except ImportError:
        raise MissingTool("%s is not installed; .venv/bin/pip install %s" % (TOOL, TOOL))
    return pypdfium2


def available():
    try:
        library()
        return TOOL
    except MissingTool:
        return None


def installed_version():
    try:
        return version(TOOL)
    except PackageNotFoundError:
        return "installed"


def read_pdf(path):
    pdfium = library()
    try:
        document = pdfium.PdfDocument(str(path))
        pages = [page.get_textpage().get_text_range() for page in document]
    except Exception as error:
        raise MissingTool("%s failed on %s: %s" % (TOOL, path, error))
    return PAGE_BREAK.join(page.replace(PAGE_BREAK, " ") for page in pages)


def document(path):
    return from_text(str(path), read_pdf(path))


def from_text(name, raw):
    body = join_small_caps(raw)
    pages = tuple(body.split(PAGE_BREAK))
    return Document(name=name, pages=pages, text=normalise(body), flat=flatten(body))


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
