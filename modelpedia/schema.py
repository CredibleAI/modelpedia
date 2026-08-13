import re
from typing import NamedTuple

from modelpedia import graph as graph_json

ANY_REGISTRY = None


class LinkField(NamedTuple):
    registry: str | None
    edge_type: str
    inline: bool = False


LINK_FIELDS = {
    "models": LinkField(graph_json.MODEL, graph_json.EDGE_ABOUT_MODEL),
    "concepts": LinkField(graph_json.CONCEPT, graph_json.EDGE_TAGGED_CONCEPT),
    "sources": LinkField(graph_json.SOURCE, graph_json.EDGE_REPORTED_IN),
    "datasets": LinkField(graph_json.DATASET, graph_json.EDGE_USES_DATASET),
    "methods": LinkField(graph_json.METHOD, graph_json.EDGE_USES_METHOD),
    "related_work": LinkField(ANY_REGISTRY, graph_json.EDGE_CITES, inline=True),
}

MODELS_FIELD = "models"

RELATED_FINDINGS_FIELD = "related_findings"

REQUIRED_FIELDS = ("id", "title", "description", "concepts", "models", "sources",
                   "extracted_by")

EMPTY_ALLOWED = frozenset(("concepts",))

OPTIONAL_FIELDS = ("key_metric", "caveat", RELATED_FINDINGS_FIELD)

CLOSED_FIELDS = ("evidence_type", "extracted_by")

MODEL_FACETS = ("modality", "domain", "task")

ROLE_FIELDS = ("datasets", "methods", "related_work")

KNOWN_FIELDS = set(REQUIRED_FIELDS) | set(CLOSED_FIELDS) | set(OPTIONAL_FIELDS) | set(LINK_FIELDS)

ROLE_SCOPE = "role"

VOCABULARY_SCOPES = {
    graph_json.FINDING: CLOSED_FIELDS,
    graph_json.MODEL: MODEL_FACETS,
    ROLE_SCOPE: ROLE_FIELDS,
}

SLUG = re.compile(r"[a-z][a-z0-9-]*")
ISO_DATE = re.compile(r"[0-9]{4}(-[0-9]{2}(-[0-9]{2})?)?")
