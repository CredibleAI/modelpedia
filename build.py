import yaml

from modelpedia.build import assemble
from modelpedia.build import database
from modelpedia import graph_io
from modelpedia import paths
from modelpedia.build import report as audit
from modelpedia.build import validate


def main():
    try:
        db = database.load()
    except (OSError, ValueError, yaml.YAMLError) as error:
        print("ERROR %s" % error)
        return 1
    errors = validate.errors(db)
    if errors:
        for error in errors:
            print("ERROR %s" % error)
        return 1
    graph = assemble.graph_from(db)
    graph_io.dump_graph(graph, paths.GRAPH)
    print(audit.render(db.findings, db.entities, graph))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
