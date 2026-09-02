import collections
import csv
import io

from modelpedia import paths
from modelpedia.ingest import manifest as store
from modelpedia.ingest import openreview as api
from modelpedia.ingest import screen

GROUP_COLUMNS = tuple(name for rules in screen.RULESETS for name in rules.groups)
RANK_COLUMNS = (("pos", "venue", "id", "title", "url", "tier", "total", "abstract", "review",
                 "reviews", "rating") + GROUP_COLUMNS + ("sampled", "findings"))


def screened(row, held):
    abstract = screen.screen(row.get("title"), row.get("abstract"), row.get("keywords") or [])
    return screen.combine(abstract, screen.review_screen(held.texts(row["id"])))


def sampled_ids():
    if not paths.PDF_SELECTION.exists():
        return set()
    return {line.strip() for line
            in paths.PDF_SELECTION.read_text(encoding="utf-8").splitlines()
            if line.strip() and not line.startswith("#")}


def ranked_rows(rows, held, yielded=None, sampled=None):
    yielded = {} if yielded is None else yielded
    sampled = sampled_ids() if sampled is None else sampled
    ranked = []
    for row in rows:
        total = screened(row, held)
        points = dict(total.subscores)
        ranked.append(dict(
            {name: points.get(name, 0.0) for name in GROUP_COLUMNS},
            venue=row["venue"], id=row["id"], title=row.get("title") or "",
            url=api.forum_url(row["id"]), tier=total.tier, total=total.score,
            abstract=screen.side_score(total.subscores, screen.ABSTRACT),
            review=screen.side_score(total.subscores, screen.REVIEW),
            reviews=held.count(row["id"]), rating=held.rating(row["id"]),
            sampled=int(row["id"] in sampled), findings=yielded.get(row["id"], 0)))
    ranked.sort(key=lambda entry: (-entry["total"], entry["id"]))
    for position, entry in enumerate(ranked, start=1):
        entry["pos"] = position
    return ranked


def as_csv(rows, columns):
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=list(columns))
    writer.writeheader()
    writer.writerows(rows)
    return buffer.getvalue()


def quantile(values, share):
    ordered = sorted(values)
    return ordered[min(len(ordered) - 1, int(share * len(ordered)))] if ordered else 0.0


def reviewed_share(entries):
    return sum(1 for entry in entries if entry["reviews"]) / len(entries) if entries else 0.0


def venue_row(label, entries):
    tiers = collections.Counter(entry["tier"] for entry in entries)
    totals = [entry["total"] for entry in entries]
    kept = tiers[screen.STRONG] + tiers[screen.POSSIBLE]
    return "%-30s %6d %8.1f%% %7.1f %6.1f %6.1f %8d %9d %6d %6.1f%%" % (
        label[:30], len(entries), 100.0 * reviewed_share(entries),
        quantile(totals, 0.5), quantile(totals, 0.75), quantile(totals, 0.90),
        tiers[screen.STRONG], tiers[screen.POSSIBLE], tiers[screen.WEAK],
        100.0 * kept / len(entries) if entries else 0.0)


def venue_target(target, venue_id):
    return target.with_name("%s-%s%s" % (target.stem, store.venue_slug(venue_id), target.suffix))


BAND_HEADER = ("%-30s %6s %9s %7s %6s %6s %8s %9s %6s %7s"
               % ("venue", "papers", "reviewed", "median", "p75", "p90", "strong", "possible",
                  "weak", "pass"))

TIER_HEADER = "%-30s %8s %9s %6s %7s %9s" % ("venue", "strong", "possible", "weak", "total",
                                             "reviewed")


def band_table(ranked):
    by_venue = {}
    for entry in ranked:
        by_venue.setdefault(entry["venue"], []).append(entry)
    lines = [BAND_HEADER]
    lines += [venue_row(venue, entries) for venue, entries in sorted(by_venue.items())]
    if len(by_venue) > 1:
        lines.append(venue_row("ALL", ranked))

    kept = sum(1 for entry in ranked if entry["tier"] != screen.WEAK)
    lines += ["", "thresholds strong >= %.1f, possible >= %.1f: %d of %d papers pass (%.1f%%)"
              % (screen.STRONG_AT, screen.POSSIBLE_AT, kept, len(ranked),
                 100.0 * kept / len(ranked) if ranked else 0.0)]

    for venue, entries in sorted(by_venue.items()):
        if reviewed_share(entries) >= 1.0:
            continue
        missing = sum(1 for entry in entries if not entry["reviews"])
        lines += ["", "  WARN %s is %.1f%% reviewed: %d paper(s) carry the abstract half only,"
                  " which" % (venue, 100.0 * reviewed_share(entries), missing),
                  "  lowers this venue's whole column. Do not compare it with a finished one until",
                  "  modelpedia harvest reviews %s has run out of papers to fetch." % venue]
    return "\n".join(lines)


def tier_table(rows, tiers_in_order):
    by_venue, reviewed = {}, {}
    for row in rows:
        by_venue.setdefault(row["venue"], {}).setdefault(row["tier"], 0)
        by_venue[row["venue"]][row["tier"]] += 1
        reviewed[row["venue"]] = reviewed.get(row["venue"], 0) + bool(row.get("reviews"))
    lines, totals = [TIER_HEADER], {tier: 0 for tier in tiers_in_order}
    for venue, tiers in sorted(by_venue.items()):
        for tier in tiers_in_order:
            totals[tier] += tiers.get(tier, 0)
        lines.append("%-30s %8d %9d %6d %7d %9d"
                     % (venue[:30], tiers.get(screen.STRONG, 0), tiers.get(screen.POSSIBLE, 0),
                        tiers.get(screen.WEAK, 0), sum(tiers.values()), reviewed[venue]))
    lines.append("%-30s %8d %9d %6d %7d %9d"
                 % ("ALL", totals[screen.STRONG], totals[screen.POSSIBLE], totals[screen.WEAK],
                    len(rows), sum(reviewed.values())))
    if sum(reviewed.values()) < len(rows):
        lines += ["  a row with no review is scored on its abstract alone, so its tier sits below",
                  "  every reviewed row by construction; modelpedia harvest reviews <venue_id>"
                  " closes that"]
    return "\n".join(lines)


def rules_in_use(by_rules, current, unknown_label):
    lines = ["screening rules in use now: %s" % current]
    for version, count in sorted(by_rules.items(), key=lambda pair: -pair[1]):
        mark = "" if version == current else \
               "  <- unfingerprinted" if version == unknown_label else "  <- stale"
        lines.append("  %-14s %d row(s)%s" % (version, count, mark))
    stale = sum(count for version, count in by_rules.items()
                if version not in (current, unknown_label))
    if stale:
        lines.append("  %d row(s) carry a tier computed by rules that are no longer the ones in "
                     "screen.py; their tier and signals were never recomputed" % stale)
    if by_rules.get(unknown_label):
        lines.append("  %d row(s) predate rules fingerprinting, so they carry no version to compare"
                     % by_rules[unknown_label])
    if stale or by_rules.get(unknown_label):
        lines.append("  modelpedia harvest rescreen recomputes every row from the metadata already"
                     " on disk")
    return "\n".join(lines)
