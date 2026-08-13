from collections import Counter
from typing import NamedTuple

from modelpedia.console import plural


class Proposed(NamedTuple):
    found: tuple
    kept: tuple
    families: tuple
    concepts: object
    unknown: tuple
    misshapen: tuple
    papers: int
    least: int


class Split(NamedTuple):
    kept: tuple
    dropped: tuple
    refused: tuple
    papers: int
    entries: int
    used: int


def render(blocks):
    lines = []
    for block in blocks:
        if not block:
            continue
        lines += ([""] if lines else []) + block
    return "\n".join(lines)


def proposal_summary(proposed):
    spread = Counter(item.reach() for item in proposed.found)
    states = Counter(item.state for item in proposed.found)
    return [
        "%d proposed entities from %d papers, %d reaching %d paper(s) or more"
        % (len(proposed.found), proposed.papers, len(proposed.kept), proposed.least),
        "  reach: %s" % ", ".join("%d paper(s) x%d" % (reach, count)
                                  for reach, count in sorted(spread.items(), reverse=True)),
        "  citation: %s" % ", ".join("%s %d" % pair for pair in sorted(states.items())),
    ]


def families(proposed):
    if not proposed.families:
        return []
    lines = ["related names that belong to one decision, resolve each group together:"]
    for family in proposed.families:
        lines.append("  %s" % family.stem)
        lines += ["      %-52s %d paper(s)" % (member.name[:52], member.reach())
                  for member in family.members]
    return lines


def undecided(proposed):
    close = [item for item in proposed.kept if item.candidates]
    if not close:
        return []
    return ["close to something already in a registry, a human decides:"] + [
        "  %-40s -> %s" % (item.name[:40], ", ".join(item.candidates)) for item in close]


def concept_summary(proposed):
    concepts = proposed.concepts
    lines = ["concepts: %s with none, %d answered, %d unanswered"
             % (plural(concepts.without_concept, "finding"),
                concepts.answered(), len(concepts.silent))]
    if concepts.stray:
        lines.append("  %d entr(ies) answer for a finding that did take a concept, not counted"
                     % concepts.stray)
    return lines


def off_list(proposed):
    lines = []
    if proposed.unknown:
        lines += ["concepts chosen from outside the closed list, the model invented these:"]
        lines += ["  %-14s %-46s %s" % (item.paper, item.finding[:46], item.value)
                  for item in proposed.unknown]
    if proposed.misshapen:
        if lines:
            lines.append("")
        lines += ["concepts on the list but written in the wrong shape, "
                  "plain 'concept:slug' wanted:"]
        lines += ["  %-14s %-46s %s" % (item.paper, item.finding[:46], item.written)
                  for item in proposed.misshapen]
    return lines


def proposed_concepts(proposed):
    records = proposed.concepts.proposals
    if not records:
        return []
    lines = ["proposed concepts, none accepted automatically:"]
    for record in records:
        lines.append("  %-28s %d paper(s)" % (record["name"][:28], len(record["papers"])))
        if record["definitions"]:
            lines.append("      %s" % record["definitions"][0][:150])
    return lines


def refusals(proposed):
    if not proposed.concepts.refusals:
        return []
    lines = ["findings left untagged on purpose, closest concept and the reason:"]
    for refusal in proposed.concepts.refusals:
        lines.append("  %-52s %s" % (refusal.finding[:52], refusal.closest or "-"))
        if refusal.why:
            lines.append("      %s" % refusal.why[:150])
    return lines


def silent(proposed):
    if not proposed.concepts.silent:
        return []
    return ["findings with no concept and no answer at all, the block did not fire:"] + [
        "  %-14s %s" % (gap.paper, gap.finding[:60]) for gap in proposed.concepts.silent]


PROPOSAL_SECTIONS = (proposal_summary, families, undecided, concept_summary,
                     off_list, proposed_concepts, refusals, silent)


def for_proposals(proposed):
    return render([section(proposed) for section in PROPOSAL_SECTIONS])


def split_summary(result):
    return [
        "%s from %s, %s refused"
        % (plural(len(result.kept), "candidate finding"), plural(result.papers, "paper"),
           plural(len(result.refused), "finding")),
        "  source entries needed: %d of %d papers; %s yielded no finding"
        % (result.used, result.entries, plural(result.entries - result.used, "paper")),
        "  links dropped because nothing resolved: %d" % len(result.dropped),
    ]


def refused_findings(result):
    if not result.refused:
        return []
    return ["findings refused outright:"] + [
        "  %-13s %-46s %s" % (item.paper, item.finding[:46], item.why)
        for item in result.refused]


def dropped_links(result):
    if not result.dropped:
        return []
    tally = Counter(item.field for item in result.dropped)
    return ["links dropped, by field:"] + [
        "  %-14s %d" % (field, count) for field, count in tally.most_common()]


SPLIT_SECTIONS = (split_summary, refused_findings, dropped_links)


def for_split(result):
    return render([section(result) for section in SPLIT_SECTIONS])


def for_citations(tally, order, rejected_state, destination):
    total = sum(tally.values())
    lines = ["  %-10s %4d  %3.0f%%" % (state, tally[state], 100 * tally[state] / total)
             for state in order]
    return render([lines, ["%d entities, %d rejected, report in %s"
                           % (total, tally[rejected_state], destination)]])
