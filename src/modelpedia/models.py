from dataclasses import dataclass

from modelpedia import record_keys as keys
from modelpedia import schema


@dataclass(frozen=True)
class Ref:
    ref: str
    role: str | None = None
    variant: str | None = None

    def to_dict(self):
        out = {keys.REF: self.ref}
        if self.role:
            out[keys.ROLE] = self.role
        if self.variant:
            out[keys.VARIANT] = self.variant
        return out

    @classmethod
    def from_dict(cls, raw):
        return cls(ref=raw[keys.REF], role=raw.get(keys.ROLE), variant=raw.get(keys.VARIANT))


@dataclass(frozen=True)
class Inline:
    name: str
    anchor: str
    role: str | None = None

    def to_dict(self):
        out = {keys.NAME: self.name, keys.ANCHOR: self.anchor}
        if self.role:
            out[keys.ROLE] = self.role
        return out

    @classmethod
    def from_dict(cls, raw):
        return cls(name=raw.get(keys.NAME), anchor=raw.get(keys.ANCHOR), role=raw.get(keys.ROLE))


def link_from(raw):
    return Ref.from_dict(raw) if keys.REF in raw else Inline.from_dict(raw)


FIELD_ORDER = ("id", "title", "description", "models", "concepts", "sources", "datasets",
               "methods", "related_work", "evidence_type", "key_metric", "caveat",
               "extracted_by", schema.RELATED_FINDINGS_FIELD)

LINK_LIST_FIELDS = ("models", "concepts", "sources", "datasets", "methods", "related_work")


@dataclass(frozen=True)
class Finding:
    """Written order is `FIELD_ORDER`, not the declaration order below; deriving it from the class
    instead would silently rewrite every record. `to_dict` keeps empty link lists, which are a
    stated gap, and drops empty optionals, which were never claimed."""

    id: str | None
    title: str
    description: str
    models: tuple
    concepts: tuple
    sources: tuple
    datasets: tuple
    methods: tuple
    related_work: tuple
    evidence_type: str | None
    extracted_by: str
    key_metric: str | None = None
    caveat: str | None = None
    related_findings: tuple = ()

    def to_dict(self):
        raw = {}
        for name in FIELD_ORDER:
            value = getattr(self, name)
            if name in LINK_LIST_FIELDS:
                raw[name] = [item.to_dict() for item in value]
                continue
            if isinstance(value, tuple):
                value = list(value)
            if not value:
                continue
            raw[name] = value
        return raw

    @classmethod
    def from_dict(cls, raw):
        links = {name: tuple(link_from(item) for item in raw.get(name) or ())
                 for name in LINK_LIST_FIELDS}
        return cls(id=raw.get("id"),
                   title=raw.get("title"),
                   description=raw.get("description"),
                   evidence_type=raw.get("evidence_type"),
                   extracted_by=raw.get("extracted_by"),
                   key_metric=raw.get("key_metric"),
                   caveat=raw.get("caveat"),
                   related_findings=tuple(raw.get(schema.RELATED_FINDINGS_FIELD) or ()),
                   **links)
