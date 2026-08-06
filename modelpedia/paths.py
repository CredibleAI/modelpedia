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

PARTIAL = ".part"
