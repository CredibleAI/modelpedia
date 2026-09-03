from modelpedia import graph as graph_json
from modelpedia import schema
import copy

from modelpedia.build import database
from modelpedia.ingest import manifest as store
from modelpedia.ingest import link
from modelpedia.ingest import openreview
from modelpedia.ingest import split as splitter
from modelpedia.ingest import screen


CANDIDATE = {
    "id": "XX-999",
    "extracted_by": "automatic-extraction",
    "title": "A candidate",
    "description": "A description.",
    "evidence_type": "observational",
    "concepts": [{"ref": "concept:idea"}],
    "models": [{"ref": "model:thing", "variant": "variant:thing-small"}],
    "sources": [{"ref": "source:the-paper"}],
    "datasets": [{"ref": "dataset:pile", "role": "eval"}],
    "methods": [{"ref": "method:probe", "role": "primary"}],
    "related_work": [],
    "related_findings": [],
}

def candidate(**changes):
    record = copy.deepcopy(CANDIDATE)
    record.update(changes)
    return record

def index_of(node_type=None, **changes):
    return link.index_of(sample_db(**changes).entities, node_type)

AUDIT = ("What does CLIP actually look at?",
         "We analyze CLIP and find that it relies on printed text in images rather than on "
         "depicted content. Our analysis reveals a systematic shortcut.")

OPTIMISER = ("A faster optimizer for large-scale training",
             "We propose a new optimizer. Our method outperforms Adam and achieves "
             "state-of-the-art convergence on ImageNet.")

AUDIT_REVIEWS = (
    "The paper analyzes what CLIP attends to and finds that the model relies on printed text. "
    "The probing experiments are convincing and the shortcut is well characterized.",
    "This is an empirical study of CLIP. The authors probe the representation and show that "
    "it fails to use depicted content. A careful analysis of a known failure mode.",
    "The submission analyzes CLIP and reveals that the model cannot separate text from content. "
    "The probing setup is sound and the shortcut it characterizes is real.",
)

def screened_row(paper_id="aBcD"):
    content = openreview.flat_content(
        {"title": {"value": AUDIT[0]}, "abstract": {"value": AUDIT[1]}})
    return store.row_for(paper_id, content, "V/2026", screen.screen(*AUDIT),
                         screen.RULES_VERSION, openreview.pdf_url(paper_id))

class Reply:
    def __init__(self, note_id, content):
        self.id = note_id
        self.content = content
        self.invitations = ["V/2026/Submission1/-/Official_Review"]

def answer_with(**fields):
    document = {"findings": [{"models": [], "methods": [], "datasets": [], "related_work": []}]}
    document["findings"][0].update(fields.pop("finding", {}))
    document.update(fields)
    return document

SPLIT_ENTITIES = {
    "model:llama-2": {"type": "model", "name": "Llama 2"},
    "variant:llama-2-7b-chat": {"type": "variant", "name": "Llama 2 7B Chat",
                                "parent": "model:llama-2"},
    "method:probe": {"type": "method", "name": "Probing classifiers"},
    "concept:shortcut": {"type": "concept", "name": "Shortcut"},
}

SPLIT_ROLES = {"methods": ["primary"], "datasets": ["eval"],
               "related_work": ["builds-on", "context"]}

def split_of(findings, entities=None, papers={"p1": "source:the-paper"}):
    documents = {"p1": {"findings": findings, "entities": entities or []}}
    return splitter.split(documents, SPLIT_ENTITIES, papers, "IC", {"concept:shortcut"},
                          SPLIT_ROLES)


VOCABULARIES = {
    graph_json.FINDING: {
        "evidence_type": ["observational", "correlational", "interventional"],
        "extracted_by": ["manual-extraction", "automatic-extraction"],
    },
    graph_json.MODEL: {
        "modality": ["image", "text"],
        "domain": ["geospatial"],
        "task": ["generative"],
    },
    schema.ROLE_SCOPE: {
        "datasets": ["train", "eval"],
        "methods": ["primary"],
        "related_work": ["builds-on"],
    },
}


ENTITIES = {
    "model:thing": {"type": graph_json.MODEL, "name": "Thing", "modality": ["image"],
                    "variants": {"variant:thing-small": {"name": "Thing small"}}},
    "variant:thing-small": {"type": graph_json.VARIANT, "name": "Thing small",
                            "parent": "model:thing"},
    "concept:idea": {"type": graph_json.CONCEPT, "name": "Idea"},
    "method:probe": {"type": graph_json.METHOD, "name": "Probe", "anchor": "https://example.org"},
    "dataset:pile": {"type": graph_json.DATASET, "name": "Pile", "anchor": "https://example.org"},
    "source:the-paper": {"type": graph_json.SOURCE, "name": "The paper", "date": "2026-01",
                         "authors": ["Ada Lovelace"]},
}


FINDING = {
    "id": "XX-001",
    "extracted_by": "manual-extraction",
    "title": "A title",
    "description": "A description.",
    "evidence_type": "observational",
    "concepts": [{"ref": "concept:idea"}],
    "models": [{"ref": "model:thing", "variant": "variant:thing-small"}],
    "sources": [{"ref": "source:the-paper"}],
    "datasets": [{"ref": "dataset:pile", "role": "eval"}],
    "methods": [{"ref": "method:probe", "role": "primary"}],
    "related_work": [{"name": "Earlier work", "anchor": "https://example.org/earlier",
                      "role": "builds-on"}],
    "related_findings": [],
}


def sample_db(**changes):
    db = database.Database(vocabularies=copy.deepcopy(VOCABULARIES),
                        entities=copy.deepcopy(ENTITIES),
                        findings={"XX-001": copy.deepcopy(FINDING)})
    for key, mutate in changes.items():
        mutate(getattr(db, key))
    return db
