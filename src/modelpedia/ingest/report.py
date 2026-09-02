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
        "  links dropped, unresolved or unlinkable: %d" % len(result.dropped),
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


class Comparison(NamedTuple):
    rows: tuple
    left_name: str
    right_name: str
    left: object
    right: object
    agreement: dict
    only_left: tuple
    only_right: tuple


def comparison_heading(result):
    return [
        "%s on %s answered by both"
        % ("%s (left) against %s (right)" % (result.left_name, result.right_name),
           plural(len(result.rows), "paper")),
        "  left only: %s; right only: %s"
        % (", ".join(result.only_left) or "none", ", ".join(result.only_right) or "none"),
    ]


def comparison_table(result):
    lines = ["%-13s %9s %11s %8s %s" % ("paper", "findings", "models", "shared", "models only on one side")]
    for row in result.rows:
        lines.append("%-13s %4d/%-4d %5d/%-5d %8d %s"
                     % (row.paper, row.left.findings, row.right.findings,
                        len(row.left.models), len(row.right.models), len(row.shared()),
                        ", ".join(sorted(row.only_left() | row.only_right()))[:44]))
    return lines


def comparison_checks(result):
    lines = ["%-26s %14s %14s" % ("", result.left_name[:14], result.right_name[:14])]
    lines.append("%-26s %14d %14d" % ("findings", result.left.findings, result.right.findings))
    for state in NUMBER_ORDER:
        lines.append("%-26s %14s %14s"
                     % ("key_metric numbers %s" % state,
                        ratio(result.left.numbers.get(state, 0), result.left.numeric()),
                        ratio(result.right.numbers.get(state, 0), result.right.numeric())))
    for state in CITATION_ORDER:
        lines.append("%-26s %14s %14s"
                     % ("citations %s" % state,
                        ratio(result.left.quotes.get(state, 0), result.left.cited()),
                        ratio(result.right.quotes.get(state, 0), result.right.cited())))
    for value, left, right in evidence_rows(result):
        lines.append("%-26s %14d %14d" % ("evidence %s" % value, left, right))
    return lines


NUMBER_ORDER = ("found", "review", "missing")
CITATION_ORDER = ("confirmed", "partial", "rejected", "absent")


def ratio(part, whole):
    return "-" if not whole else "%d/%d  %3.0f%%" % (part, whole, 100 * part / whole)


def evidence_rows(result):
    left, right = Counter(result.left.evidence), Counter(result.right.evidence)
    return [(value, left.get(value, 0), right.get(value, 0))
            for value in sorted(set(left) | set(right))]


def comparison_agreement(result):
    found = result.agreement
    return [
        "models named by both sides: %d of %d on the left (%.0f%%), of %d on the right (%.0f%%)"
        % (found["shared"], found["left"], 100 * found["of_left"],
           found["right"], 100 * found["of_right"]),
        "  papers where the two sides share no model at all: %d" % found["papers_without_overlap"],
        "  a shared model is agreement about what the paper is about, not about what it says;"
        " the claims themselves still need reading",
    ]


def comparison_unreadable(result):
    broken = [row.paper for row in result.rows if row.left.unreadable or row.right.unreadable]
    if not broken:
        return []
    return ["answers that would not parse: %s" % ", ".join(broken)]


COMPARISON_SECTIONS = (comparison_heading, comparison_table, comparison_checks,
                       comparison_agreement, comparison_unreadable)


def for_comparison(result):
    return render([section(result) for section in COMPARISON_SECTIONS])


def for_citations(tally, order, rejected_state, destination):
    total = sum(tally.values())
    lines = ["  %-10s %4d  %3.0f%%" % (state, tally[state], 100 * tally[state] / total)
             for state in order]
    return render([lines, ["%d entities, %d rejected, report in %s"
                           % (total, tally[rejected_state], destination)]])


def for_adoption(verdicts, unreadable, proposals):
    adopted = [v for v in verdicts if v.adopted()]
    refused = [v for v in verdicts if v.decision == "refuse"]
    troubled = [v for v in verdicts if v.problem]
    anchored = [v for v in adopted if v.anchor]
    lines = [
        "%s judged of %s put to the model"
        % (plural(len(verdicts), "answer"), plural(proposals, "proposal")),
        "  adopted        %d" % len(adopted),
        "  refused        %d, of which %d name an entry the registry already holds"
        % (len(refused), sum(1 for v in refused if v.alias_of)),
        "  anchors kept   %d of %d, the rest were not in any citing paper"
        % (len(anchored), len(adopted)),
    ]
    families = Counter(v.family for v in adopted if v.field == "models" and v.family)
    if families:
        lines.append("  models placed under a family: %d, new families: %d"
                     % (sum(count for key, count in families.items() if key != "new"),
                        families.get("new", 0)))
    blocks = [lines]
    if troubled:
        blocks.append(["answers the checks did not let through as written:"]
                      + ["  %-28s %s" % (v.name[:28], v.problem) for v in troubled])
    if unreadable:
        blocks.append(["answers that would not parse:"]
                      + ["  %-28s %s" % (name[:28], why) for name, why in unreadable])
    return render(blocks)


def for_tagging(agreed, none, invented, unreadable):
    lines = [
        "%s tagged, %d left with no concept" % (plural(agreed["findings"], "finding"), none),
        "  unchanged      %d" % agreed["unchanged"],
        "  concepts added %d, removed %d" % (agreed["added"], agreed["removed"]),
    ]
    blocks = [lines]
    if invented:
        blocks.append(["identifiers outside the closed list, not written:"]
                      + ["  %-12s %s" % pair for pair in invented[:20]])
    if unreadable:
        blocks.append(["answers that would not parse:"]
                      + ["  %-16s %s" % pair for pair in unreadable[:20]])
    return render(blocks)


def for_facets(taken, empty, refused, unreadable):
    spread = Counter(field for values in taken.values() for field in values)
    lines = ["%s described, %d answered with nothing on any axis"
             % (plural(len(taken), "model"), empty)]
    lines += ["  %-9s on %d models" % (field, count) for field, count in spread.most_common()]
    blocks = [lines]
    if refused:
        blocks.append(["values outside the closed list, not written:"]
                      + ["  %-26s %s: %s" % row for row in refused[:20]])
    if unreadable:
        blocks.append(["answers that would not parse:"]
                      + ["  %-26s %s" % pair for pair in unreadable[:20]])
    return render(blocks)
