from typing import NamedTuple

import yaml

from modelpedia import graph as graph_json
from modelpedia import paths
from modelpedia import record_keys as keys


class Database(NamedTuple):
    vocabularies: dict
    entities: dict
    findings: dict


class UniqueKeyLoader(yaml.SafeLoader):
    pass


def construct_unique_mapping(loader, node, deep=False):
    seen = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=deep)
        if key in seen:
            raise yaml.constructor.ConstructorError(
                "while reading a mapping", node.start_mark,
                "the key %r is declared twice; the second one would silently win" % (key,),
                key_node.start_mark)
        seen.add(key)
    return yaml.SafeLoader.construct_mapping(loader, node, deep=deep)


UniqueKeyLoader.add_constructor(yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG,
                                construct_unique_mapping)


def as_mapping(value, what):
    if value is None:
        return {}
    if not isinstance(value, dict):
        raise ValueError("%s is not a mapping" % what)
    return value


def read_yaml(path):
    with path.open(encoding="utf-8") as handle:
        document = yaml.load(handle, Loader=UniqueKeyLoader)
    return as_mapping(document, "%s: the top level" % path.name)


def add_entity(entities, key, entity):
    if key in entities:
        raise ValueError("registry: duplicated identifier %s" % key)
    entities[key] = entity


def load_registries():
    entities = {}
    for node_type in graph_json.NODE_TYPES:
        if not node_type.registry_file:
            continue
        registry = read_yaml(paths.REGISTRIES / node_type.registry_file)
        for key, entity in registry.items():
            entity = as_mapping(entity, "registry: %s" % key)
            add_entity(entities, key, dict(entity, type=node_type.name))
            variants = as_mapping(entity.get(keys.VARIANTS), "registry: variants on %s" % key)
            for variant_key, variant in variants.items():
                add_entity(entities, variant_key,
                           dict(as_mapping(variant, "registry: %s" % variant_key),
                                type=graph_json.VARIANT, parent=key))
    return entities


def load_findings():
    findings = {}
    for path in sorted(paths.FINDINGS.glob("*.yaml")):
        finding = read_yaml(path)
        fid = finding.get("id")
        if not isinstance(fid, str):
            raise ValueError("finding file %s has no string id" % path.name)
        if path.stem != fid:
            raise ValueError("finding file %s must contain id %s" % (path.name, path.stem))
        if fid in findings:
            raise ValueError("finding id %s is duplicated" % fid)
        findings[fid] = finding
    return findings


def load():
    return Database(
        vocabularies=read_yaml(paths.VOCABULARIES),
        entities=load_registries(),
        findings=load_findings(),
    )
