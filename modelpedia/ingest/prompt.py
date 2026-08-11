import yaml

from modelpedia import record_keys as keys
from modelpedia.ingest import text as textutil

MAX_PAPER_CHARS = 160000
OMITTED = "\n\n[... middle of the paper omitted ...]\n\n"
CONCEPT_SUMMARY = 340

DROPPED_FROM_EXAMPLES = ("id", "review_status", "extracted_by", "related_findings", "sources")
FREE_TEXT = ("description", "key_metric", "caveat")
LINKED = ("models", "methods", "datasets", "related_work", "concepts")

TASK = """You extract findings for Modelpedia, a structured database of findings about machine
learning models.

A finding is a third-party, post-hoc claim about how a SPECIFIC, PRE-EXISTING model behaves.
The paper below is the source. Your job is to decide whether it contains any such claim, and if
so, to write one record per claim.

FIRST TEST, apply it before anything else. Name the model the claim is about, then ask:

    Did this model exist, under this name, before this paper was written, so that a reader
    could obtain or cite it independently of this paper?

Apply this test to EACH MODEL SEPARATELY, never to the paper as a whole. What the paper
contributes is irrelevant; what matters is which models the claim is about. A paper introducing
a brand-new benchmark, method or tool still qualifies whenever it measures released models --
run the test on the models it measures, not on the thing the paper contributes.

If the answer is no for a given model, drop that model. If it is no for every model in a claim,
the claim does not qualify. This test is strict and it fails many papers:

- Authors who TRAIN their own networks fail it, even when they use a well-known architecture.
  "We train GPT-2-small on our synthetic task" is a model the authors made. "GPT-2-small" here
  names a size, not the released checkpoint. This is the single most common mistake.
- Models the authors produced by fine-tuning, merging or adapting a released model fail it.
  "Llama-2-chat-VL-mtl", built by the authors from Llama-2-chat, is their model. List the
  released base only when the claim is about the base itself.
- Toy or illustrative networks trained to demonstrate a theory fail it.
- A model introduced by this very paper fails it, however well it performs.

Worked example of the common trap. A paper introduces the NEW-BENCH benchmark and reports that
GPT-4o and Claude score below a human baseline on it. NEW-BENCH is new, so it is not a model and
the test does not apply to it; it goes under `datasets`. GPT-4o and Claude are released, so they
pass. This paper yields one finding, about GPT-4o and Claude. Returning `findings: []` here is
wrong.

What counts:
- A claim about a released model: CLIP, Llama-3-8B, GPT-4o, Gemma, a published ViT checkpoint.
- A benchmark or evaluation paper measuring how released models perform. The benchmark is the
  instrument; the measured behaviour of those models is the finding.
- A method paper that, while presenting its tool, reports what a released model does. Extract
  the claim about the model, not the claim about the tool.
- A negative or null result. "X does not hold for Llama-3" is a finding.

What does not count:
- Anything failing the first test above.
- A property of an algorithm, optimiser, or training procedure rather than of a model.
- A dataset, framework or infrastructure contribution with no measured claim about a model.

If the paper contains no qualifying claim, still fill `considered` in full, then write
`findings: []`. The `considered` block is what makes an empty result checkable, so an empty
result without it is not acceptable.

Papers with no qualifying claim are common and an empty result is a correct, useful answer.
Do not invent a finding to fill the space, and do not stretch the first test to avoid an empty
result.
"""

