KEY_COLUMN = 40


def entry(key, note, width=KEY_COLUMN):
    return "  %-*s %s" % (width, key, note)


def plural(count, word):
    return "%d %s%s" % (count, word, "" if count == 1 else "s")
