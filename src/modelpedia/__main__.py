import sys

from modelpedia import cli
from modelpedia.commands import ask, build, check, export, extract, harvest, render, verify


def plain(module):
    return lambda rest: module.main() or 0


def with_argv(module, name):
    return lambda rest: module.main([name] + rest)


COMMANDS = (
    cli.Command("build", plain(build),
                note="validate data/, write out/graph.json, print the audit"),
    cli.Command("render", plain(render),
                note="out/graph.json -> site/"),
    cli.Command("export", plain(export),
                note="out/graph.json -> out/csv/*.csv"),
    cli.Command("check", with_argv(check, "check"), "check <candidate.yaml>",
                "schema errors and link resolution for a candidate finding"),
    cli.Command("verify", with_argv(verify, "verify"), "verify <finding.yaml> <source.pdf>",
                "locate a record's evidence in its own source"),
    cli.Command("harvest", with_argv(harvest, "harvest"), "harvest <command> [...]",
                "OpenReview -> corpus/; run it bare for its own commands"),
    cli.Command("extract", with_argv(extract, "extract"), "extract <command> [...]",
                "corpus/ -> prompts, answers, data/findings/; bare for its own commands"),
    cli.Command("ask", with_argv(ask, "ask"), "ask <command> [...]",
                "corpus/prompts/ -> corpus/runs/; bare for its own commands"),
)

USAGE = cli.usage_text(
    COMMANDS, "modelpedia", column=44,
    footer="  the everyday loop is: modelpedia build && modelpedia render\n"
           "  harvest, extract and ask carry sub-commands; run them with no arguments to see them.")


dispatcher = cli.runner(COMMANDS, USAGE)


def main(argv=None):
    return dispatcher(sys.argv if argv is None else argv)


if __name__ == "__main__":
    raise SystemExit(main())