RULES = """Output rules.

Return YAML only. No prose before or after, no code fences, no commentary.

Wrap every field holding a sentence in double quotes: `title`, `description`, `key_metric`,
`caveat`, `finding`, `why`, `definition` and `instead_of`. Their text often contains a colon, and
unquoted YAML breaks on it.

Top level has up to four keys, in this order: `considered`, `findings`, `entities`,
`concepts_considered`. The last is required whenever any finding has `concepts: []`.

`considered` comes first and is ALWAYS present, including when `findings` is empty. It is your
worked answer to the first test, one entry per model the paper measures or analyses:

considered:
- model: "GPT-4o"
  released: true
  why: "OpenAI model, public API, predates this paper"
- model: "NEW-BENCH"
  released: false
  why: "not a model, it is the benchmark this paper introduces"

List every model the paper puts numbers on, including ones you end up rejecting. Judge each on
its own. If any entry has `released: true`, the paper almost certainly yields a finding about
that model, and returning an empty `findings` would be a mistake. Fill `considered` before you
write anything else.

`findings` holds a list, one item per distinct claim.

HOW MANY. Most qualifying papers yield exactly one. Two is uncommon. Three is the hard maximum
and needs three genuinely separate mechanisms, not three measurements of one mechanism. A result
at four scales, on three datasets, or under two prompting regimes is ONE finding whose
`key_metric` carries the range. When unsure, merge.

Every item has exactly these keys:

  title          One sentence, the claim itself, specific enough to be checked against the paper.
                 Not the paper's title. Name the model and the behaviour.
  description    3-5 sentences in your own words. What was measured, on what, and what came out.
  evidence_type  One of: observational, correlational, interventional.
                 observational   - described, nothing measured against a reference
                 correlational   - a quantified relationship, nothing manipulated
                 interventional  - THE MODEL ITSELF was altered and the effect measured:
                                   weights edited, activations patched or steered, a head or
                                   layer ablated, a component removed.
                 Changing the input is NOT intervening on the model. Different prompts, more
                 in-context examples, another dataset, a new decoding setting: these are
                 correlational. Comparing two released checkpoints is correlational, not
                 interventional. When torn between correlational and interventional, choose
                 correlational.
  key_metric     The numbers that carry the claim, quoted from the paper with their units and
                 conditions. Copy digits exactly as printed. Never read a value off a figure and
                 never write an approximation such as "~0.72": if the number is not printed in
                 the text or a table, leave it out. Empty string if the paper reports none.
  caveat         What weakens the claim, in the authors' own terms where possible: stated
                 limitations, confounds, narrow conditions. Empty string if none is stated.
  models         The released models the claim is about. Plain names as written in the paper.
                 `variant` only when the paper names a specific released checkpoint, such as
                 "ViT-B/32" or "Llama-3-8B-Instruct". A layer or head count is a configuration,
                 not a variant: never write "3 layers 3 heads".
  concepts       List of concept ids chosen ONLY from the closed list below, each written as a
                 plain string carrying the full id: `["concept:shortcut"]`. Never a bare slug
                 such as "shortcut", never a mapping such as {concept: shortcut}. Use [] when
                 none fits, and then account for it in `concepts_considered`. Never invent an id.
  methods        List of {name, role} where role is one of: primary, compared-to, validation,
                 eval, supporting. Omit role when the paper gives no particular role.
  datasets       List of {name, role} where role is one of: train, eval, source.
                 Benchmarks belong here, not under methods.
  related_work   List of {name, role} where role is one of: builds-on, compared-to, context.
                 Papers, models or methods this work positions itself against.

THE `entities` BLOCK, third top-level key. Present whenever `findings` is non-empty.

One entry per distinct name you used in any `models`, `methods`, `datasets` or `related_work`
field, listed once even when several findings mention it:

entities:
- name: "Grad-CAM"
  kind: method
  citation: "R. R. Selvaraju, M. Cogswell, A. Das, R. Vedantam, D. Parikh, and D. Batra. Grad-CAM: Visual explanations from deep networks via gradient-based localization. In ICCV, 2017."
  artifact: ""
- name: "MS COCO"
  kind: dataset
  citation: "Tsung-Yi Lin, Michael Maire, Serge Belongie, et al. Microsoft COCO: Common objects in context. arXiv preprint arXiv:1405.0312, 2014."
  artifact: "https://cocodataset.org"

  kind      one of: model, method, dataset, related_work
  citation  the reference line for this thing, COPIED VERBATIM out of this paper
  artifact  where it can be obtained or run, only if the paper prints such a URL in full

THE CITATION RULE. This is the strictest rule in the whole task, and the one most likely to be
broken without anyone noticing.

**`citation` must be a literal copy of text that appears in THIS paper.** Go to the paper's
reference list, find the entry, copy the line. Copy it as printed, including author initials and
venue. Do not tidy it, do not reformat it, do not translate an abbreviated venue into a full one.

A previous run of this task was asked for URLs instead, and wrote them from memory. Roughly a
third pointed at completely unrelated work: a paper on colloidal coagulation stood in for a
vision-language model, a paper on graphene heterostructures stood in for an image dataset. Every
one of them was well-formed and passed every automated check. This is why you are being asked for
a copy instead of an identifier: a copy can be checked against the paper, an identifier cannot.

Therefore:
- If the paper's reference list has no entry for this thing, write an empty string. Common for
  datasets and models the paper only names in passing. An empty citation is correct.
- Never reconstruct a citation you believe to be true. Believing is not copying.
- Never write an arXiv number, DOI or URL that you did not read in this paper.
- If the entry is cut off in the extracted text, copy the part you can actually see rather than
  completing it.

`artifact` follows the same rule: a URL only if the paper prints it in full. The extracted text
often truncates long URLs mid-string, for example "https://www.adept." -- if that is all you can
see, leave `artifact` empty.

THE `concepts_considered` BLOCK, fourth top-level key. Required whenever any finding has
`concepts: []`. Omit it only when every finding took a concept from the closed list.

The concept list is closed on purpose and small on purpose. Twelve concepts cover twenty
findings, and four of them are used once. If none fits a finding, `concepts: []` is the correct
answer and you should give it without hesitation -- but never silently. Write one entry here for
EVERY finding whose `concepts` you left empty, saying what you weighed and how it came out. An
empty list nobody accounted for is the one thing this block exists to prevent.

FIRST, CHECK THAT YOU ARE APPLYING THE RIGHT BAR TO THE CLOSED LIST. A concept is meant to be
BROADER than any one finding. That breadth is the whole point: it is what lets findings about
different models meet on the same node, and the specific mechanism is never lost, because it
survives in the finding's own `title` and `description`.

So "the concept is too broad" is not a reason to refuse it, and neither is "related, but not a
perfect fit". Take the concept whenever the finding is a SPECIAL CASE of it. Refuse only when the
finding fits under NOTHING in the list -- not when it fits under something imperfectly.

Where that line falls, in one pair. A finding that some property sharpens gradually across layers
IS `concept:depth-dependent-structure`, even when the paper's own claim is narrower and more
specific than that sentence. A finding that one circuit sits in a particular range of layers is
NOT: there, nothing is progressing across depths, and the concept genuinely does not apply.

WHAT DECIDES IS THE DEFINITION, NEVER THE TITLE. Read the concept's definition in the list above
and find the phrase of it that the finding satisfies. If you cannot point at such a phrase, you do
not have a fit, however close the two names sound. Being broad is not the same as being elastic:
a broad concept still says something specific, and a finding either meets it or does not.

The two ways this goes wrong, both worth checking before you write a concept id:

- `concept:method-artefact` is about the APPARATUS used to study the model, not about the model.
  A property of the model itself is never one, however surprising the property is.
- `concept:shortcut` is reliance on a feature that correlates with the target without causing it.
  A response pattern, a null result, a performance gap or a data imbalance is not a shortcut,
  even when the paper calls the behaviour a bias.

Write an entry in this block ONLY for a finding whose `concepts` you left empty. A finding that
took a concept needs no entry, and no note explaining the choice.

Each entry answers for exactly one finding and takes one of two shapes.

Nothing fits and no new concept earns its place. This is the common case:

concepts_considered:
- finding: "GPT-4o answers change with the position of the correct option"
  closest: "concept:shortcut"
  why: "The effect is real, but the mechanism is this paper's own retrieval prompt, and a finding
    about another model would never be tagged with it."

Nothing fits and the mechanism will recur across other papers and other models. Propose it:

- finding: "GPT-4o answers change with the position of the correct option"
  closest: "concept:shortcut"
  name: "Positional bias"
  definition: "The model's output depends on where an item sits in its input rather than on what
    the item is: the same option scores differently as answer A than as answer D, the same
    document is weighted differently first than last."
  instead_of: "concept:shortcut is the closest, but a shortcut is reliance on a spurious feature
    of the data, and position is a property of the prompt, not of the data."

  finding      copy the finding's `title`, so the entry can be matched back to it
  closest      the nearest id from the closed list, always present, even where it does not fit
  why          refusal only, one sentence: why the closest does not cover this and why no new
               concept earns its place
  name         proposal only, two or three words, the mechanism, not the task or the domain
  definition   proposal only, two to four sentences, written so someone who has not read this
               paper could decide whether a different paper's finding belongs under it
  instead_of   proposal only, name the closest existing concept and say why it does not cover this

An entry carrying a `name` is a proposal; an entry without one is a refusal. Both are complete
answers. Leaving a finding out of this block is not an answer at all.

The bar for a proposal is high and it has not moved. A concept is a MECHANISM by which a model
behaves as it does. These are concepts: shortcut reliance, linear representation, scale-dependent
behaviour. These are NOT concepts, and proposing them is the most common mistake:
- a task or domain: "question answering", "medical imaging", "code generation"
- a method: "sparse autoencoders", "activation patching"
- a metric or an outcome: "low accuracy", "high perplexity"
- a restatement of `evidence_type`: "causal intervention", "correlational finding"
- anything that fits one paper and no other

Propose at most one concept per paper. A proposal that only ever applies to this one paper is
worse than a refusal, because it inflates the vocabulary that findings are supposed to join on.
When torn, refuse and say why. A refusal costs nothing and there is no limit on refusals.

WHAT DESERVES TO BE AN ENTITY. Each name you write becomes a permanent node in a shared
registry, so the bar is high. Include a name only if it passes both tests:

  1. It is citable. It has a paper, a repository or a release behind it, and a reader could
     look it up without this paper.
  2. It is reusable. A different paper, on a different model, could plausibly refer to the
     same thing by the same name.

These FAIL and must be left out entirely:
  - optimisers and training settings: Adam, early stopping, learning-rate warmup, a batch size
  - implementation details: "CUDA implementation of X", "our training recipe"
  - names of experiment sections or evaluation procedures the paper invented for itself:
    "triplet setup", "automatic evaluation", "human evaluation", "attention heatmap analysis"
  - one-off parameterisations of one underlying method. If a paper defines AFC3, AFC4 and BFC3
    as variations of one curvature measure, write the underlying measure once.

Put things in the right field. A neural network is a model even when the paper uses it as a
tool: DQN, Dreamer, a GCN, a VLM used as a judge all go under `models`, never under `methods`.
A named benchmark goes under `datasets`. `methods` is for techniques of analysis, training or
explanation: integrated gradients, activation patching, linear probing, DPO.

Write plain names, NOT identifiers. Write "Grad-CAM", not "method:grad-cam". A separate
deterministic step matches your names against the registries; inventing an identifier breaks it.

Two hard rules:
- Every number in `key_metric` must appear in the paper text. Do not round, rescale or combine.
- If the paper does not state something, leave the field empty. Never fill a gap with a guess.
"""


