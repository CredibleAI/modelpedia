import collections
import hashlib
import json
import math
import re
from typing import NamedTuple

from modelpedia.ingest import text as textutil

STRONG = "strong"
POSSIBLE = "possible"
WEAK = "weak"

STRONG_AT = 8.0
POSSIBLE_AT = 4.0

CONSENSUS = 0.5

UNKNOWN_VERSION = "unknown"


class Group(NamedTuple):
    weight: float
    cap: int
    stems: tuple
    words: tuple


class Rules(NamedTuple):
    name: str
    groups: dict
    patterns: dict


class Signal(NamedTuple):
    group: str
    term: str


class Screening(NamedTuple):
    score: float
    tier: str
    signals: tuple
    subscores: tuple

    def groups(self):
        return tuple(dict.fromkeys(signal.group for signal in self.signals))

    def points(self, group):
        return dict(self.subscores).get(group, 0.0)


XAI = Group(2.0, 2, (
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

BEHAVIOUR = Group(1.0, 2, (
    "failure mode", "failure case", "shortcut", "clever hans", "spurious",
    "stereotyp", "memoriz", "memoris", "hallucinat", "sycophan", "jailbreak",
    "backdoor", "trojan", "confound", "contaminat", "leakage", "brittle",
    "degradat", "out-of-distribution", "distribution shift", "calibrat",
    "overconfiden", "error analysis", "systematic error", "artifact", "artefact",
    "unfaithful", "emergent", "generaliz", "robustness", "limitation",
    "unintended", "undesirab", "misclassif", "blind spot", "corner case",
), ("bias", "biased", "biases", "fairness", "capability", "capabilities"))

FINDING = Group(1.0, 2, (
    "we find", "we show that", "we demonstrate that", "we observe", "we reveal",
    "we analyz", "we analys", "we investigate", "we examine", "we audit",
    "we characteriz", "we quantify", "we uncover", "we discover", "we probe",
    "our analysis", "our findings", "reveals that", "suggests that",
    "case study", "empirical study", "empirical analysis", "sheds light",
    "surprisingly", "counterintuitive", "contrary to", "we ask whether",
    "we test whether", "we evaluate whether", "diagnos",
), ("audit", "audits", "understanding"))

METHOD = Group(-0.5, 2, (
    "we propose", "we introduce", "we present a novel", "we develop",
    "our method", "our approach", "our framework", "novel framework",
    "state-of-the-art", "outperforms", "we design a", "new architecture",
), ("sota",))

MODEL = Group(2.0, 2, (
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

ANALYSIS = Group(1.0, 4, (
    "analyz", "investigat", "empirical stud", "systematic stud",
    "case stud", "audit", "characteriz", "characteris", "quantif",
    "understand how", "understand why", "understand the",
    "reveals that", "reveal that", "finds that", "find that", "shows that",
    "observes that", "demonstrates that", "surprising", "counterintuitive",
    "contrary to", "diagnos", "probe", "probing", "dissect", "inspect",
    "behaviour of", "behavior of", "properties of", "geometry of", "structure of",
    "emerges", "emergent", "does not", "fails to", "cannot", "is not",
    "revisit", "rethink", "re-examine", "myth", "illusion", "pitfall",
), ())

PROPOSES = Group(-2.0, 3, (
    "the proposed", "proposed method", "proposed approach", "proposed framework",
    "proposed model", "proposed algorithm", "propose a", "proposes a", "propose an",
    "proposes an", "introduce a", "introduces a", "present a novel", "presents a novel",
    "novel method", "novel approach", "novel framework", "novel architecture",
    "new method", "new approach", "new framework", "new architecture", "new algorithm",
    "outperform", "state-of-the-art", "sota", "achieves better", "improves over",
    "our method", "we propose",
), ())

STOPTERMS = ("generaliz", "limitation", "robustness", "outperforms",
             "state-of-the-art", "capability", "capabilities", "bias",
             "biased", "biases")


def word_pattern(words):
    joined = "|".join(sorted((re.escape(word) for word in words), key=len, reverse=True))
    return re.compile(r"(?<![a-z0-9])(?:%s)(?![a-z0-9])" % joined)


def retuned(group, weight, cap, dropped=()):
    return Group(weight, cap,
                 tuple(stem for stem in group.stems if stem not in dropped),
                 tuple(word for word in group.words if word not in dropped))


def ruleset(name, groups):
    return Rules(name, groups,
                 {key: word_pattern(group.words)
                  for key, group in groups.items() if group.words})


ABSTRACT = ruleset("abstract", {
    "xai": XAI,
    "behaviour": BEHAVIOUR,
    "finding": FINDING,
    "method": METHOD,
    "model": MODEL,
})

REVIEW = ruleset("review", {
    "r-xai": retuned(XAI, 2.0, 3, STOPTERMS),
    "r-behaviour": retuned(BEHAVIOUR, 1.0, 2, STOPTERMS),
    "r-analysis": ANALYSIS,
    "r-proposes": PROPOSES,
    "r-model": retuned(MODEL, 1.5, 2),
})

RULESETS = (ABSTRACT, REVIEW)

VERSION_LENGTH = 12


def fingerprint(rulesets, knobs):
    tuned = {rules.name: {name: [group.weight, group.cap,
                                 sorted(group.stems), sorted(group.words)]
                          for name, group in rules.groups.items()}
             for rules in rulesets}
    packed = json.dumps({"rules": tuned, "knobs": dict(knobs)},
                        sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(packed.encode("utf-8")).hexdigest()[:VERSION_LENGTH]


RULES_VERSION = fingerprint(RULESETS, {"strong_at": STRONG_AT, "possible_at": POSSIBLE_AT,
                                       "consensus": CONSENSUS})


def haystack(title, abstract, keywords=()):
    parts = [title or "", abstract or ""]
    parts += [str(word) for word in (keywords or [])]
    return textutil.normalise(" \n ".join(parts))


def terms_in(field, group, pattern):
    found = [stem for stem in group.stems if stem in field]
    if pattern:
        found += sorted(set(pattern.findall(field)))
    return dict.fromkeys(found)


def agreed(fields, group, pattern, consensus):
    seen = [terms_in(field, group, pattern) for field in fields]
    counted = collections.Counter(term for found in seen for term in found)
    need = max(1, math.ceil(consensus * len(fields)))
    ordered = list(group.stems) + sorted(set(counted) - set(group.stems))
    return [term for term in ordered if counted[term] >= need]


def tier_of(score):
    if score >= STRONG_AT:
        return STRONG
    if score >= POSSIBLE_AT:
        return POSSIBLE
    return WEAK


def assess(rules, fields, consensus=CONSENSUS):
    signals, subscores, score = [], [], 0.0
    for name, group in rules.groups.items():
        found = agreed(fields, group, rules.patterns.get(name), consensus)
        signals += [Signal(name, term) for term in found]
        points = group.weight * min(len(found), group.cap)
        subscores.append((name, round(points + 0.0, 2)))
        score += points
    return Screening(round(score, 2), tier_of(score), tuple(signals), tuple(subscores))


def screen(title, abstract, keywords=()):
    return assess(ABSTRACT, [haystack(title, abstract, keywords)])


def review_screen(texts):
    return assess(REVIEW, [textutil.normalise(text) for text in texts if str(text or "").strip()])


def side_score(subscores, rules):
    return round(sum(points for name, points in dict(subscores).items()
                     if name in rules.groups), 2)


def combine(*screenings):
    score = round(sum(one.score for one in screenings), 2)
    return Screening(score, tier_of(score),
                     tuple(signal for one in screenings for signal in one.signals),
                     tuple(pair for one in screenings for pair in one.subscores))
