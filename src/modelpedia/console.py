import sys

KEY_COLUMN = 40


def entry(key, note, width=KEY_COLUMN):
    return "  %-*s %s" % (width, key, note)


def plural(count, word):
    return "%d %s%s" % (count, word, "" if count == 1 else "s")


def line_buffered():
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(line_buffering=True)
