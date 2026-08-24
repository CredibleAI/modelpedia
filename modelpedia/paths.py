from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

DATA = ROOT / "data"
REGISTRIES = DATA / "registries"
FINDINGS = DATA / "findings"
VOCABULARIES = DATA / "vocabularies.yaml"

OUT = ROOT / "out"
GRAPH = OUT / "graph.json"
CSV = OUT / "csv"

SITE = ROOT / "site"
ASSETS = ROOT / "assets"

CORPUS = ROOT / "corpus"

MANIFEST = CORPUS / "manifest.jsonl"
META = CORPUS / "meta"
PDFS = CORPUS / "pdf"
TEXTS = CORPUS / "text"
REVIEWS = CORPUS / "reviews"

PROMPTS = CORPUS / "prompts"
PAGE_PROMPTS = CORPUS / "prompts-pages"
ENTITY_PROMPTS = CORPUS / "prompts-entities"
FACET_PROMPTS = CORPUS / "prompts-facets"
TAGS = CORPUS / "tags"

RUNS = CORPUS / "runs"
ANSWERS = CORPUS / "answers"

REPORTS = CORPUS / "reports"
RANKING = REPORTS / "ranking.csv"
ENTITY_REPORT = REPORTS / "entities.jsonl"
PROPOSED = REPORTS / "proposed.jsonl"
ANCHOR_PROPOSALS = REPORTS / "anchor_proposals.jsonl"

SELECTION = CORPUS / "selection"
PDF_SELECTION = SELECTION / "pdf_selection.txt"
PDF_CANDIDATES = SELECTION / "pdf_candidates.txt"
FROZEN_TERMS = SELECTION / "screen_terms_2026-08-06.txt"

LEGACY = CORPUS / "legacy"

PARTIAL = ".part"
