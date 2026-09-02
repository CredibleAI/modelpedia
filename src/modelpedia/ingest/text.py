import io
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

URL_HEAD = re.compile(r"https?:\s*//\S*", re.I)
URL_TAIL = re.compile(r"[A-Za-z0-9._~%\-]+(?:[/?#=][^\s]*)?[.,;)]?")
URL_OPEN = ("/", "=", "?", "#", "-", "_", ".", ":")
URL_JOINS_BARE = ("/", "=", "-", "_")
URL_TRIM = ".,;)]"
URL_HOST = re.compile(r"https?://[^/\s]+\.[A-Za-z]{2,}(?:[:/?#]|$)")
URL_CUT_SHORT = ("=", "?", "#", "&")

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


def squeezed(value):
    return WHITESPACE.sub("", str(value or ""))


def urls_in(value):
    body = WHITESPACE.sub(" ", str(value or ""))
    for match in URL_HEAD.finditer(body):
        url = WHITESPACE.sub("", match.group(0))
        rest = body[match.end():].split()
        while rest and url.endswith(URL_OPEN):
            head = rest[0]
            if not URL_TAIL.fullmatch(head):
                break
            if "/" not in head and not url.endswith(URL_JOINS_BARE):
                break
            url += head
            rest = rest[1:]
        yield url.rstrip(URL_TRIM)


def usable_url(url):
    trimmed = str(url or "").rstrip(URL_TRIM)
    if not URL_HOST.match(trimmed) or trimmed.endswith(URL_CUT_SHORT):
        return ""
    return trimmed


def with_source_case(url, packed):
    if not url or not packed:
        return url
    found = re.search(re.escape(url), packed, re.I)
    return found.group(0) if found else url


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


RENDER_SCALE = 2.0


def page_images(path, scale=RENDER_SCALE, limit=None):
    pdfium = library()
    try:
        document = pdfium.PdfDocument(str(path))
        pages = list(document)[:limit] if limit else list(document)
        return [page.render(scale=scale).to_pil() for page in pages]
    except Exception as error:
        raise MissingTool("%s failed to render %s: %s" % (TOOL, path, error))


def png_bytes(image):
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    return buffer.getvalue()


def jpeg_bytes(image, quality=80):
    buffer = io.BytesIO()
    image.convert("RGB").save(buffer, format="JPEG", quality=quality)
    return buffer.getvalue()


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
