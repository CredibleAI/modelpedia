from typing import NamedTuple

from modelpedia import console

NOTE_COLUMN = 51


class Command(NamedTuple):
    name: str
    run: object
    usage: str = ""
    note: str = ""


def dispatch(argv, commands, usage):
    if len(argv) < 2:
        print(usage)
        return 2
    name, rest = argv[1], argv[2:]
    for command in commands:
        if command.name == name:
            return command.run(rest)
    print(usage)
    return 2


def runner(commands, usage):
    def main(argv):
        console.line_buffered()
        return dispatch(argv, commands, usage)
    return main


def usage_text(commands, prefix, header="", footer="", column=NOTE_COLUMN):
    """Generated from the same tuple `dispatch` walks, so a command cannot be documented and
    missing, or present and undocumented."""
    lines = ["usage: %s" % header if header else "usage:"]
    for command in commands:
        head = "  %s %s" % (prefix, command.usage or command.name)
        note = [line.strip() for line in command.note.splitlines() if line.strip()]
        if not note:
            lines.append(head)
            continue
        if len(head) < column:
            lines.append("%-*s%s" % (column, head, note[0]))
        else:
            lines += [head, " " * column + note[0]]
        lines += [" " * column + extra for extra in note[1:]]
    if footer:
        lines += ["", footer]
    return "\n".join(lines)


def fail(message):
    print("ERROR %s" % message)
    return 1


def options(rest, values, flags=()):
    """Flags before values: `harvest.py` had no flag support at all, so its caller filtered
    `--write` out of the list by hand before parsing, and a flag typed where a value was expected
    swallowed the next argument."""
    given, index = {}, 0
    while index < len(rest):
        flag = rest[index]
        if flag in flags:
            given[flag] = True
            index += 1
            continue
        if flag not in values:
            raise ValueError("unknown option %r; expected one of %s"
                             % (flag, ", ".join(tuple(values) + tuple(flags))))
        if index + 1 >= len(rest):
            raise ValueError("%s needs a value" % flag)
        given[flag] = rest[index + 1]
        index += 2
    return given


def positive(value, flag="--limit"):
    if value is None:
        return None
    if not str(value).isdigit() or int(value) < 1:
        raise ValueError("%s takes a positive whole number, not %r" % (flag, value))
    return int(value)


def number(value, flag, low=0.0, high=None):
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        raise ValueError("%s takes a number, not %r" % (flag, value))
    if parsed < low or (high is not None and parsed > high):
        raise ValueError("%s takes a number between %s and %s, not %r"
                         % (flag, low, "infinity" if high is None else high, value))
    return parsed


def one_of(value, allowed, flag):
    if value is None:
        return None
    if value not in allowed:
        raise ValueError("%s takes %s, not %r" % (flag, "/".join(allowed), value))
    return value


def comma_list(value, allowed, flag, default=None):
    if value is None:
        return default
    chosen = tuple(part.strip() for part in value.split(",") if part.strip())
    unknown = [item for item in chosen if item not in allowed]
    if not chosen or unknown:
        raise ValueError("%s takes %s, not %r" % (flag, "/".join(allowed), value))
    return chosen
