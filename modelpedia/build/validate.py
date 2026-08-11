from modelpedia import graph as graph_json
from modelpedia import record_keys as keys
from modelpedia import schema


def check_vocabularies(database):
    for scope, names in schema.VOCABULARY_SCOPES.items():
        terms_by_name = database.vocabularies.get(scope)
        if not isinstance(terms_by_name, dict):
            yield "vocabularies: %s is missing or is not a mapping" % scope
            continue
        for name in names:
            terms = terms_by_name.get(name)
            if not isinstance(terms, list) or not terms:
                yield "vocabularies: %s.%s is missing or is not a non-empty list" % (scope, name)
                continue
            for term in terms:
                if not isinstance(term, str) or not schema.SLUG.fullmatch(term):
                    yield "vocabularies: %s.%s has a term that is not kebab-case: %s" % (
                        scope, name, term)


def check_entity_keys(database):
    for key, entity in database.entities.items():
        if not isinstance(key, str):
            yield "registry: identifier %r is not a string" % (key,)
            continue
        prefix, _, slug = key.partition(":")
        if not slug or not schema.SLUG.fullmatch(slug):
            yield "registry: %s is not a kebab-case identifier" % key
        if prefix != entity["type"]:
            yield "registry: %s is a %s, so its identifier must start with %s:" % (
                key, entity["type"], entity["type"])


def check_entity_dates(database):
    for key, entity in database.entities.items():
        date = entity.get("date")
        if date is not None and not (isinstance(date, str) and schema.ISO_DATE.fullmatch(date)):
            yield "registry: %s has a date that is not a quoted ISO string" % key


def check_authors(database):
    for key, entity in database.entities.items():
        for author in entity.get(keys.AUTHORS) or []:
            if not isinstance(author, str) or not author.strip():
                yield "registry: %s has an author that is not a name" % key


def check_model_facets(database):
    facet_terms = database.vocabularies[graph_json.MODEL]
    for key, entity in database.entities.items():
        if entity["type"] != graph_json.MODEL:
            continue
        for facet in schema.MODEL_FACETS:
            values = entity.get(facet)
            if values is None:
                continue
            if not isinstance(values, list):
                yield "registry: %s has %s that is not a list" % (key, facet)
                continue
            for value in values:
                if value not in facet_terms[facet]:
                    yield "registry: %s has unknown %s %s" % (key, facet, value)


def reference_error(field, spec, ref, entities):
    if not isinstance(ref, str):
        return "%r is not a string reference" % (ref,)
    registry = ref.partition(":")[0]
    if spec.registry is not schema.ANY_REGISTRY and registry != spec.registry:
        return "%s does not belong in %s" % (ref, field)
    if registry not in graph_json.REGISTRY_TYPES:
        return "%s has no registry" % ref
    if ref not in entities:
        return "%s is not in any registry" % ref
    return None


def check_finding_fields(fid, finding, database):
    for field in schema.REQUIRED_FIELDS:
        if field not in finding or (not finding[field]
                                    and not (field in schema.EMPTY_ALLOWED
                                             and finding[field] == [])):
            yield "%s: missing %s" % (fid, field)

    for field in sorted(set(finding) - schema.KNOWN_FIELDS):
        yield "%s: %s is not a field in the schema" % (fid, field)

    for field in schema.CLOSED_FIELDS:
        value = finding.get(field)
        if value and value not in database.vocabularies[graph_json.FINDING][field]:
            yield "%s: unknown %s %s" % (fid, field, value)


def check_finding_links(fid, finding, database):
    for field, spec in schema.LINK_FIELDS.items():
        links = finding.get(field)
        if links is not None and not isinstance(links, list):
            yield "%s: %s is not a list" % (fid, field)
            continue
        for link in links or []:
            if not isinstance(link, dict):
                yield "%s: %s has an entry that is not a mapping" % (fid, field)
                continue
            inline = keys.REF not in link
            if inline and not spec.inline:
                yield "%s: %s has an entry that is not a reference" % (fid, field)
                continue
            allowed = {keys.NAME, keys.ANCHOR, keys.NOTE} if inline else {keys.REF}
            if field in schema.ROLE_FIELDS:
                allowed.add(keys.ROLE)
            if field == schema.MODELS_FIELD:
                allowed.add(keys.VARIANT)
            unknown_keys = sorted(set(link) - allowed)
            if unknown_keys:
                yield "%s: %s has unknown keys: %s" % (fid, field, ", ".join(unknown_keys))

            if inline:
                ref = link.get(keys.NAME)
                if not isinstance(ref, str) or not ref.strip():
                    yield "%s: %s has an entry with no name" % (fid, field)
                    continue
            else:
                ref = link[keys.REF]
                error = reference_error(field, spec, ref, database.entities)
                if error:
                    yield "%s: %s" % (fid, error)

            role = link.get(keys.ROLE)
            if role is not None:
                if not isinstance(role, str) or not role:
                    yield "%s: role on %s is not a non-empty string" % (fid, ref)
                elif role not in database.vocabularies[schema.ROLE_SCOPE].get(field, []):
                    yield "%s: unknown role %s on %s" % (fid, role, ref)

            variant = link.get(keys.VARIANT)
            if variant is not None and variant != graph_json.VARIANT_NOT_SPECIFIED:
                if not isinstance(variant, str) or not variant:
                    yield "%s: variant on %s is not a non-empty string" % (fid, ref)
                elif variant not in database.entities:
                    yield "%s: %s is not a known variant" % (fid, variant)
                elif database.entities[variant]["type"] != graph_json.VARIANT:
                    yield "%s: %s is not a variant" % (fid, variant)
                elif field == schema.MODELS_FIELD \
                        and database.entities[variant]["parent"] != ref:
                    yield "%s: %s does not belong to %s" % (fid, variant, ref)


def check_related_findings(fid, finding, database):
    related_findings = finding.get(schema.RELATED_FINDINGS_FIELD)
    if related_findings is not None and not isinstance(related_findings, list):
        yield "%s: %s is not a list" % (fid, schema.RELATED_FINDINGS_FIELD)
        return
    for related in related_findings or []:
        if not isinstance(related, str):
            yield "%s: related finding %r is not a string id" % (fid, related)
            continue
        if related not in database.findings:
            yield "%s: %s is not a known finding" % (fid, related)
            continue
        back = database.findings[related].get(schema.RELATED_FINDINGS_FIELD)
        if not isinstance(back, list) or fid not in back:
            yield "%s: %s is listed but does not link back" % (fid, related)


REGISTRY_CHECKS = (check_entity_keys, check_entity_dates, check_authors, check_model_facets)

FINDING_CHECKS = (check_finding_fields, check_finding_links, check_related_findings)


def errors(database):
    vocabulary_errors = list(check_vocabularies(database))
    if vocabulary_errors:
        return vocabulary_errors

    found = [error for check in REGISTRY_CHECKS for error in check(database)]
    for fid, finding in database.findings.items():
        found += [error for check in FINDING_CHECKS for error in check(fid, finding, database)]
    return found
