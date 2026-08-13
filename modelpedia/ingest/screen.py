import hashlib
import json
import re
from typing import NamedTuple

from modelpedia import graph as graph_json
from modelpedia.ingest import link
from modelpedia.ingest import text as textutil

STRONG = "strong"
POSSIBLE = "possible"
WEAK = "weak"

STRONG_AT = 4.0
POSSIBLE_AT = 1.5

PER_GROUP = 2


class Group(NamedTuple):
    weight: float
    stems: tuple
    words: tuple


class Signal(NamedTuple):
    group: str
    term: str


class Screening(NamedTuple):
    score: float
    tier: str
    signals: tuple

    def groups(self):
        return tuple(dict.fromkeys(signal.group for signal in self.signals))


XAI = Group(2.0, (
    "explain", "explanat", "interpret", "attribution", "salien", "faithful",
    "transparen", "understandab", "counterfactual", "shapley", "banzhaf",
    "probing", "mechanistic", "feature importance", "feature visualiz",
    "relevance propagation", "integrated gradient", "sparse autoencoder",
    "concept vector", "concept bottleneck", "concept activation",
    "activation patching", "activation steering", "steering vector",
    "attention map", "attention head", "attention rollout", "influence function",
    "post-hoc", "posthoc", "self-explain", "black-box", "blackbox", "black box",
    "latent space", "embedding space", "representation analysis",
    "linear probe", "logit lens", "causal tracing", "model editing",
    "knowledge editing", "circuit analysis", "occlusion", "prototype",
    "rationale", "decision boundary", "internal representation",
), ("xai", "lime", "tcav", "cam", "shap", "lrp", "ig", "sae", "probe", "probes",
    "neuron", "neurons", "circuit", "circuits", "grad-cam", "gradcam"))

BEHAVIOUR = Group(1.0, (
    "failure mode", "failure case", "shortcut", "clever hans", "spurious",
    "stereotyp", "memoriz", "memoris", "hallucinat", "sycophan", "jailbreak",
    "backdoor", "trojan", "confound", "contaminat", "leakage", "brittle",
    "degradat", "out-of-distribution", "distribution shift", "calibrat",
    "overconfiden", "error analysis", "systematic error", "artifact", "artefact",
    "unfaithful", "emergent", "generaliz", "robustness", "limitation",
    "unintended", "undesirab", "misclassif", "blind spot", "corner case",
), ("bias", "biased", "biases", "fairness", "capability", "capabilities"))

FINDING = Group(1.0, (
    "we find", "we show that", "we demonstrate that", "we observe", "we reveal",
    "we analyz", "we analys", "we investigate", "we examine", "we audit",
    "we characteriz", "we quantify", "we uncover", "we discover", "we probe",
    "our analysis", "our findings", "reveals that", "suggests that",
    "case study", "empirical study", "empirical analysis", "sheds light",
    "surprisingly", "counterintuitive", "contrary to", "we ask whether",
    "we test whether", "we evaluate whether", "diagnos",
), ("audit", "audits", "understanding"))

METHOD = Group(-0.5, (
    "we propose", "we introduce", "we present a novel", "we develop",
    "our method", "our approach", "our framework", "novel framework",
    "state-of-the-art", "outperforms", "we design a", "new architecture",
), ("sota",))

MODEL = Group(2.0, (
    "segment anything", "stable diffusion", "vision transformer",
    "masked autoencoder",
), (
    "clip", "siglip", "siglip-2", "bert", "roberta", "deberta", "electra",
    "gpt-2", "gpt-3", "gpt-4", "gpt-4o", "chatgpt", "llama", "llama-2", "llama-3",
    "mistral", "mixtral", "gemma", "qwen", "falcon", "bloom", "pythia", "olmo",
    "phi-2", "phi-3", "opt-125m", "opt-1.3b", "opt-6.7b",
    "vit", "resnet", "resnet-50", "vgg", "vgg-16", "alexnet", "inception",
    "densenet", "efficientnet", "convnext", "swin", "dino", "dinov2",
    "detr", "yolo", "whisper", "wav2vec", "t5", "flan-t5", "bart", "dall-e",
    "sora", "flamingo", "alphafold", "prithvi", "terramind", "sybil", "u-net",
))

GROUPS = {"xai": XAI, "behaviour": BEHAVIOUR, "finding": FINDING,
          "method": METHOD, "model": MODEL}

REGISTRY_WEIGHT = 2.5

TERM_TYPES = (graph_json.METHOD, graph_json.MODEL, graph_json.CONCEPT,
              graph_json.DATASET)

MIN_REGISTRY_TERM = 5

UNKNOWN_VERSION = "unknown"


def word_pattern(words):
    joined = "|".join(sorted((re.escape(word) for word in words), key=len, reverse=True))
    return re.compile(r"(?<![a-z0-9])(?:%s)(?![a-z0-9])" % joined)


PATTERNS = {name: word_pattern(group.words) for name, group in GROUPS.items() if group.words}

VERSION_LENGTH = 12


def fingerprint(groups, knobs):
    tuned = {
        "groups": {name: [group.weight, sorted(group.stems), sorted(group.words)]
                   for name, group in groups.items()},
        "knobs": dict(knobs),
    }
    packed = json.dumps(tuned, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(packed.encode("utf-8")).hexdigest()[:VERSION_LENGTH]


def registry_terms(entities):
    terms = set()
    for key, entity in (entities or {}).items():
        if entity.get("type") not in TERM_TYPES:
            continue
        for alias in link.aliases(entity.get("name") or ""):
            candidate = textutil.flatten(alias)
            if len(candidate) >= MIN_REGISTRY_TERM:
                terms.add(candidate)
    return frozenset(terms)


def haystack(title, abstract, keywords=()):
    parts = [title or "", abstract or ""]
    parts += [str(word) for word in (keywords or [])]
    return textutil.normalise(" \n ".join(parts))


def hits_in(field, group, name):
    found = [stem for stem in group.stems if stem in field]
    pattern = PATTERNS.get(name)
    if pattern:
        found += sorted(set(pattern.findall(field)))
    return [Signal(name, term) for term in dict.fromkeys(found)]


def tier_of(score):
    if score >= STRONG_AT:
        return STRONG
    if score >= POSSIBLE_AT:
        return POSSIBLE
    return WEAK


RULES_VERSION = fingerprint(GROUPS, {"strong_at": STRONG_AT, "possible_at": POSSIBLE_AT,
                                     "per_group": PER_GROUP,
                                     "registry_weight": REGISTRY_WEIGHT,
                                     "min_registry_term": MIN_REGISTRY_TERM})


def screen(title, abstract, keywords=(), terms=frozenset()):
    field = haystack(title, abstract, keywords)
    signals, score = [], 0.0

    for name, group in GROUPS.items():
        found = hits_in(field, group, name)
        signals += found
        score += group.weight * min(len(found), PER_GROUP)

    packed = textutil.flatten(field)
    known = [term for term in terms if term in packed]
    signals += [Signal("registry", term) for term in sorted(known)]
    score += REGISTRY_WEIGHT * min(len(known), PER_GROUP)

    return Screening(score=round(score, 2), tier=tier_of(score), signals=tuple(signals))
