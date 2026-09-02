from pathlib import Path

import yaml

from modelpedia.build import database
from modelpedia.ingest import text
from modelpedia.ingest import verification


def main(argv):
    if len(argv) != 3:
        print("usage: python3 verify.py data/findings/ID.yaml source.pdf")
        return 2
    finding_path, source_path = map(Path, argv[1:])
    if not finding_path.is_file():
        print("ERROR no such finding: %s" % finding_path)
        return 2
    if not source_path.is_file():
        print("ERROR no such source: %s" % source_path)
        return 2
    try:
        finding = database.read_yaml(finding_path)
        entities = database.load_registries()
        document = text.document(source_path)
    except (OSError, ValueError, yaml.YAMLError, text.MissingTool) as error:
        print("ERROR %s" % error)
        return 1
    fid = finding.get("id") or finding_path.stem
    report = verification.run(finding, entities, document)
    print(verification.render(fid, report))
    return 1 if verification.blocking(report) or verification.nothing_verified(report) else 0

