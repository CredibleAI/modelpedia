import sys

KEY_COLUMN = 40


def entry(key, note, width=KEY_COLUMN):
    return "  %-*s %s" % (width, key, note)


def plural(count, word):
    return "%d %s%s" % (count, word, "" if count == 1 else "s")


def line_buffered():
    """Every entry point calls this first. A run that takes hours is watched through
    `nohup ... > log` or a pipe, and Python buffers stdout into 8 KB blocks the moment it is not a
    terminal -- so the log stays empty while the run works and reads as dead.

    It lived in `harvest.py` from 2026-08-23 and was missed twice, in `ask.py` and then in
    `extract.py`, each time discovered by staring at an empty log. A rule that has to be
    remembered in four files is a rule that gets forgotten in three, so it lives here now and the
    entry points only call it."""
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(line_buffering=True)
