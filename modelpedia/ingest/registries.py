import re

import yaml

from modelpedia import graph as graph_json
from modelpedia import paths
from modelpedia.ingest import adoption
from modelpedia.ingest import link

FILES = {"models": "models.yaml", "datasets": "datasets.yaml", "methods": "methods.yaml"}
PREFIX = {"models": graph_json.MODEL, "datasets": graph_json.DATASET,
          "methods": graph_json.METHOD}

ALIAS_FILE = {graph_json.MODEL: "models", graph_json.VARIANT: "models",
              graph_json.DATASET: "datasets", graph_json.METHOD: "methods"}


def path_for(field):
    return paths.REGISTRIES / FILES[field]


def taken(field, entities):
    prefix = PREFIX[field]
    return {key for key in entities if key.startswith("%s:" % prefix)} | {
        key for key in entities if key.startswith("%s:" % graph_json.VARIANT)}


def free_slug(prefix, slug, used):
    key = "%s:%s" % (prefix, slug)
    number = 2
    while key in used:
        key = "%s:%s-%d" % (prefix, slug, number)
        number += 1
    used.add(key)
    return key


def append(field, blocks):
    if not blocks:
        return
    path = path_for(field)
    body = path.read_text(encoding="utf-8").rstrip()
    path.write_text(body + "\n\n" + "\n\n".join(blocks) + "\n", encoding="utf-8")


def dumped(key, entry):
    return yaml.safe_dump({key: entry}, allow_unicode=True, sort_keys=False, width=98).rstrip()


def insert_variant(model_key, variant_key, name):
    """Into the model's own `variants:` block, which is where a checkpoint lives. Written as a
    text insertion rather than a load-and-dump so the rest of a hand-curated file keeps the
    shape its author gave it."""
    path = path_for("models")
    lines = path.read_text(encoding="utf-8").splitlines()
    start = next((i for i, line in enumerate(lines) if line == "%s:" % model_key), None)
    if start is None:
        return False
    end = next((i for i in range(start + 1, len(lines))
                if lines[i] and not lines[i][0].isspace()), len(lines))
    block = next((i for i in range(start, end) if lines[i].strip() == "variants:"), None)
    entry = ["    %s:" % variant_key, "      name: %s" % name]
    if block is None:
        lines[end:end] = ["  variants:"] + entry
    else:
        stop = next((i for i in range(block + 1, end) if not lines[i].startswith("    ")), end)
        lines[stop:stop] = entry
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True


def add_fields(field, key, values):
    """Extra keys onto an entry that already exists, right after its `name:`. A text insertion
    for the same reason as `insert_variant`: the rest of a hand-curated file keeps its shape."""
    path = path_for(field)
    lines = path.read_text(encoding="utf-8").splitlines()
    at = next((i for i, line in enumerate(lines) if line.rstrip() == "%s:" % key), None)
    if at is None:
        return False
    name_at = next((i for i in range(at + 1, len(lines))
                    if lines[i].strip().startswith("name:")), None)
    if name_at is None:
        return False
    block = []
    for name, items in values.items():
        block.append("  %s:" % name)
        block += ["  - %s" % item for item in items]
    if not block:
        return False
    lines[name_at + 1:name_at + 1] = block
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True


def set_anchor(field, key, anchor):
    """An anchor onto an entry that has none. Refuses to touch an entry that already carries one:
    a proposal is evidence that a URL fits a citation, never that it fits the entity better than
    what a human already put there. Returns False when it changed nothing, so the caller can
    count what it actually wrote instead of what it meant to write."""
    path = path_for(field)
    lines = path.read_text(encoding="utf-8").splitlines()
    at = next((i for i, line in enumerate(lines) if line.rstrip() == "%s:" % key), None)
    if at is None:
        return False
    end = next((i for i in range(at + 1, len(lines))
                if lines[i] and not lines[i].startswith(" ")), len(lines))
    held = next((i for i in range(at + 1, end) if lines[i].strip().startswith("anchor:")), None)
    if held is not None:
        if lines[held].split(":", 1)[1].strip() not in ("", "null", "~"):
            return False
        lines[held] = "  anchor: %s" % anchor
    else:
        name_at = next((i for i in range(at + 1, end)
                        if lines[i].strip().startswith("name:")), None)
        if name_at is None:
            return False
        lines.insert(name_at + 1, "  anchor: %s" % anchor)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True


def add_alias(field, key, spelling):
    path = path_for(field)
    lines = path.read_text(encoding="utf-8").splitlines()
    at = next((i for i, line in enumerate(lines) if line.strip() == "%s:" % key), None)
    if at is None:
        return False
    name_at = next((i for i in range(at + 1, len(lines))
                    if lines[i].strip().startswith("name:")), None)
    if name_at is None:
        return False
    written = lines[name_at]
    if spelling.lower() in written.lower():
        return False
    lines[name_at] = written.rstrip() + " / " + spelling
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return True