def squeezed(value):
    return " ".join(str(value or "").split())


def concept_block(concepts):
    lines = []
    for key, entry in sorted(concepts.items()):
        summary = squeezed((entry or {}).get("description"))[:CONCEPT_SUMMARY]
        lines.append("  %s  (%s)\n      %s" % (key, (entry or {}).get("name"), summary))
    return "\n".join(lines)


def plain_links(items, names):
    out = []
    for item in items or []:
        if not isinstance(item, dict):
            out.append(item)
            continue
        ref = str(item.get(keys.REF) or "")
        if ref.startswith("concept:"):
            out.append(ref)
            continue
        entry = {"name": names.get(ref, ref)}
        if item.get(keys.ROLE):
            entry["role"] = item[keys.ROLE]
        if item.get(keys.VARIANT):
            entry["variant"] = names.get(item[keys.VARIANT], item[keys.VARIANT])
        out.append(entry)
    return out


def considered_from(record):
    return [{"model": item["name"], "released": True,
             "why": "released checkpoint, cited independently of this paper"}
            for item in record.get("models") or [] if isinstance(item, dict)]


def as_example(record, names):
    shown = {key: value for key, value in record.items() if key not in DROPPED_FROM_EXAMPLES}
    for field in LINKED:
        shown[field] = plain_links(record.get(field), names)
    for field in FREE_TEXT:
        shown[field] = squeezed(shown.get(field))
    return yaml.safe_dump({"considered": considered_from(shown), "findings": [shown]},
                          allow_unicode=True, sort_keys=False, width=95).strip()


def clipped(body, limit=MAX_PAPER_CHARS):
    if len(body) <= limit:
        return body, False
    return body[:limit // 2] + OMITTED + body[-(limit // 2):], True


def build(title, raw_text, concepts, examples, names, limit=MAX_PAPER_CHARS):
    body, truncated = clipped(textutil.normalise(raw_text), limit)
    parts = [
        TASK,
        "Closed list of concepts. Choose only from these, or use [].\n",
        concept_block(concepts),
        "\n" + RULES,
        "\nTwo worked examples of `considered` and `findings`, both hand-verified against their",
        "sources. Their `entities` blocks are omitted here; follow the citation rules above.\n",
        "\n\n".join(as_example(record, names) for record in examples),
        "\n\nPaper title: %s" % title,
        "Paper text follows. It is machine-extracted, so hyphenation and spacing may be odd.\n",
        "-----BEGIN PAPER-----",
        body,
        "-----END PAPER-----",
        "\nReturn the YAML now, and nothing else.",
    ]
    return "\n".join(parts), truncated
