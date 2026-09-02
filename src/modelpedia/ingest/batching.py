import collections
import time

DEAD_BATCHES = 3
GIVE_UP_AFTER = 10


def as_clock(seconds):
    whole = int(seconds)
    return "%d:%02d:%02d" % (whole // 3600, whole % 3600 // 60, whole % 60)


def fetch_each(rows, ask, delay, every=100, give_up_after=GIVE_UP_AFTER):
    answered, counts, running = set(), collections.Counter(), 0
    for number, row in enumerate(rows, start=1):
        try:
            counts[ask(row)] += 1
            answered.add(row["id"])
            running = 0
        except Exception as error:
            counts["failed"] += 1
            running += 1
            print("  FAILED %s: %s" % (row["id"], error))
            if running >= give_up_after:
                print("  %d refusals in a row after %d paper(s); giving the batch up here"
                      % (running, number - 1))
                break
            continue
        if number % every == 0:
            print("  %d/%d" % (number, len(rows)))
        time.sleep(delay)
    return answered, counts


def sleep_until_next_batch(pause, started, left):
    waited = time.monotonic() - started
    rest = max(0.0, pause - waited)
    print("  batch took %s, %d paper(s) left, next batch in %s"
          % (as_clock(waited), left, as_clock(rest)))
    time.sleep(rest)


def in_batches(outstanding, fetch, limit=None, pause=None):
    totals, barren = collections.Counter(), 0
    while outstanding:
        batch = outstanding[:limit] if limit else outstanding
        started = time.monotonic()
        answered, counts = fetch(batch)
        totals.update(counts)
        outstanding = [row for row in outstanding if row["id"] not in answered]

        barren = barren + 1 if not answered else 0
        if barren >= DEAD_BATCHES:
            print("  %d batches in a row answered for nothing; stopping rather than waiting\n"
                  "  out the clock on what looks like a refusal and not a quota" % barren)
            return totals, outstanding, True
        if not outstanding or not pause:
            return totals, outstanding, False
        sleep_until_next_batch(pause, started, len(outstanding))
    return totals, outstanding, False


def batch_plan(outstanding, limit, pause, what):
    if pause and limit:
        print("%d %s per batch, %s between batches measured from the start of each: %d batch(es)"
              % (limit, what, as_clock(pause), -(-len(outstanding) // limit)))


def fetched(outstanding, one, what, delay, limit, pause, kept, every=100):
    batch_plan(outstanding, limit, pause, what)
    try:
        return in_batches(outstanding, lambda batch: fetch_each(batch, one, delay, every=every),
                          limit, pause)
    except KeyboardInterrupt:
        print("\nstopped by hand; %s and the same command resumes" % kept)
        return None


def still_to_fetch(left):
    if left:
        print("%d paper(s) still to fetch; run the same command again" % len(left))