CHECKPOINT = re.compile(
    r"[-\s_](?:\d+x\d+b|\d+(?:\.\d+)?[bkm]|v\d+(?:\.\d+)*|mini|preview|small|base|large|xl|"
    r"tiny|chat|it|instruct|turbo|opus|sonnet|haiku|flash|pro)\b", re.I)


def family_stem(title):
    """What is left of a checkpoint name once the size and the tuning fall away: `Vicuna-7B-v1.5`
    and `Vicuna-13B` are both `vicuna`.

    A bare version number is deliberately NOT stripped, because it is as often part of the family
    name as of the checkpoint: `GPT-2` and `Llama 3.1` are families, `Vicuna-7B` is not. Stripping
    it turned `GPT-2` into `gpt` and would have merged two unrelated releases. Only sizes, point
    versions after a size, and tuning words go."""
    return link.slugify(CHECKPOINT.sub("", title)).strip("-")


def regrouped(adopted):
    """One family per stem, not one per checkpoint. The model is asked about each name on its own,
    so when a family is absent from the registry every one of its checkpoints answers `new` and
    the registry ends up with four Vicunas instead of one with four variants. Measured 2026-08-21:
    8 spurious families out of 26. The shortest title in a group names the family; where no
    proposal carries the bare family name, the stem does."""
    groups = {}
    for verdict in adopted:
        if verdict.field == "models" and verdict.family == "new":
            groups.setdefault(family_stem(verdict.title), []).append(verdict)
    placed = {}
    for stem, members in groups.items():
        if len(members) < 2 or not stem:
            continue
        head = min(members, key=lambda item: (len(item.title), item.title))
        if family_stem(head.title) == link.slugify(head.title).strip("-"):
            family_key = "%s:%s" % (graph_json.MODEL, link.slugify(head.title))
            rest = [item for item in members if item is not head]
        else:
            family_key = "%s:%s" % (graph_json.MODEL, stem)
            rest = members
        for item in rest:
            placed[item.name] = family_key
    return placed


def apply(verdicts, held, entities):
    """Every adopted entry that is not already in the registry, written where its kind belongs.
    A refusal writes nothing except an alias, which is the one case where a refusal still carries
    information: the thing is here already, under another spelling."""
    done, skipped, aliases = [], [], []
    fresh = {field: [] for field in FILES}
    used = {field: taken(field, entities) for field in FILES}
    adopted = [item for item in verdicts if item.adopted() and item.name not in held]
    merged = regrouped(adopted)

    variants, standalone = [], []
    for verdict in sorted(adopted, key=lambda item: (item.field, item.name.lower())):
        if verdict.field not in FILES:
            continue
        family = merged.get(verdict.name) or verdict.family
        if verdict.field == "models" and family and family != "new":
            variants.append((family, verdict))
        else:
            standalone.append(verdict)

    named_here = set()
    indexes = {field: link.index_of(entities, PREFIX[field]) for field in FILES}
    written_titles = {}
    for verdict in standalone:
        title_key = (verdict.field, link.normalise(verdict.title))
        twin = written_titles.get(title_key)
        if twin is None:
            found = link.resolve(verdict.title, indexes[verdict.field])
            twin = found.slug if found.kind == link.HIT else None
        if twin:
            aliases.append((verdict.field, twin, verdict.name))
            done.append("%s: ta sama nazwa co %s, dopisany jako alias" % (verdict.name, twin))
            continue
        key = free_slug(PREFIX[verdict.field], adoption.slug_for(verdict.title, verdict.name),
                        used[verdict.field])
        fresh[verdict.field].append(dumped(key, adoption.entry_for(verdict)))
        named_here.add(key)
        written_titles[title_key] = key
        done.append("%s%s" % (key, "" if verdict.anchor else "  (bez anchora)"))
    for family_key in sorted({family for family, _ in variants}):
        if family_key in entities or family_key in named_here:
            continue
        fresh["models"].append(dumped(family_key, {"name": family_key.split(":", 1)[1].title()}))
        used["models"].add(family_key)
        done.append("%s  (rodzina zlozona z wariantow)" % family_key)

    for field, blocks in fresh.items():
        append(field, blocks)

    for family_key, verdict in variants:
        key = free_slug(graph_json.VARIANT, adoption.slug_for(verdict.title, verdict.name),
                        used["models"])
        if insert_variant(family_key, key, verdict.title):
            done.append("%s pod %s" % (key, family_key))
        else:
            skipped.append((verdict.name, "%s: nie ma takiej rodziny w pliku" % family_key))

    for field, key, spelling in aliases:
        add_alias(field, key, spelling)
    for verdict in verdicts:
        if verdict.decision != adoption.REFUSE or not verdict.alias_of or verdict.problem:
            continue
        field = ALIAS_FILE.get(verdict.alias_of.split(":")[0])
        if not field:
            skipped.append((verdict.name, verdict.alias_of))
            continue
        if add_alias(field, verdict.alias_of, verdict.name):
            done.append("alias %s -> %s" % (verdict.name, verdict.alias_of))
    for name, target in skipped:
        done.append("alias %s -> %s pominiety: to nie jest rejestr, do ktorego piszemy"
                    % (name, target))
    return done
